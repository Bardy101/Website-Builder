"""Google Places API (New) client — search + details, cached to disk.

Legitimate API use, no scraping (spec section 4). Two calls matter:

  places:searchText   -> candidate place IDs for "<niche> in <town>"
  places/<id>         -> the detail fields the shortlist and letter need

The HTTP layer is injectable so tests can run without a key or a network.
Detail responses are cached by place ID for 30 days; search responses by
the query + radius, so widening a radius does not re-pay for known places.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
DETAILS_URL = "https://places.googleapis.com/v1/places/{place_id}"

# Only ask for the fields we use — field masks are what the API bills on.
SEARCH_FIELDS = "places.id,places.displayName,places.formattedAddress,nextPageToken"
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


@dataclass
class PlacesClient:
    api_key: Optional[str] = None
    cache: Any = None
    post: Optional[Callable[..., Any]] = None
    get: Optional[Callable[..., Any]] = None
    timeout: float = 30.0

    def _require_key(self) -> str:
        if not self.api_key:
            raise PlacesError(
                "GOOGLE_PLACES_API_KEY is not set. Copy .env.example to .env "
                "and add your key, or run with --fixture for offline testing."
            )
        return self.api_key

    def _do_post(self, url: str, *, json_body: dict, headers: dict) -> dict:
        if self.post is not None:
            return self.post(url, json_body=json_body, headers=headers)
        import requests

        resp = requests.post(url, json=json_body, headers=headers, timeout=self.timeout)
        if resp.status_code >= 400:
            raise PlacesError(f"Places search failed [{resp.status_code}]: {resp.text[:400]}")
        return resp.json()

    def _do_get(self, url: str, *, headers: dict) -> dict:
        if self.get is not None:
            return self.get(url, headers=headers)
        import requests

        resp = requests.get(url, headers=headers, timeout=self.timeout)
        if resp.status_code >= 400:
            raise PlacesError(f"Place details failed [{resp.status_code}]: {resp.text[:400]}")
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
        cache_key = f"{query}|r={radius_m}|n={max_results}"

        def fetch() -> list[dict]:
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
                if page_token:
                    body["pageToken"] = page_token
                data = self._do_post(SEARCH_URL, json_body=body, headers=headers)
                results.extend(data.get("places", []) or [])
                page_token = data.get("nextPageToken")
                if not page_token:
                    break
            return results[:max_results]

        if self.cache is None:
            return fetch()
        return self.cache.get_or_fetch("places_search", cache_key, fetch)

    def details(self, place_id: str) -> dict:
        """Full Place Details for one place ID, cached for the TTL."""

        def fetch() -> dict:
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
