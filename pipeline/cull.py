"""The cull loop — turning your judgement into weights (spec section 17).

The discover stage ranks; you cull. The cull is not overhead to be
automated away: it is how the scoring model gets trained, and it is the
only part of the pipeline that sees what the API cannot.

Reason codes are a fixed, short list — free text kills the analysis.
"""

from __future__ import annotations

from typing import Optional

REASON_CODES = {
    "chain": "Franchise or multi-site; head office decides",
    "winding_down": "Looks dormant, sparse recent reviews, closure signals",
    "site_fine": "Existing site is genuinely good enough",
    "too_small": "One-person operation unlikely to spend",
    "wrong_niche": "Miscategorised by Places",
    "duplicate": "Same business, second listing",
    "no_owner_signal": "No named contact and no route to one",
    "gut": "You just don't fancy it — kept deliberately, and tracked",
}

# Numeric signals tune.py compares between approved and rejected sets.
MACHINE_SIGNALS = [
    "lead_score",
    "mobile_score",
    "rating",
    "review_count",
    "photo_count",
]


def validate_reason(code: str) -> str:
    if code not in REASON_CODES:
        raise ValueError(
            f"Unknown reason code '{code}'. Use one of: {', '.join(sorted(REASON_CODES))}"
        )
    return code


def split_shortlist(
    rows: list[dict], decisions: dict[str, Optional[str]]
) -> tuple[list[dict], list[dict]]:
    """Split shortlist rows into (approved, rejected) using a decisions map.

    ``decisions`` maps place_id -> reason code for rejections. Any row not
    named in the map is approved.
    """
    approved, rejected = [], []
    for row in rows:
        reason = decisions.get(row.get("place_id"))
        if reason:
            rejected.append({**row, "reason": reason})
        else:
            approved.append(row)
    return approved, rejected


def reason_counts(rejections: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for rej in rejections:
        code = rej.get("reason") or "unknown"
        counts[code] = counts.get(code, 0) + 1
    return counts


def gut_share(rejections: list[dict]) -> float:
    """Share of rejections coded 'gut'.

    Above ~20% after three batches there is a real signal not yet
    articulated, and it's worth sitting down to name it.
    """
    if not rejections:
        return 0.0
    return reason_counts(rejections).get("gut", 0) / len(rejections)
