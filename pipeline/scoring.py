"""Lead scoring — the weights table from spec section 4, made tunable.

    No website at all                          +40
    Facebook page / directory URL              +30
    Site with mobile score < 50                +25
    Site with no HTTPS                         +15
    review_count >= 20 and rating >= 4.3       +15
    review_count < 5                           -20
    business_status != OPERATIONAL             exclude
    Chain / franchise name match               exclude

Weights live in weights.json so ``tune.py`` can propose changes against a
versioned file rather than editing code (spec section 17).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .staleness import MODERN_PLATFORMS, staleness_flags

DEFAULT_WEIGHTS_PATH = "weights.json"


@dataclass
class Weights:
    signals: dict = field(default_factory=dict)
    thresholds: dict = field(default_factory=dict)
    exclude: dict = field(default_factory=dict)
    chain_names: list = field(default_factory=list)
    raw: dict = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path = DEFAULT_WEIGHTS_PATH) -> "Weights":
        p = Path(path)
        if not p.is_file():
            # Fall back to the copy beside the package, so running the CLIs
            # from another working directory still picks up tuned weights
            # rather than silently reverting to the built-in defaults.
            beside_package = Path(__file__).resolve().parent.parent / p.name
            if beside_package.is_file():
                p = beside_package
            else:
                return cls.default()
        data = json.loads(p.read_text(encoding="utf-8"))
        return cls(
            signals=data.get("signals", {}),
            thresholds=data.get("thresholds", {}),
            exclude=data.get("exclude", {}),
            chain_names=data.get("chain_names", []),
            raw=data,
        )

    @classmethod
    def default(cls) -> "Weights":
        return cls(
            signals={
                "no_website": 40,
                "social_or_directory_only": 30,
                "no_viewport": 25,
                "no_media_queries": 15,
                "copyright_stale": 15,
                "table_layout": 15,
                "no_https": 15,
                "flash": 10,
                "established_and_reputable": 15,
                "too_few_reviews": -20,
                "recent_review_activity": 5,
                "modern_platform": -15,
            },
            thresholds={
                "established_min_reviews": 20,
                "established_min_rating": 4.0,
                "too_few_reviews_max": 5,
                "recent_review_days": 90,
                "copyright_stale_years": 3,
                "dormant_after_days": 365,
                "min_site_points": 25,
            },
            exclude={
                "non_operational": True,
                "chain_or_franchise": True,
                "dormant": True,
                "site_fine": True,
            },
            chain_names=[],
        )


@dataclass
class ScoreResult:
    score: int
    excluded: bool
    exclude_reason: Optional[str]
    breakdown: dict
    # Human detail behind the reason code, e.g. "last review 2024-03-01".
    exclude_detail: Optional[str] = None


def _normalise(name: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", (name or "").lower())


def looks_like_chain(name: str, chain_names: list[str]) -> bool:
    """Substring match of a known chain/franchise name against the business name."""
    haystack = f" {_normalise(name)} "
    for chain in chain_names:
        needle = _normalise(chain).strip()
        if needle and needle in haystack:
            return True
    return False


def modern_platforms(weights: "Weights") -> set[str]:
    """Platforms that mean someone pays for and maintains the site.

    weights.json may list them (tune.py can propose additions from your
    culls); otherwise the built-in set. Detection is unaffected — this only
    decides which detected platforms earn the modern_platform penalty.
    """
    listed = (weights.raw or {}).get("modern_platforms")
    return {str(p).lower() for p in listed} if listed else set(MODERN_PLATFORMS)


# Short words for each staleness flag, for exclusion details a human reads.
_FLAG_WORDS = {
    "no_viewport": "no mobile viewport",
    "no_media_queries": "not responsive",
    "copyright_stale": "old copyright year",
    "table_layout": "table layout",
    "no_https": "no HTTPS",
    "flash": "Flash",
}


def dormancy(business: dict[str, Any], within_days: int) -> Optional[str]:
    """Why this business looks dormant, or None if it doesn't — or can't be told.

    A dormant listing is one with no review inside ``within_days``. The
    evidence has to be positive in both directions:

    - ``review_count`` absent means the details were never fetched (a search
      stub). No evidence, no verdict.
    - ``review_count`` of zero is real evidence: the listing has no reviews.
    - A positive count with no dated reviews returned is incomplete evidence
      (Places hands back at most five, by relevance, not recency), so it
      fails open rather than excluding a business that may well be trading.
    """
    from datetime import datetime, timezone

    from .places import most_recent_review_date

    count = business.get("review_count")
    if count is None:
        return None
    if count == 0:
        return "no reviews"
    latest = most_recent_review_date(business)
    if not latest:
        return None
    try:
        when = datetime.strptime(latest, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    if (datetime.now(timezone.utc) - when).days > within_days:
        return f"last review {latest}"
    return None


def score_business(business: dict[str, Any], weights: Weights) -> ScoreResult:
    """Score one business record. Returns score plus an audit breakdown.

    ``business`` is the flat business.json shape: name, website,
    business_status, rating, review_count, site_score {verdict, mobile_score,
    https}.
    """
    sig = weights.signals
    thr = weights.thresholds
    breakdown: dict[str, int] = {}

    status = (business.get("business_status") or "").upper()
    if weights.exclude.get("non_operational", True) and status and status != "OPERATIONAL":
        return ScoreResult(0, True, "non_operational", breakdown)

    if weights.exclude.get("chain_or_franchise", True) and looks_like_chain(
        business.get("name", ""), weights.chain_names
    ):
        return ScoreResult(0, True, "chain_or_franchise", breakdown)

    # A listing nobody has reviewed in a year is far more often a closed or
    # moved business, or a registered-office address, than a live prospect.
    # Only fires once details are in hand — see dormancy() for the evidence
    # rules — so the search-stub screen never trips it.
    if weights.exclude.get("dormant", False):
        why = dormancy(business, thr.get("dormant_after_days", 365))
        if why:
            return ScoreResult(0, True, "dormant", breakdown, why)

    site = business.get("site_score") or {}
    verdict = site.get("verdict") or ("none" if not business.get("website") else None)

    # Nothing to sell to a business whose site measured clean on every
    # staleness check. "unknown" (the fetch failed) is not "fine" and stays.
    if weights.exclude.get("site_fine", False) and verdict == "fine":
        return ScoreResult(0, True, "site_fine", breakdown, "no staleness signals")

    # Nor to one with too little wrong to sell a redesign on. One weak sign
    # alone — most often a footer still saying (c) 2021 on an otherwise
    # current site — used to put a good-looking site on the list as "poor".
    # A measured site needs min_site_points of problems: one strong sign (no
    # mobile viewport, 25) or two weaker ones (15 each) at the defaults.
    # Only measured sites: an unknown verdict established nothing either way.
    minimum = thr.get("min_site_points", 0)
    stale = business.get("staleness") or {}
    if (weights.exclude.get("site_fine", False) and minimum and stale.get("fetch_ok")
            and verdict not in ("none", "social_only")):
        flags = staleness_flags(stale, stale_years=thr.get("copyright_stale_years", 3))
        found = [name for name, fired in flags.items() if fired]
        points = sum(sig.get(name, 0) for name in found)
        if points < minimum:
            what = ", ".join(_FLAG_WORDS.get(n, n) for n in found) or "nothing"
            return ScoreResult(0, True, "site_fine", breakdown,
                               f"only {points} points wrong ({what}); needs {minimum}")

    if verdict == "none":
        breakdown["no_website"] = sig.get("no_website", 40)
    elif verdict == "social_only":
        breakdown["social_or_directory_only"] = sig.get("social_or_directory_only", 30)
    else:
        # Staleness, not slowness. Each flag is only set where the evidence
        # was positively established, so a failed fetch scores nothing.
        flags = staleness_flags(
            business.get("staleness") or {},
            stale_years=thr.get("copyright_stale_years", 3),
        )
        for name, fired in flags.items():
            if fired:
                breakdown[name] = sig.get(name, 0)

        # A recognised managed platform means someone pays for this site.
        platform = (business.get("staleness") or {}).get("platform_hint")
        if platform in modern_platforms(weights):
            breakdown["modern_platform"] = sig.get("modern_platform", -15)

    rating = business.get("rating")
    reviews = business.get("review_count")
    if (
        reviews is not None
        and rating is not None
        and reviews >= thr.get("established_min_reviews", 20)
        and rating >= thr.get("established_min_rating", 4.0)
    ):
        breakdown["established_and_reputable"] = sig.get("established_and_reputable", 15)
    if reviews is not None and reviews < thr.get("too_few_reviews_max", 5):
        breakdown["too_few_reviews"] = sig.get("too_few_reviews", -20)
    if _reviewed_recently(business, thr.get("recent_review_days", 90)):
        breakdown["recent_review_activity"] = sig.get("recent_review_activity", 5)

    return ScoreResult(sum(breakdown.values()), False, None, breakdown)


def _reviewed_recently(business: dict, within_days: int) -> bool:
    """Still trading properly — the best dormancy signal we have (spec section 2)."""
    from datetime import datetime, timezone

    from .places import most_recent_review_date

    latest = most_recent_review_date(business)
    if not latest:
        return False
    try:
        when = datetime.strptime(latest, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    return (datetime.now(timezone.utc) - when).days <= within_days


def staleness_points(business: dict, weights: Weights) -> int:
    """Subtotal from the staleness signals alone, for the CSV column.

    Uses the same flag set as the verdict, so the number a human reads during
    the cull and the bucket they see can never disagree.
    """
    flags = staleness_flags(
        business.get("staleness") or {},
        stale_years=weights.thresholds.get("copyright_stale_years", 3),
    )
    return sum(weights.signals.get(name, 0) for name, fired in flags.items() if fired)


def sort_key(business: dict):
    """Rank by lead_score, breaking ties on mobile score ascending.

    A low mobile score with no staleness flags is a rich modern site, not a
    neglected one, so the tiebreak only ever separates equal lead scores — it
    can never lift such a site on its own.
    """
    mobile = (business.get("site_score") or {}).get("mobile_score")
    return (
        -business.get("lead_score", 0),
        mobile if mobile is not None else 101,
        -(business.get("review_count") or 0),
        business.get("name") or "",
    )
