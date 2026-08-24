"""The discover stage: niche + area -> ranked shortlist.

Input:  niche keyword(s), centre point (town), radius, max results.
Output: shortlist.csv ranked worst-web-presence-first, plus one
        business.json per row.

This is the orchestration only — Places, site checks, Companies House and
scoring each live in their own module and are injected here, so the whole
stage can be exercised offline against a fixture.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional

from .addressee import decide as decide_addressee
from .places import normalise_place
from .scoring import Weights, score_business
from .site_checks import classify_url
from .storage import Batch, business_to_row, slugify, utcnow


@dataclass
class DiscoverResult:
    rows: list[dict]
    businesses: list[dict]
    excluded: list[dict]


def discover(
    *,
    niche: str,
    area: str,
    places_client,
    site_checker=None,
    companies_house=None,
    site_contacts=None,
    weights: Optional[Weights] = None,
    radius_m: int = 8000,
    top: int = 25,
    search_limit: Optional[int] = None,
    progress: Optional[Callable[[str], None]] = None,
    seen_place_ids: Optional[set[str]] = None,
) -> DiscoverResult:
    """Search, enrich, score and rank candidates for one niche + area.

    ``search_limit`` caps how many search hits get full detail lookups;
    defaults to 3x ``top`` so exclusions (chains, closed, too new) don't
    starve the final list.
    """
    weights = weights or Weights.default()
    say = progress or (lambda _msg: None)
    seen = seen_place_ids if seen_place_ids is not None else set()
    limit = search_limit or max(top * 3, 30)

    say(f"Searching Places for '{niche}' in {area} (radius {radius_m}m)…")
    stubs = places_client.search(niche, area, radius_m=radius_m, max_results=limit)
    say(f"  {len(stubs)} candidates returned")

    businesses: list[dict] = []
    excluded: list[dict] = []

    for stub in stubs:
        place_id = stub.get("id")
        if not place_id or place_id in seen:
            continue
        seen.add(place_id)

        # Screen on what the search already told us, before spending a
        # Place Details call. Details bill at the Enterprise tier (reviews,
        # rating, photos), so fetching a chain or a closed business and then
        # binning it is the most expensive thing this stage can do.
        stub_name = (stub.get("displayName") or {}).get("text") or ""
        stub_probe = {
            "name": stub_name,
            "business_status": stub.get("businessStatus"),
        }
        stub_verdict = score_business(stub_probe, weights)
        if stub_verdict.excluded:
            excluded.append(
                {
                    "place_id": place_id,
                    "name": stub_name,
                    "excluded": stub_verdict.exclude_reason,
                    "screened_before_details": True,
                }
            )
            say(f"  excluded ({stub_verdict.exclude_reason}, no details call): "
                f"{stub_name}")
            continue

        detail = places_client.details(place_id)
        if not detail:
            continue
        business = normalise_place(detail)
        business["niche"] = niche
        business["search_area"] = area
        business["created"] = utcnow()
        business["preview_slug"] = slugify(business.get("name") or place_id)
        business["owner"] = {"name": None, "source": None, "confidence": "none"}
        business["company"] = {"number": None, "type": "unknown"}
        business["site_contact"] = None
        business["site_emails"] = []
        business["site_score"] = None
        business["lead_score"] = 0

        # Cheap exclusions first — don't spend a PageSpeed call on a chain
        # or a closed business.
        pre = score_business(business, weights)
        if pre.excluded:
            business["excluded"] = pre.exclude_reason
            excluded.append(business)
            say(f"  excluded ({pre.exclude_reason}): {business.get('name')}")
            continue

        # Baseline verdict from the URL alone — pure, no network, so it holds
        # even with --no-site-checks. The checker then refines it for real sites.
        kind = classify_url(business.get("website"))
        business["site_score"] = {
            "verdict": {"none": "none", "social": "social_only",
                        "directory": "social_only"}.get(kind, "unknown"),
            "mobile_score": None,
            "https": None,
            "viewport": None,
        }
        if site_checker is not None:
            check = site_checker.check(business.get("website"))
            business["site_score"] = {
                "verdict": check.verdict,
                "mobile_score": check.mobile_score,
                "https": check.https,
                "viewport": check.viewport,
            }

        if companies_house is not None:
            found = companies_house.lookup(
                business.get("name") or "",
                (business.get("address") or {}).get("postcode"),
            )
            business["owner"] = found["owner"]
            business["company"] = found["company"]

        # Spec section 5, step 2: the business's own About page. Companies
        # House says who owns it; the website says who runs it.
        if site_contacts is not None and business.get("website"):
            site_found = site_contacts.find(business["website"]) or {}
            business["site_contact"] = site_found.get("contact")
            business["site_emails"] = site_found.get("emails", [])

        business["addressee"] = decide_addressee(
            business.get("owner"),
            business.get("site_contact"),
            company_type=(business.get("company") or {}).get("type", "unknown"),
        )

        result = score_business(business, weights)
        if result.excluded:
            business["excluded"] = result.exclude_reason
            excluded.append(business)
            continue
        business["lead_score"] = result.score
        business["score_breakdown"] = result.breakdown
        businesses.append(business)
        say(f"  scored {result.score:>4}  {business.get('name')}")

    # Worst web presence first; ties broken by review count so the more
    # established of two equally weak sites is the better prospect.
    businesses.sort(
        key=lambda b: (-b["lead_score"], -(b.get("review_count") or 0), b.get("name") or "")
    )
    top_businesses = businesses[:top]
    rows = [business_to_row(b) for b in top_businesses]
    return DiscoverResult(rows=rows, businesses=top_businesses, excluded=excluded)


def merge_results(results: Iterable[DiscoverResult]) -> DiscoverResult:
    """Merge several niche/town runs into one de-duplicated, ranked set."""
    by_id: dict[str, dict] = {}
    excluded: list[dict] = []
    for result in results:
        for business in result.businesses:
            pid = business.get("place_id")
            # A place found by two niches keeps its higher score.
            if pid not in by_id or business["lead_score"] > by_id[pid]["lead_score"]:
                by_id[pid] = business
        excluded.extend(result.excluded)
    merged = sorted(
        by_id.values(),
        key=lambda b: (-b["lead_score"], -(b.get("review_count") or 0), b.get("name") or ""),
    )
    return DiscoverResult(
        rows=[business_to_row(b) for b in merged], businesses=merged, excluded=excluded
    )


def write_batch(batch: Batch, result: DiscoverResult) -> None:
    """Persist a discover result into a batch folder."""
    for business in result.businesses:
        batch.write_business(business)
    batch.write_shortlist(result.rows)
    meta = batch.read_meta()
    meta["status_counts"] = {
        "shortlisted": len(result.businesses),
        "excluded": len(result.excluded),
    }
    meta["discovered"] = utcnow()
    batch.write_meta(meta)
