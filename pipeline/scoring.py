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
                "mobile_score_below_50": 25,
                "no_https": 15,
                "established_and_reputable": 15,
                "too_few_reviews": -20,
            },
            thresholds={
                "mobile_score_poor": 50,
                "mobile_score_dated_max": 70,
                "established_min_reviews": 20,
                "established_min_rating": 4.3,
                "too_few_reviews_max": 5,
            },
            exclude={"non_operational": True, "chain_or_franchise": True},
            chain_names=[],
        )


@dataclass
class ScoreResult:
    score: int
    excluded: bool
    exclude_reason: Optional[str]
    breakdown: dict


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

    site = business.get("site_score") or {}
    verdict = site.get("verdict") or ("none" if not business.get("website") else None)

    if verdict == "none":
        breakdown["no_website"] = sig.get("no_website", 40)
    elif verdict == "social_only":
        breakdown["social_or_directory_only"] = sig.get("social_or_directory_only", 30)
    else:
        mobile = site.get("mobile_score")
        if mobile is not None:
            if mobile < thr.get("mobile_score_poor", 50):
                breakdown["mobile_score_below_50"] = sig.get("mobile_score_below_50", 25)
            elif mobile < thr.get("mobile_score_dated_max", 70):
                # The graded band: without it, mobile 51 scored the same as
                # a flawless 95, and 49 vs 51 swung a full 25 points. A
                # visibly middling site now outranks a fine one.
                breakdown["mobile_score_middling"] = sig.get("mobile_score_middling", 12)
        if site.get("viewport") is False:
            breakdown["no_viewport"] = sig.get("no_viewport", 10)
        if site.get("https") is False:
            breakdown["no_https"] = sig.get("no_https", 15)

    rating = business.get("rating")
    reviews = business.get("review_count")
    if (
        reviews is not None
        and rating is not None
        and reviews >= thr.get("established_min_reviews", 20)
        and rating >= thr.get("established_min_rating", 4.3)
    ):
        breakdown["established_and_reputable"] = sig.get("established_and_reputable", 15)
    if reviews is not None and reviews < thr.get("too_few_reviews_max", 5):
        breakdown["too_few_reviews"] = sig.get("too_few_reviews", -20)

    return ScoreResult(sum(breakdown.values()), False, None, breakdown)
