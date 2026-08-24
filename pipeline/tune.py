"""tune.py's engine — a frequency table, not machine learning (spec 17).

For each reason code, compare the average value of each machine-visible
signal among rejected businesses versus approved ones. Where a cluster is
clear, propose a threshold adjustment as a diff against weights.json. The
human accepts or ignores it; nothing is applied automatically.
"""

from __future__ import annotations

from statistics import median
from typing import Any, Optional

from .cull import MACHINE_SIGNALS


def _value(record: dict, signal: str) -> Optional[float]:
    """Pull a machine signal out of either a CSV row or a business record."""
    if signal in record and record[signal] not in ("", None):
        try:
            return float(record[signal])
        except (TypeError, ValueError):
            return None
    site = record.get("site_score") or {}
    if signal == "mobile_score" and site.get("mobile_score") is not None:
        return float(site["mobile_score"])
    return None


def signal_summary(records: list[dict]) -> dict[str, dict[str, float]]:
    """Mean/median/count per machine signal across a set of records."""
    out: dict[str, dict[str, float]] = {}
    for signal in MACHINE_SIGNALS:
        values = [v for v in (_value(r, signal) for r in records) if v is not None]
        if values:
            out[signal] = {
                "n": len(values),
                "mean": round(sum(values) / len(values), 2),
                "median": round(median(values), 2),
                "min": min(values),
                "max": max(values),
            }
    return out


def compare(approved: list[dict], rejected: list[dict]) -> dict[str, Any]:
    """Per-reason-code comparison of signals against the approved set."""
    by_reason: dict[str, list[dict]] = {}
    for rec in rejected:
        by_reason.setdefault(rec.get("reason", "unknown"), []).append(rec)

    return {
        "approved": signal_summary(approved),
        "by_reason": {
            code: {"n": len(recs), "signals": signal_summary(recs)}
            for code, recs in sorted(by_reason.items())
        },
    }


def propose(comparison: dict, weights: dict, *, min_cases: int = 3) -> list[dict]:
    """Propose threshold changes from clear clusters. Conservative by design.

    Each proposal names the field, the current and suggested value, and the
    evidence — so an implausible one is easy to throw away.
    """
    proposals: list[dict] = []
    thresholds = weights.get("thresholds", {})
    by_reason = comparison.get("by_reason", {})

    # 'too_small' clustering at low review counts -> raise the low-review bar.
    too_small = by_reason.get("too_small")
    if too_small and too_small["n"] >= min_cases:
        stats = too_small["signals"].get("review_count")
        current = thresholds.get("too_few_reviews_max", 5)
        if stats and stats["median"] > current:
            suggested = int(round(stats["median"]))
            proposals.append(
                {
                    "field": "thresholds.too_few_reviews_max",
                    "current": current,
                    "suggested": suggested,
                    "why": (
                        f"{too_small['n']} 'too_small' rejections cluster at a median of "
                        f"{stats['median']} reviews, above the current {current} bar — "
                        "the penalty is not catching them."
                    ),
                }
            )

    # 'site_fine' clustering at decent mobile scores -> tighten the band.
    site_fine = by_reason.get("site_fine")
    if site_fine and site_fine["n"] >= min_cases:
        stats = site_fine["signals"].get("mobile_score")
        current = thresholds.get("mobile_score_dated_max", 70)
        if stats and stats["median"] < current:
            suggested = int(round(stats["median"]))
            proposals.append(
                {
                    "field": "thresholds.mobile_score_dated_max",
                    "current": current,
                    "suggested": suggested,
                    "why": (
                        f"{site_fine['n']} 'site_fine' rejections cluster at a median mobile "
                        f"score of {stats['median']}, below the current 'dated' ceiling of "
                        f"{current} — sites you'd call fine are being scored as dated."
                    ),
                }
            )

    # A high-scoring rejected set means the score is ranking the wrong things.
    approved_score = comparison.get("approved", {}).get("lead_score", {})
    all_rejected_scores = [
        r["signals"].get("lead_score", {}) for r in by_reason.values() if r["signals"]
    ]
    rejected_means = [s["mean"] for s in all_rejected_scores if s]
    if approved_score and rejected_means:
        rejected_mean = sum(rejected_means) / len(rejected_means)
        if rejected_mean > approved_score.get("mean", 0):
            proposals.append(
                {
                    "field": "(no single weight)",
                    "current": f"approved mean {approved_score.get('mean')}",
                    "suggested": f"rejected mean {round(rejected_mean, 2)}",
                    "why": (
                        "Rejections are scoring higher than approvals on average. The "
                        "signals, not the weights, are the problem — likely the niche is "
                        "too broad or Places categorisation is too noisy. Narrow the "
                        "niche before adding cleverness."
                    ),
                }
            )

    return proposals


def format_report(comparison: dict, proposals: list[dict]) -> str:
    """Render the comparison and proposals as a plain-text report."""
    lines: list[str] = []
    lines.append("Approved set")
    lines.append("-" * 60)
    for signal, stats in comparison.get("approved", {}).items():
        lines.append(
            f"  {signal:<16} n={int(stats['n']):<4} mean={stats['mean']:<8} "
            f"median={stats['median']}"
        )
    if not comparison.get("approved"):
        lines.append("  (no approved records yet)")

    lines.append("")
    lines.append("Rejections by reason code")
    lines.append("-" * 60)
    for code, block in comparison.get("by_reason", {}).items():
        lines.append(f"  {code}  (n={block['n']})")
        for signal, stats in block["signals"].items():
            approved_stats = comparison.get("approved", {}).get(signal, {})
            delta = ""
            if approved_stats:
                diff = stats["mean"] - approved_stats["mean"]
                delta = f"  ({diff:+.2f} vs approved)"
            lines.append(f"      {signal:<16} mean={stats['mean']:<8}{delta}")
        lines.append("")

    lines.append("Proposals")
    lines.append("-" * 60)
    if not proposals:
        lines.append("  None. Not enough evidence yet — keep culling.")
    for prop in proposals:
        lines.append(f"  {prop['field']}: {prop['current']} -> {prop['suggested']}")
        lines.append(f"      {prop['why']}")
        lines.append("")
    lines.append("Apply by hand, and bump _version / _changed_by_batch in weights.json.")
    return "\n".join(lines)
