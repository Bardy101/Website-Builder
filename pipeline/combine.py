"""Combine existing batch folders into one re-ranked sheet.

Batches are cheap to slice after the fact because every business.json is
self-contained: combining reads folders already on disk and costs zero API
calls. Two slices cover the obvious questions:

    all physios across every town   -> filter by niche
    every niche inside one town     -> filter by town

Records are de-duplicated by Place ID (a business found by two searches
appears once) and re-scored with the *current* weights.json, so a combined
sheet built after a weights change ranks old discoveries under the new
rules — no re-fetching needed.

**A cull is respected.** Where a source batch has an approved.csv, only its
survivors are merged; a batch never culled contributes its whole shortlist.
Rejections apply across the whole merge, not just the batch they were made
in — rejecting a business is a judgement about the business, and a place
found by two searches should not come back through the other one. Pass
``full=True`` to ignore both and merge the original shortlists.
"""

from __future__ import annotations

from typing import Callable, Iterable, Optional

from .addressee import decide as decide_addressee
from .scoring import Weights, score_business, sort_key, staleness_points
from .storage import Batch, business_to_row


def _niche_matches(business: dict, wanted: str) -> bool:
    """Case-insensitive, containment both ways: 'physio' matches a batch
    searched as 'physiotherapist' and vice versa."""
    have = (business.get("niche") or "").lower().strip()
    want = wanted.lower().strip()
    return bool(have) and (want in have or have in want)


def _town_matches(business: dict, wanted: str) -> bool:
    have = ((business.get("address") or {}).get("town") or "").lower().strip()
    return have == wanted.lower().strip()


def approved_ids(batch: Batch) -> Optional[set[str]]:
    """Place IDs surviving this batch's cull, or None if it was never culled.

    None and an empty set mean different things: never culled contributes
    everything, culled-to-nothing contributes nothing.
    """
    if not batch.approved_path.is_file():
        return None
    return {
        row.get("place_id")
        for row in batch.read_shortlist(path=batch.approved_path)
        if row.get("place_id")
    }


def rejected_ids(batch: Batch) -> set[str]:
    """Place IDs this batch's cull rejected, with a reason recorded."""
    return {
        rej.get("place_id")
        for rej in batch.read_rejections()
        if rej.get("place_id")
    }


def combine_batches(
    sources: Iterable[Batch],
    *,
    niche: Optional[str] = None,
    town: Optional[str] = None,
    weights: Optional[Weights] = None,
    top: Optional[int] = None,
    full: bool = False,
    report: Optional[Callable[[str], None]] = None,
) -> list[dict]:
    """Merge, filter, re-score and re-rank businesses from several batches.

    Returns the merged business records, worst web presence first. A place
    seen in several batches keeps its highest re-scored record.

    Culled batches contribute only their approved rows, and a rejection in
    any source excludes that business from the whole merge. ``full=True``
    ignores the culls entirely. ``report`` is called with one line per
    source saying which list was used, so the choice is never silent.
    """
    weights = weights or Weights.load()
    sources = list(sources)
    say = report or (lambda _msg: None)
    by_id: dict[str, dict] = {}

    # Gathered across every source before merging any of them: a business
    # rejected in one town must not reappear via a neighbouring town's
    # search, which overlapping radii make likely.
    rejected: set[str] = set()
    if not full:
        for batch in sources:
            rejected |= rejected_ids(batch)

    for batch in sources:
        approved = None if full else approved_ids(batch)
        kept = 0
        dropped_elsewhere = 0
        for business in batch.iter_businesses():
            place_id = business.get("place_id")
            if not place_id:
                continue
            if approved is not None and place_id not in approved:
                continue
            if place_id in rejected:
                # Anything this batch rejected is already absent from its
                # approved.csv, so reaching here means another source
                # rejected it — worth reporting rather than silently losing.
                dropped_elsewhere += 1
                continue
            if niche and not _niche_matches(business, niche):
                continue
            if town and not _town_matches(business, town):
                continue
            result = score_business(business, weights)
            if result.excluded:
                continue
            record = dict(business)
            record["lead_score"] = result.score
            record["score_breakdown"] = result.breakdown
            record["staleness_points"] = staleness_points(record, weights)
            record["combined_from"] = batch.path.name
            # Recompute rather than copy: batches written before the website
            # contact lookup existed have no addressee at all, and the
            # decision is pure logic over the owner/site_contact already
            # stored — so an old record still gets a usable address_to.
            record["addressee"] = decide_addressee(
                record.get("owner"),
                record.get("site_contact"),
                company_type=(record.get("company") or {}).get("type", "unknown"),
            )
            place_id = record.get("place_id")
            if not place_id:
                continue
            existing = by_id.get(place_id)
            if existing is None or record["lead_score"] > existing["lead_score"]:
                by_id[place_id] = record
            kept += 1

        extra = (f", {dropped_elsewhere} dropped by a cull in another batch"
                 if dropped_elsewhere else "")
        if full:
            say(f"  {batch.path.name}: {kept} row(s) — cull ignored")
        elif approved is None:
            say(f"  {batch.path.name}: {kept} row(s) — never culled, using "
                f"the whole shortlist{extra}")
        else:
            say(f"  {batch.path.name}: {kept} row(s) from approved.csv{extra}")

    merged = sorted(by_id.values(), key=sort_key)
    if top:
        merged = merged[:top]
    return merged


def write_combined(
    batches_dir,
    merged: list[dict],
    *,
    name: str,
    source_names: list[str],
    niche_label: str = "combined",
    area_label: str = "combined",
) -> Batch:
    """Persist a combined sheet as an ordinary batch folder.

    It behaves like any other batch afterwards: cull it, tune from it, open
    its CSV. The businesses' folders are copied records, not symlinks, so
    the combined batch stands alone.
    """
    from .storage import utcnow

    batch = Batch.create(batches_dir, niche=niche_label, area=area_label, name=name)
    for business in merged:
        batch.write_business(business)
    batch.write_shortlist(business_to_row(b) for b in merged)
    meta = batch.read_meta()
    meta["combined_from"] = source_names
    meta["status_counts"] = {"shortlisted": len(merged)}
    meta["combined_at"] = utcnow()
    batch.write_meta(meta)
    return batch
