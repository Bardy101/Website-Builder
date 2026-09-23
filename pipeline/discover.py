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
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from .addressee import decide as decide_addressee
from .places import normalise_place
from .scoring import Weights, score_business, sort_key, staleness_points
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
    screenshots=None,
    weights: Optional[Weights] = None,
    radius_m: int = 8000,
    top: int = 25,
    search_limit: Optional[int] = None,
    progress: Optional[Callable[[str], None]] = None,
    seen_place_ids: Optional[set[str]] = None,
    workers: int = 8,
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
    pending: list[dict] = []

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
            business["excluded_detail"] = pre.exclude_detail
            excluded.append(business)
            why = pre.exclude_reason + (
                f", {pre.exclude_detail}" if pre.exclude_detail else "")
            say(f"  excluded ({why}): {business.get('name')}")
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
        business["staleness"] = {}
        business["staleness_points"] = 0
        business["screenshot_path"] = ""
        pending.append(business)

    # Enrichment is the slow part — PageSpeed alone runs 20-30s per site and
    # the contact lookup fetches several pages. Every business's lookups are
    # independent of every other's, so they run concurrently; caches write
    # distinct files per key, which keeps this safe.
    _enrich_all(
        pending,
        site_checker=site_checker,
        companies_house=companies_house,
        site_contacts=site_contacts,
        workers=workers,
        say=say,
    )

    # Screenshots come after enrichment so only surviving candidates are
    # captured. Cached by place_id, so a re-run captures nothing new.
    if screenshots is not None and pending:
        shots = screenshots.capture_all(
            [(b["place_id"], b.get("website") or "") for b in pending]
        )
        for business in pending:
            shot = shots.get(business["place_id"])
            if shot and shot.ok and shot.mobile:
                business["screenshot_path"] = str(shot.mobile)
                business["screenshot_desktop"] = str(shot.desktop)
            else:
                # No file, empty path — the reason is in the capturer's log.
                business["screenshot_path"] = ""
        say(f"  screenshots: {sum(1 for s in shots.values() if s.ok)} of "
            f"{len(shots)} captured")
        rechecked = recheck_from_browser(
            pending, screenshots,
            stale_years=weights.thresholds.get("copyright_stale_years", 3))
        if rechecked:
            say(f"  {rechecked} site(s) that blocked the direct check were measured "
                "from the browser instead")

    for business in pending:
        business["addressee"] = decide_addressee(
            business.get("owner"),
            business.get("site_contact"),
            company_type=(business.get("company") or {}).get("type", "unknown"),
        )

        result = score_business(business, weights)
        if result.excluded:
            business["excluded"] = result.exclude_reason
            business["excluded_detail"] = result.exclude_detail
            excluded.append(business)
            why = result.exclude_reason + (
                f", {result.exclude_detail}" if result.exclude_detail else "")
            say(f"  excluded ({why}): {business.get('name')}")
            continue
        business["lead_score"] = result.score
        business["score_breakdown"] = result.breakdown
        business["staleness_points"] = staleness_points(business, weights)
        businesses.append(business)
        say(f"  scored {result.score:>4}  {business.get('name')}")

    # Worst web presence first. Mobile score breaks ties only — see
    # scoring.sort_key for why it must never rank on its own.
    businesses.sort(key=sort_key)
    top_businesses = businesses[:top]
    rows = [business_to_row(b) for b in top_businesses]
    return DiscoverResult(rows=rows, businesses=top_businesses, excluded=excluded)


