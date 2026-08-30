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


def decisions_from_export(payload) -> dict[str, str]:
    """Turn a contact sheet's decisions.json into a place_id -> reason map.

    Only culls carry a reason; keeps are simply absent from the map, which is
    what split_shortlist expects. An unknown reason code is rejected rather
    than silently coerced, because free text would poison the tuning data.
    """
    if not isinstance(payload, list):
        raise ValueError("decisions.json must be a list of decisions")

    decisions: dict[str, str] = {}
    for entry in payload:
        if not isinstance(entry, dict):
            raise ValueError(f"Not a decision object: {entry!r}")
        place_id = entry.get("place_id")
        if not place_id:
            raise ValueError(f"Decision without a place_id: {entry!r}")
        decision = (entry.get("decision") or "").strip().lower()
        if decision == "keep":
            continue
        if decision != "cull":
            raise ValueError(
                f"'{place_id}': decision must be 'keep' or 'cull', got "
                f"'{entry.get('decision')}'"
            )
        reason = (entry.get("reason") or "").strip().lower()
        if not reason:
            raise ValueError(
                f"'{entry.get('name') or place_id}' was culled with no reason "
                "code. Pick one in the sheet and export again — free text and "
                "blanks would make the rejection useless to tune.py."
            )
        if reason not in REASON_CODES:
            raise ValueError(
                f"'{entry.get('name') or place_id}': unknown reason code "
                f"'{reason}'. Use one of: {', '.join(sorted(REASON_CODES))}"
            )
        decisions[place_id] = reason
    return decisions
