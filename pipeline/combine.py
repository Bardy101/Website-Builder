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
"""

from __future__ import annotations

from typing import Iterable, Optional

from .addressee import decide as decide_addressee
from .scoring import Weights, score_business
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


def combine_batches(
    sources: Iterable[Batch],
    *,
    niche: Optional[str] = None,
    town: Optional[str] = None,
    weights: Optional[Weights] = None,
    top: Optional[int] = None,
) -> list[dict]:
    """Merge, filter, re-score and re-rank businesses from several batches.

    Returns the merged business records, worst web presence first. A place
    seen in several batches keeps its highest re-scored record.
    """
    weights = weights or Weights.load()
    by_id: dict[str, dict] = {}

    for batch in sources:
        for business in batch.iter_businesses():
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

    merged = sorted(
        by_id.values(),
        key=lambda b: (
            -b["lead_score"],
            -(b.get("review_count") or 0),
            b.get("name") or "",
        ),
    )
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