def recheck_from_browser(businesses: list[dict], capturer, *, stale_years: int = 3) -> int:
    """Measure sites the direct check couldn't, from the page the browser saw.

    A site that answers a plain HTTP fetch with 403, a bot challenge or a
    timeout came out "unknown" — and, failing open, stayed on the list
    unjudged, good-looking sites included. The screenshot step has already
    loaded each of those pages in a real browser, which sites don't block
    that way, and saved the rendered HTML. Running the same staleness checks
    on it turns "unknown" into a real verdict.

    Media queries come from asking the browser, which has loaded every
    stylesheet; where it couldn't read one (cross-origin) the HTML check
    stands, and that fails open as before. A site the browser couldn't load
    either stays unknown. Returns how many were measured this way.
    """
    from .screenshots import read_page
    from .site_checks import SiteCheck, classify_url, verdict_from
    from .staleness import analyse_staleness

    measured = 0
    for business in businesses:
        website = business.get("website")
        if not website or classify_url(website) != "real":
            continue
        if (business.get("staleness") or {}).get("fetch_ok"):
            continue
        page = read_page(capturer.page_path_for(business["place_id"]))
        if page is None:
            continue
        # No network here: the page is already in hand, and linked CSS the
        # direct fetch couldn't reach it won't reach now either.
        analysis = analyse_staleness(
            website, prefetched=(page.get("url") or website, page["html"]),
            fetch=lambda _url, _timeout: None)
        if not analysis.get("fetch_ok"):
            continue
        if page.get("media_queries") is not None:
            analysis["has_media_queries"] = bool(page["media_queries"])
        analysis["source"] = "browser"
        check = SiteCheck(True, False, https=analysis.get("has_https"),
                          viewport=analysis.get("has_viewport"), staleness=analysis)
        site = business.get("site_score") or {}
        business["staleness"] = analysis
        business["site_score"] = {
            **site,
            "verdict": verdict_from(check, stale_years=stale_years),
            "https": analysis.get("has_https"),
            "viewport": analysis.get("has_viewport"),
        }
        measured += 1
    return measured


def _enrich_one(business, site_checker, companies_house, site_contacts) -> None:
    """All slow lookups for one business. Runs on a worker thread.

    The three lookups are independent, so each gets its own chance to fail.
    They used to share one try: a site whose HTML broke the parser also
    cost the business its Companies House owner and its website contact,
    and the letter fell back to "FAO the Owner" for no reason connected to
    who owns it. A failure is recorded against its step and the rest carry
    on; the baseline values set before enrichment stay in place, so a
    failed step never scores anything (fail open).
    """
    errors: dict[str, str] = {}

    if site_checker is not None:
        try:
            check = site_checker.check(business.get("website"))
            business["site_score"] = {
                "verdict": check.verdict,
                "mobile_score": check.mobile_score,
                "https": check.https,
                "viewport": check.viewport,
            }
            business["staleness"] = check.staleness or {}
        except Exception as exc:  # noqa: BLE001
            errors["site check"] = _describe(exc)

    if companies_house is not None:
        try:
            found = companies_house.lookup(
                business.get("name") or "",
                (business.get("address") or {}).get("postcode"),
            )
            business["owner"] = found["owner"]
            business["company"] = found["company"]
        except Exception as exc:  # noqa: BLE001
            errors["owner lookup"] = _describe(exc)

    # Spec section 5, step 2: the business's own About page. Companies
    # House says who owns it; the website says who runs it.
    if site_contacts is not None and business.get("website"):
        try:
            site_found = site_contacts.find(business["website"]) or {}
            business["site_contact"] = site_found.get("contact")
            business["site_emails"] = site_found.get("emails", [])
        except Exception as exc:  # noqa: BLE001
            errors["website contact"] = _describe(exc)

    if errors:
        business["enrich_errors"] = errors
    else:
        business.pop("enrich_errors", None)


def _describe(exc: Exception) -> str:
    """One line a human can act on: the type and the first line of the text."""
    text = str(exc).strip().splitlines()[0] if str(exc).strip() else ""
    return f"{type(exc).__name__}: {text}"[:200] if text else type(exc).__name__


