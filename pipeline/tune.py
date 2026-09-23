"""tune.py's engine — a frequency table, not machine learning (spec 17).

For each reason code, compare what the machine saw on the businesses you
rejected against the ones you kept, and where a cluster is clear, propose a
change to weights.json. Every proposal names the setting, the current and
suggested value, and the evidence in counts ("7 of your 9 'site_fine'
culls…"), so an implausible one is easy to throw away. Proposals carrying a
``path`` can be applied by ``tune.py --apply``; the rest are advice.

What it learns from is what now decides selection: the staleness signals
(viewport, media queries, copyright year, table layout, HTTPS, Flash), the
points they add up to, the site verdict and platform, and review recency.
An earlier version compared PageSpeed mobile scores and proposed a
``mobile_score_dated_max`` that nothing read — scoring stopped using mobile
score when staleness replaced it, and the tuner never followed.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from math import ceil
from statistics import median
from typing import Any, Optional

from .staleness import MODERN_PLATFORMS, staleness_flags

# Numeric signals summarised per set. site_points is recomputed from the
# flags under the current weights, not read from an old CSV column.
NUMERIC_SIGNALS = ["lead_score", "site_points", "review_count", "rating", "review_age_days"]

FLAGS = ["no_viewport", "no_media_queries", "copyright_stale", "table_layout",
         "no_https", "flash"]

# How lopsided a pattern must be before it becomes a proposal. Deliberately
# conservative: a wrong proposal applied costs a batch of good prospects.
DOMINANT = 0.6        # share of a reason's culls showing the pattern
RARE_IN_KEEPS = 0.3   # at most this share of your keeps showing it
MAX_KEEPS_LOST = 0.2  # a new bar may drop at most this share of your keeps


# -- turning records into features ----------------------------------------

def _num(value) -> Optional[float]:
    if value in ("", None):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _yes(value) -> Optional[bool]:
    """A CSV tri-state ('yes'/'no'/blank) as True/False/None."""
    if isinstance(value, bool):
        return value
    v = str(value or "").strip().lower()
    return True if v in ("yes", "true") else False if v in ("no", "false") else None


def record_flags(record: dict, *, stale_years: int = 3,
                 today: Optional[date] = None) -> Optional[list[str]]:
    """The staleness flags that fired on a record, or None if never measured.

    Prefers the business record's own staleness analysis; falls back to the
    shortlist columns, so approved.csv rows and older rejections still count.
    """
    stale = record.get("staleness")
    if isinstance(stale, dict) and stale.get("fetch_ok"):
        return [f for f, fired in staleness_flags(stale, stale_years=stale_years).items()
                if fired]
    if isinstance(stale, dict) and stale and not stale.get("fetch_ok"):
        return None
    viewport = _yes(record.get("has_viewport"))
    media = _yes(record.get("has_media_queries"))
    table = _yes(record.get("uses_table_layout"))
    https = _yes(record.get("https"))
    flash = _yes(record.get("has_flash"))
    year = _num(record.get("copyright_year"))
    if all(v is None for v in (viewport, media, table, flash, year)):
        return None
    this_year = (today or datetime.now(timezone.utc).date()).year
    flags = []
    if viewport is False:
        flags.append("no_viewport")
    if media is False:
        flags.append("no_media_queries")
    if year is not None and this_year - year >= stale_years:
        flags.append("copyright_stale")
    if table is True:
        flags.append("table_layout")
    if https is False:
        flags.append("no_https")
    if flash is True:
        flags.append("flash")
    return flags


def _verdict(record: dict) -> str:
    return (record.get("site_verdict") or (record.get("site_score") or {}).get("verdict")
            or "")


def _platform(record: dict) -> str:
    return (record.get("platform_hint") or (record.get("staleness") or {}).get("platform_hint")
            or "").lower()


def _review_age(record: dict, today: date) -> Optional[float]:
    raw = record.get("recent_review_date") or ""
    try:
        return float((today - date.fromisoformat(str(raw)[:10])).days)
    except ValueError:
        return None


def features(record: dict, *, stale_years: int = 3, today: Optional[date] = None) -> dict:
    today = today or datetime.now(timezone.utc).date()
    return {
        "flags": record_flags(record, stale_years=stale_years, today=today),
        "verdict": _verdict(record),
        "platform": _platform(record),
        "review_age_days": _review_age(record, today),
        "lead_score": _num(record.get("lead_score")),
        "review_count": _num(record.get("review_count")),
        "rating": _num(record.get("rating")),
    }


def _is_site(feat: dict) -> bool:
    """A real website that was measured — the only kind a site rule can judge."""
    return feat["flags"] is not None and feat["verdict"] not in ("none", "social_only")


def _points(feat: dict, signals: dict) -> int:
    return sum(int(signals.get(f, 0)) for f in feat["flags"] or [])


# -- comparing ---------------------------------------------------------------

def signal_summary(records: list[dict]) -> dict[str, dict[str, float]]:
    """Mean/median/count per numeric signal across a set of records."""
    out: dict[str, dict[str, float]] = {}
    for signal in NUMERIC_SIGNALS:
        values = [v for v in (_num(r.get(signal)) for r in records) if v is not None]
        if values:
            out[signal] = {
                "n": len(values),
                "mean": round(sum(values) / len(values), 2),
                "median": round(median(values), 2),
                "min": min(values),
                "max": max(values),
            }
    return out


def _flag_rates(feats: list[dict]) -> dict[str, dict]:
    sites = [f for f in feats if _is_site(f)]
    return {flag: {"count": sum(1 for f in sites if flag in f["flags"]), "of": len(sites)}
            for flag in FLAGS}


def compare(approved: list[dict], rejected: list[dict], *,
            weights: Optional[dict] = None, today: Optional[date] = None) -> dict[str, Any]:
    """Per-reason-code comparison against the approved set.

    ``weights`` (the weights.json dict) sets the copyright-age cut-off and
    what each flag is worth, so site_points reflects the current weights
    rather than whatever an older CSV column recorded.
    """
    if weights is None:
        from .scoring import Weights

        default = Weights.default()
        weights = {"signals": default.signals, "thresholds": default.thresholds}
    stale_years = weights.get("thresholds", {}).get("copyright_stale_years", 3)
    signals = weights.get("signals", {})

    def enrich(records):
        out = []
        for r in records:
            feat = features(r, stale_years=stale_years, today=today)
            extra = {"review_age_days": feat["review_age_days"], "_feat": feat}
            if _is_site(feat):
                extra["site_points"] = _points(feat, signals)
            out.append({**r, **extra})
        return out

    approved_f = enrich(approved)
    by_reason: dict[str, list[dict]] = {}
    for rec in enrich(rejected):
        by_reason.setdefault(rec.get("reason") or "unknown", []).append(rec)

    def block(recs):
        feats = [r["_feat"] for r in recs]
        return {"n": len(recs), "signals": signal_summary(recs),
                "flags": _flag_rates(feats), "features": feats}

    return {"approved": {**signal_summary(approved_f), "_block": block(approved_f)},
            "by_reason": {code: block(recs) for code, recs in sorted(by_reason.items())}}


# -- proposing ---------------------------------------------------------------

def _round5(x: float) -> int:
    return int(5 * round(x / 5))


def _share(n: int, of: int) -> float:
    return n / of if of else 0.0


def propose(comparison: dict, weights: dict, *, min_cases: int = 3) -> list[dict]:
    """Proposals from clear clusters. Conservative by design."""
    proposals: list[dict] = []
    thresholds = weights.get("thresholds", {})
    signals = weights.get("signals", {})
    by_reason = comparison.get("by_reason", {})
    approved_block = comparison.get("approved", {}).get("_block") or {"features": []}
    kept_sites = [f for f in approved_block["features"] if _is_site(f)]

    # -- 'site_fine': good sites still reaching the list ----------------------
    fine = by_reason.get("site_fine")
    fine_sites = [f for f in (fine or {}).get("features", []) if _is_site(f)]
    if len(fine_sites) >= min_cases:
        proposals.extend(_propose_for_fine(fine_sites, kept_sites, signals, thresholds,
                                           weights, min_cases))

    # -- 'too_small': the few-reviews bar is too low ---------------------------
    too_small = by_reason.get("too_small")
    if too_small and too_small["n"] >= min_cases:
        stats = too_small["signals"].get("review_count")
        current = thresholds.get("too_few_reviews_max", 5)
        if stats and stats["median"] > current:
            suggested = int(round(stats["median"]))
            proposals.append({
                "field": "thresholds.too_few_reviews_max",
                "path": ["thresholds", "too_few_reviews_max"],
                "current": current,
                "suggested": suggested,
                "why": (f"{too_small['n']} 'too_small' rejections cluster at a median of "
                        f"{stats['median']} reviews, above the current {current} bar — "
                        "the penalty is not catching them."),
            })

    # -- 'winding_down': still-listed but fading businesses --------------------
    winding = by_reason.get("winding_down")
    if winding:
        ages = [f["review_age_days"] for f in winding["features"]
                if f["review_age_days"] is not None]
        current = thresholds.get("dormant_after_days", 365)
        if len(ages) >= min_cases:
            suggested = max(90, 30 * int(median(ages) // 30))
            kept_ages = [f["review_age_days"] for f in approved_block["features"]
                         if f["review_age_days"] is not None]
            lost = sum(1 for a in kept_ages if a > suggested)
            caught = sum(1 for a in ages if a > suggested)
            if suggested < current and lost <= MAX_KEEPS_LOST * len(kept_ages):
                proposals.append({
                    "field": "thresholds.dormant_after_days",
                    "path": ["thresholds", "dormant_after_days"],
                    "current": current,
                    "suggested": suggested,
                    "why": (f"The businesses you culled as winding down were last reviewed a "
                            f"median {int(median(ages))} days ago. Treating {suggested}+ days "
                            f"as dormant would have caught {caught} of {len(ages)} of them "
                            f"and dropped {lost} of {len(kept_ages)} you kept."),
                })

    # -- diagnostic: rejections outscoring approvals -----------------------------
    approved_score = comparison.get("approved", {}).get("lead_score", {})
    rejected_means = [r["signals"]["lead_score"]["mean"] for r in by_reason.values()
                      if r["signals"].get("lead_score")]
    if approved_score and rejected_means:
        rejected_mean = sum(rejected_means) / len(rejected_means)
        if rejected_mean > approved_score.get("mean", 0):
            proposals.append({
                "field": "(no single weight)",
                "current": f"approved mean {approved_score.get('mean')}",
                "suggested": f"rejected mean {round(rejected_mean, 2)}",
                "why": ("Rejections are scoring higher than approvals on average. The "
                        "signals, not the weights, are the problem — likely the niche is "
                        "too broad or Places categorisation is too noisy. Narrow the "
                        "niche before adding cleverness."),
            })
    return proposals


def _propose_for_fine(fine_sites, kept_sites, signals, thresholds, weights, min_cases):
    """What the sites you called fine had in common, turned into a fix that works.

    Every candidate change is simulated on your own culls and keeps before
    it is proposed: it must leave at least half the 'fine' culls below the
    min_site_points bar, and cost no more than a fifth of the sites you kept.
    A plausible-sounding change that wouldn't actually move them off the list
    is not proposed.

    First choice: weaken the one signal that fires on most of them and few of
    your keeps — the precise fix. Only if none works: raise the bar. Never
    both; each alone corrects the same leak, and both would over-correct.
    """
    out: list[dict] = []
    n = len(fine_sites)
    bar_now = int(thresholds.get("min_site_points", 0))

    def outcome(sigs: dict, bar: int) -> tuple[int, int]:
        caught = sum(1 for f in fine_sites if _points(f, sigs) < bar)
        lost = sum(1 for f in kept_sites if _points(f, sigs) < bar)
        return caught, lost

    def acceptable(caught: int, lost: int) -> bool:
        return caught >= ceil(n / 2) and lost <= MAX_KEEPS_LOST * len(kept_sites)

    best = None   # (caught, -lost, -change, flag, new_weight)
    for flag in FLAGS:
        weight = int(signals.get(flag, 0))
        hits = sum(1 for f in fine_sites if flag in f["flags"])
        kept_hits = sum(1 for f in kept_sites if flag in f["flags"])
        if (weight <= 0 or hits < min_cases or _share(hits, n) < DOMINANT
                or (kept_sites and _share(kept_hits, len(kept_sites)) > RARE_IN_KEEPS)):
            continue
        # The gentlest cut that works, in steps of 5.
        for new_weight in range(weight - 5, -1, -5):
            caught, lost = outcome({**signals, flag: new_weight}, bar_now)
            if acceptable(caught, lost):
                candidate = (caught, -lost, -(weight - new_weight), flag, new_weight)
                if best is None or candidate[:3] > best[:3]:
                    best = candidate
                break
    if best is not None:
        caught, neg_lost, _, flag, new_weight = best
        hits = sum(1 for f in fine_sites if flag in f["flags"])
        kept_hits = sum(1 for f in kept_sites if flag in f["flags"])
        before, _ = outcome(signals, bar_now)
        out.append({
            "field": f"signals.{flag}",
            "path": ["signals", flag],
            "current": int(signals.get(flag, 0)),
            "suggested": new_weight,
            "why": (f"'{flag}' fired on {hits} of the {n} sites you culled as fine but on "
                    f"{kept_hits} of the {len(kept_sites)} you kept, so it is flagging sites "
                    f"you think look good. At {new_weight}, {caught} of those {n} would "
                    f"fall below the {bar_now}-point bar and stay off the list (now "
                    f"{before}), and {-neg_lost} of your keeps would."),
        })
    else:
        points = sorted(_points(f, signals) for f in fine_sites)
        for bar in sorted({p + 5 for p in points if p + 5 > bar_now}):
            caught, lost = outcome(signals, bar)
            if acceptable(caught, lost):
                out.append({
                    "field": "thresholds.min_site_points",
                    "path": ["thresholds", "min_site_points"],
                    "current": bar_now,
                    "suggested": bar,
                    "why": (f"{caught} of the {n} sites you culled as fine had fewer than "
                            f"{bar} points of problems; {lost} of the {len(kept_sites)} "
                            f"sites you kept did. Requiring {bar} would have left them "
                            "off the list."),
                })
                break

    # A platform your 'fine' culls share, not yet treated as modern. A separate
    # lever from the two above — it lowers ranking rather than excluding — so
    # it is considered whichever of them was proposed.
    listed = (weights.get("modern_platforms") or sorted(MODERN_PLATFORMS))
    known = [f["platform"] for f in fine_sites if f["platform"]]
    for platform in sorted(set(known)):
        if platform in listed or platform == "wordpress":
            continue
        hits = known.count(platform)
        kept_known = [f["platform"] for f in kept_sites if f["platform"]]
        kept_hits = kept_known.count(platform)
        if (hits >= min_cases and _share(hits, len(known)) >= DOMINANT
                and (not kept_known or _share(kept_hits, len(kept_known)) <= RARE_IN_KEEPS)):
            out.append({
                "field": "modern_platforms",
                "path": ["modern_platforms"],
                "current": list(listed),
                "suggested": sorted(set(listed) | {platform}),
                "why": (f"{hits} of the {len(known)} sites you culled as fine with a known "
                        f"platform were on {platform}; {kept_hits} of the sites you kept "
                        f"were. Treating {platform} as a maintained platform gives it the "
                        "modern_platform penalty, so its sites rank lower."),
            })
    return out


# -- applying -----------------------------------------------------------------

def apply(weights: dict, proposals: list[dict]) -> list[str]:
    """Write proposals that name a path into a weights dict. Returns what changed."""
    changed = []
    for prop in proposals:
        path = prop.get("path")
        if not path:
            continue
        node = weights
        for key in path[:-1]:
            node = node.setdefault(key, {})
        before = node.get(path[-1], prop.get("current"))
        node[path[-1]] = prop["suggested"]
        changed.append(f"{'.'.join(path)}: {before} -> {prop['suggested']}")
    if changed:
        weights["_version"] = int(weights.get("_version", 0)) + 1
        weights["_tuned"] = datetime.now(timezone.utc).date().isoformat()
    return changed


# -- reporting ----------------------------------------------------------------

def format_report(comparison: dict, proposals: list[dict]) -> str:
    """Render the comparison and proposals as a plain-text report."""
    lines: list[str] = []
    approved = {k: v for k, v in comparison.get("approved", {}).items() if k != "_block"}
    lines.append("Approved set")
    lines.append("-" * 60)
    for signal, stats in approved.items():
        lines.append(f"  {signal:<16} n={int(stats['n']):<4} mean={stats['mean']:<8} "
                     f"median={stats['median']}")
    if not approved:
        lines.append("  (no approved records yet)")

    lines.append("")
    lines.append("Rejections by reason code")
    lines.append("-" * 60)
    kept_flags = (comparison.get("approved", {}).get("_block") or {}).get("flags", {})
    for code, block in comparison.get("by_reason", {}).items():
        lines.append(f"  {code}  (n={block['n']})")
        for signal, stats in block["signals"].items():
            approved_stats = approved.get(signal, {})
            delta = ""
            if approved_stats:
                delta = f"  ({stats['mean'] - approved_stats['mean']:+.2f} vs approved)"
            lines.append(f"      {signal:<16} mean={stats['mean']:<8}{delta}")
        flags = block.get("flags") or {}
        shown = [(f, r) for f, r in flags.items() if r["count"]]
        if shown:
            lines.append("      site problems seen (these culls / your keeps):")
            for flag, rate in shown:
                kept = kept_flags.get(flag, {"count": 0, "of": 0})
                lines.append(f"        {flag:<18} {rate['count']}/{rate['of']}"
                             f"   vs   {kept['count']}/{kept['of']}")
        lines.append("")

    lines.append("Proposals")
    lines.append("-" * 60)
    if not proposals:
        lines.append("  None. Not enough evidence yet — keep culling, and code your")
        lines.append("  good-site culls as 'site_fine' so this has something to learn from.")
    for prop in proposals:
        lines.append(f"  {prop['field']}: {prop['current']} -> {prop['suggested']}")
        lines.append(f"      {prop['why']}")
        lines.append("")
    if any(p.get("path") for p in proposals):
        lines.append("To apply: tune.py --apply (backs up weights.json first), or the")
        lines.append("'Apply tune's suggestions' button on the Batches tab.")
    return "\n".join(lines)
