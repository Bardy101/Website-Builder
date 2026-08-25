"""Google Places API (New) client — search + details, cached to disk.

Legitimate API use, no scraping (spec section 4). Two calls matter:

  places:searchText   -> candidate place IDs for "<niche> in <town>"
  places/<id>         -> the detail fields the shortlist and letter need

The HTTP layer is injectable so tests can run without a key or a network.
Detail responses are cached by place ID for 30 days; search responses by
the query + radius, so widening a radius does not re-pay for known places.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
DETAILS_URL = "https://places.googleapis.com/v1/places/{place_id}"

# Only ask for the fields we use — field masks are what the API bills on.
# Field masks decide which billing SKU a call lands in, and the tiers have
# very different free monthly allowances (Essentials 10k, Pro 5k, Enterprise
# 1k per SKU). Asking for businessStatus here moves Text Search up a tier,
# but it is one call per search and it saves an Enterprise-tier Place Details
# call for every closed business and chain we would otherwise fetch and bin.
# Locating a town: one cheap Essentials call, cached — towns don't move.
AREA_FIELDS = "places.id,places.location,places.displayName"
SEARCH_FIELDS = (
    "places.id,places.displayName,places.formattedAddress,"
    "places.businessStatus,nextPageToken"
)
# These include reviews, rating and photos, which are Enterprise-tier fields.
# Every Place Details call therefore bills at Enterprise (1,000 free/month).
# The spec needs them: verbatim review quotes for the letter copy, and
# recent_review_date as the "still trading properly" signal.
DETAIL_FIELDS = ",".join(
    [
        "id",
        "displayName",
        "formattedAddress",
        "addressComponents",
        "location",
        "nationalPhoneNumber",
        "internationalPhoneNumber",
        "websiteUri",
        "googleMapsUri",
        "rating",
        "userRatingCount",
        "businessStatus",
        "primaryType",
        "types",
        "regularOpeningHours",
        "photos",
        "reviews",
    ]
)


class PlacesError(RuntimeError):
    pass


def _explain(status: int, body: str, what: str) -> str:
    """Turn an API error into something that names the likely fix.

    A key restricted to websites is the common trap: it looks like the
    cautious choice in the console, but blocks server-side calls entirely.
    """
    from .keycheck import diagnose_google

    detail, hint = diagnose_google(status, body)
    message = f"{what} failed [{status}]: {detail}"
    if hint:
        message += "\n\n" + hint
    message += "\n\nRun 'Test API keys' on the menu to check the key on its own."
    return message


def _new_stats() -> dict:
    return {
        "search_requested": 0,
        "search_fetched": 0,
        "area_fetched": 0,
        "details_requested": 0,
        "details_fetched": 0,
    }


@dataclass
class PlacesClient:
    api_key: Optional[str] = None
    cache: Any = None
    post: Optional[Callable[..., Any]] = None
    get: Optional[Callable[..., Any]] = None
    timeout: float = 30.0
    # Billable calls actually made, vs served from the 30-day cache.
    stats: dict = field(default_factory=_new_stats)

    def _require_key(self) -> str:
        if not self.api_key:
            raise PlacesError(
                "GOOGLE_PLACES_API_KEY is not set.\n\n"
                "Add one with 'Set up API keys' on the run.bat menu, or run\n"
                "with --fixture to use the offline demo data."
            )
        return self.api_key

    def _do_post(self, url: str, *, json_body: dict, headers: dict) -> dict:
        if self.post is not None:
            return self.post(url, json_body=json_body, headers=headers)
        import requests

        resp = requests.post(url, json=json_body, headers=headers, timeout=self.timeout)
        if resp.status_code >= 400:
            raise PlacesError(_explain(resp.status_code, resp.text, "Places search"))
        return resp.json()

    def _do_get(self, url: str, *, headers: dict) -> dict:
        if self.get is not None:
            return self.get(url, headers=headers)
        import requests

        resp = requests.get(url, headers=headers, timeout=self.timeout)
        if resp.status_code >= 400:
            raise PlacesError(_explain(resp.status_code, resp.text, "Place details"))
        return resp.json()

    # -- public API ---------------------------------------------------------

    def search(
        self,
        niche: str,
        area: str,
        *,
        radius_m: int = 8000,
        max_results: int = 60,
        region: str = "gb",
    ) -> list[dict]:
        """Text-search for '<niche> in <area>', following pagination.

        Returns a list of raw place stubs (id + displayName + address).
        """
        query = f"{niche} in {area}"
        # max_results is deliberately NOT in the key: a cached response is a
        # superset or an exhausted search, either of which can serve a
        # smaller request. Keying on it meant `--top 30` after `--top 25`
        # bought a fresh billable search for the same query and radius.
        cache_key = f"{query}|r={radius_m}"

        self.stats["search_requested"] += 1
        centre = self.area_center(area) if radius_m else None

        if self.cache is not None:
            cached = self.cache.get("places_search", cache_key)
            if isinstance(cached, dict):
                places = cached.get("places") or []
                requested = cached.get("requested", 0)
                # Reuse when we asked for at least this much before, or the
                # search ran dry before hitting its cap (fetching again with
                # a bigger cap cannot find more).
                if max_results <= requested or len(places) < requested:
                    return places[:max_results]

        def fetch() -> list[dict]:
            self.stats["search_fetched"] += 1
            key = self._require_key()
            headers = {
                "Content-Type": "application/json",
                "X-Goog-Api-Key": key,
                "X-Goog-FieldMask": SEARCH_FIELDS,
            }
            results: list[dict] = []
            page_token: Optional[str] = None
            while len(results) < max_results:
                body: dict[str, Any] = {
                    "textQuery": query,
                    "regionCode": region,
                    "maxResultCount": min(20, max_results - len(results)),
                }
                # Without this the radius argument does nothing at all: it
                # would only ever have changed the cache key, so widening it
                # bought a fresh billable search returning identical results.
                if centre:
                    body["locationBias"] = {
                        "circle": {"center": centre, "radius": float(radius_m)}
                    }
                if page_token:
                    body["pageToken"] = page_token
                data = self._do_post(SEARCH_URL, json_body=body, headers=headers)
                results.extend(data.get("places", []) or [])
                page_token = data.get("nextPageToken")
                if not page_token:
                    break
            return results[:max_results]

        results = fetch()
        if self.cache is not None:
            self.cache.set(
                "places_search",
                cache_key,
                {
                    "requested": max_results,
                    "places": results,
                    # Stored so the menu can list past searches exactly,
                    # rather than parsing them back out of the key.
                    "niche": niche,
                    "area": area,
                    "radius_m": radius_m,
                },
            )
        return results

    def area_center(self, area: str) -> Optional[dict]:
        """Coordinates for a town, so a radius can be applied to the search.

        Text Search alone scopes by the words in the query; the radius has to
        be expressed as a locationBias circle, which needs a centre point.
        One cached Essentials-tier call per town, reused for every niche.
        """

        def fetch() -> Optional[dict]:
            if not self.api_key:
                return None
            self.stats["area_fetched"] += 1
            headers = {
                "Content-Type": "application/json",
                "X-Goog-Api-Key": self.api_key,
                "X-Goog-FieldMask": AREA_FIELDS,
            }
            try:
                data = self._do_post(
                    SEARCH_URL,
                    json_body={"textQuery": area, "maxResultCount": 1},
                    headers=headers,
                )
            except PlacesError:
                return None
            places = data.get("places") or []
            if not places:
                return None
            location = places[0].get("location") or {}
            if "latitude" not in location or "longitude" not in location:
                return None
            return {
                "latitude": location["latitude"],
                "longitude": location["longitude"],
            }

        if self.cache is None:
            return fetch()
        return self.cache.get_or_fetch("area_center", area, fetch)

    def details(self, place_id: str) -> dict:
        """Full Place Details for one place ID, cached for the TTL."""

        self.stats["details_requested"] += 1

        def fetch() -> dict:
            self.stats["details_fetched"] += 1
            key = self._require_key()
            headers = {"X-Goog-Api-Key": key, "X-Goog-FieldMask": DETAIL_FIELDS}
            return self._do_get(DETAILS_URL.format(place_id=place_id), headers=headers)

        if self.cache is None:
            return fetch()
        return self.cache.get_or_fetch("places_details", place_id, fetch)


# -- normalisation ---------------------------------------------------------


def _address_component(components: list[dict], *types: str) -> Optional[str]:
    for comp in components or []:
        comp_types = comp.get("types", [])
        if any(t in comp_types for t in types):
            return comp.get("longText") or comp.get("shortText")
    return None


def normalise_place(detail: dict) -> dict:
    """Map a Places API (New) detail response onto our flat business record.

    Unknown fields stay None — never guessed (spec section 3).
    """
    components = detail.get("addressComponents", []) or []
    name = (detail.get("displayName") or {}).get("text")

    street_number = _address_component(components, "street_number")
    route = _address_component(components, "route")
    line1 = " ".join(x for x in (street_number, route) if x) or None
    town = _address_component(components, "postal_town", "locality") or None
    postcode = _address_component(components, "postal_code")

    hours = (detail.get("regularOpeningHours") or {}).get("weekdayDescriptions") or []
    photos = [p.get("name") for p in (detail.get("photos") or []) if p.get("name")]

    reviews = []
    for r in (detail.get("reviews") or [])[:5]:
        reviews.append(
            {
                "text": ((r.get("originalText") or r.get("text") or {}) or {}).get("text"),
                "rating": r.get("rating"),
                "time": r.get("publishTime"),
                "author": (r.get("authorAttribution") or {}).get("displayName"),
            }
        )

    return {
        "place_id": detail.get("id"),
        "name": name,
        "address": {
            "line1": line1,
            "town": town,
            "postcode": postcode,
            "formatted": detail.get("formattedAddress"),
        },
        "phone": detail.get("nationalPhoneNumber") or detail.get("internationalPhoneNumber"),
        "website": detail.get("websiteUri"),
        "maps_url": detail.get("googleMapsUri"),
        "rating": detail.get("rating"),
        "review_count": detail.get("userRatingCount"),
        "business_status": detail.get("businessStatus"),
        "primary_type": detail.get("primaryType"),
        "types": detail.get("types") or [],
        "opening_hours": hours,
        "photos": photos,
        "photo_count": len(photos),
        "reviews": reviews,
    }


def most_recent_review_date(business: dict) -> Optional[str]:
    """Date (YYYY-MM-DD) of the newest review we have, or None.

    The single best 'still trading properly' signal (spec section 2).
    """
    times = [r.get("time") for r in business.get("reviews", []) if r.get("time")]
    if not times:
        return None
    return max(times)[:10]