def _enrich_all(
    pending: list[dict],
    *,
    site_checker,
    companies_house,
    site_contacts,
    workers: int,
    say: Callable[[str], None],
) -> None:
    """Run _enrich_one across all businesses, concurrently when it helps.

    A failure in one business's lookups leaves that business unenriched
    (fields stay null) rather than killing the whole run.
    """
    if not pending:
        return
    if site_checker is None and companies_house is None and site_contacts is None:
        return

    def safely(business: dict) -> None:
        # _enrich_one isolates its own steps; this outer guard is for a bug
        # in that bookkeeping itself, so one business still cannot end a run.
        try:
            _enrich_one(business, site_checker, companies_house, site_contacts)
        except Exception as exc:  # noqa: BLE001
            business["enrich_errors"] = {"lookups": _describe(exc)}

    if workers <= 1 or len(pending) == 1:
        for business in pending:
            safely(business)
            say(f"  checked      {business.get('name')}")
        return

    from concurrent.futures import ThreadPoolExecutor, as_completed

    with ThreadPoolExecutor(max_workers=min(workers, len(pending))) as pool:
        futures = {pool.submit(safely, b): b for b in pending}
        for future in as_completed(futures):
            say(f"  checked      {futures[future].get('name')}")


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
    merged = sorted(by_id.values(), key=sort_key)
    return DiscoverResult(
        rows=[business_to_row(b) for b in merged], businesses=merged, excluded=excluded
    )


EXCLUDED_COLUMNS = [
    "name", "reason", "detail", "last_review", "review_count",
    "site_verdict", "website", "town", "place_id",
]


def excluded_to_row(business: dict) -> dict:
    """One line per excluded business: enough to see why, no more.

    Search stubs screened before details carry only a name and a reason;
    fully fetched records carry the lot. Blank means unknown, as elsewhere.
    """
    from .places import most_recent_review_date

    site = business.get("site_score") or {}
    address = business.get("address") or {}
    return {
        "name": business.get("name") or "",
        "reason": business.get("excluded") or "",
        "detail": business.get("excluded_detail") or "",
        "last_review": most_recent_review_date(business) or "",
        "review_count": (
            "" if business.get("review_count") is None
            else business["review_count"]
        ),
        "site_verdict": site.get("verdict") or "",
        "website": business.get("website") or "",
        "town": address.get("town") or "",
        "place_id": business.get("place_id") or "",
    }


def write_excluded(batch: Batch, excluded: list[dict]) -> Optional[Path]:
    """Write excluded.csv beside the shortlist. Nothing excluded, no file."""
    import csv

    if not excluded:
        return None
    path = batch.excluded_path
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=EXCLUDED_COLUMNS)
        writer.writeheader()
        for business in excluded:
            writer.writerow(excluded_to_row(business))
    return path


LOOKUP_ERROR_COLUMNS = ["name", "step", "error", "website", "place_id"]


def lookup_errors(businesses: Iterable[dict]) -> list[dict]:
    """One row per failed step, across every business in the result."""
    out = []
    for b in businesses:
        for step, error in (b.get("enrich_errors") or {}).items():
            out.append({
                "name": b.get("name") or "",
                "step": step,
                "error": error,
                "website": b.get("website") or "",
                "place_id": b.get("place_id") or "",
            })
    return out


def write_lookup_errors(batch: Batch, businesses: list[dict]) -> Optional[Path]:
    """lookup_errors.csv beside the shortlist. No failures, no file."""
    import csv

    rows = lookup_errors(businesses)
    path = batch.path / "lookup_errors.csv"
    if not rows:
        path.unlink(missing_ok=True)
        return None
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=LOOKUP_ERROR_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return path


def write_batch(batch: Batch, result: DiscoverResult) -> None:
    """Persist a discover result into a batch folder."""
    for business in result.businesses:
        batch.write_business(business)
    batch.write_shortlist(result.rows)
    # The exclusions are the answer to "where did X go?" — a question that
    # gets asked every time the rules tighten, so the list lives on disk.
    write_excluded(batch, result.excluded)
    write_lookup_errors(batch, result.businesses)
    meta = batch.read_meta()
    meta["status_counts"] = {
        "shortlisted": len(result.businesses),
        "excluded": len(result.excluded),
    }
    meta["discovered"] = utcnow()
    batch.write_meta(meta)
