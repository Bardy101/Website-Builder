"""Tests for the lead-score table in spec section 4."""

import unittest
from pathlib import Path

from pipeline.scoring import (
    Weights,
    looks_like_chain,
    score_business,
    sort_key,
    staleness_points,
)


def business(**overrides):
    base = {
        "name": "Test Clinic",
        "business_status": "OPERATIONAL",
        "website": None,
        "rating": 4.5,
        "review_count": 30,
        "site_score": None,
    }
    base.update(overrides)
    return base


class TestScoring(unittest.TestCase):
    def setUp(self):
        self.w = Weights.default()
        self.w.chain_names = ["Nuffield Health", "Specsavers"]

    def test_no_website_scores_40_plus_established(self):
        result = score_business(business(), self.w)
        self.assertFalse(result.excluded)
        # +40 no website, +15 established
        self.assertEqual(result.score, 55)
        self.assertEqual(result.breakdown["no_website"], 40)

    def test_social_only_scores_30(self):
        b = business(
            website="https://facebook.com/x",
            site_score={"verdict": "social_only", "mobile_score": None, "https": True},
        )
        result = score_business(b, self.w)
        self.assertEqual(result.breakdown["social_or_directory_only"], 30)
        self.assertNotIn("no_website", result.breakdown)

    def test_staleness_signals_stack(self):
        b = business(
            website="http://example.co.uk",
            site_score={"verdict": "dated", "mobile_score": 31},
            staleness={"fetch_ok": True, "has_viewport": False, "has_https": False,
                       "has_media_queries": True, "copyright_age": 0,
                       "uses_table_layout": False, "has_flash": False,
                       "platform_hint": None},
        )
        result = score_business(b, self.w)
        self.assertEqual(result.breakdown["no_viewport"], 25)
        self.assertEqual(result.breakdown["no_https"], 15)
        self.assertEqual(result.score, 25 + 15 + 15)

    def test_good_site_scores_only_reputation(self):
        b = business(
            website="https://example.co.uk",
            site_score={"verdict": "fine", "mobile_score": 88},
            staleness={"fetch_ok": True, "has_viewport": True, "has_https": True,
                       "has_media_queries": True, "copyright_age": 0,
                       "uses_table_layout": False, "has_flash": False,
                       "platform_hint": None},
        )
        result = score_business(b, self.w)
        self.assertEqual(result.score, 15)

    def test_too_few_reviews_penalised(self):
        result = score_business(business(review_count=3, rating=5.0), self.w)
        # +40 no website, -20 too few reviews, no established bonus
        self.assertEqual(result.score, 20)
        self.assertEqual(result.breakdown["too_few_reviews"], -20)

    def test_non_operational_excluded(self):
        result = score_business(business(business_status="CLOSED_PERMANENTLY"), self.w)
        self.assertTrue(result.excluded)
        self.assertEqual(result.exclude_reason, "non_operational")

    def test_chain_excluded(self):
        result = score_business(business(name="Nuffield Health Hitchin"), self.w)
        self.assertTrue(result.excluded)
        self.assertEqual(result.exclude_reason, "chain_or_franchise")

    def test_established_bonus_needs_both_thresholds(self):
        # 25 reviews but only 3.5 rating -> no bonus (threshold is now 4.0)
        result = score_business(business(review_count=25, rating=3.5), self.w)
        self.assertNotIn("established_and_reputable", result.breakdown)

    def test_missing_rating_does_not_crash(self):
        result = score_business(business(rating=None, review_count=None), self.w)
        self.assertEqual(result.score, 40)

    def test_chain_match_is_word_aware(self):
        self.assertTrue(looks_like_chain("Specsavers Hitchin", ["Specsavers"]))
        self.assertFalse(looks_like_chain("Bancroft Physio", ["Specsavers"]))

    def test_weights_are_tunable(self):
        self.w.signals["no_website"] = 60
        result = score_business(business(review_count=1), self.w)
        self.assertEqual(result.breakdown["no_website"], 60)



class TestWeightsLoading(unittest.TestCase):
    """Running a CLI from another folder must not silently drop tuned weights."""

    def test_falls_back_to_copy_beside_package(self):
        import os
        import tempfile

        repo_weights = Path(__file__).resolve().parent.parent / "weights.json"
        self.assertTrue(repo_weights.is_file(), "repo weights.json should exist")
        cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            try:
                os.chdir(tmp)
                loaded = Weights.load("weights.json")
            finally:
                os.chdir(cwd)
        # Chain names only exist in the repo file, never in default().
        self.assertTrue(loaded.chain_names)
        self.assertEqual(loaded.chain_names, Weights.load(repo_weights).chain_names)

    def test_truly_absent_file_uses_defaults(self):
        loaded = Weights.load("no-such-weights-file.json")
        self.assertEqual(loaded.chain_names, [])
        self.assertEqual(loaded.signals["no_website"], 40)



def stale(**overrides):
    base = {"fetch_ok": True, "has_viewport": True, "has_media_queries": True,
            "copyright_year": 2026, "copyright_age": 0, "uses_table_layout": False,
            "has_https": True, "has_flash": False, "platform_hint": None}
    base.update(overrides)
    return base


class TestStalenessScoring(unittest.TestCase):
    """Brief 1.2: staleness scores, mobile score does not."""

    def setUp(self):
        self.w = Weights.load(Path(__file__).resolve().parent.parent / "weights.json")

    def _score(self, mobile=None, **stale_overrides):
        b = business(
            website="https://x.co.uk",
            site_score={"verdict": "x", "mobile_score": mobile},
            staleness=stale(**stale_overrides),
        )
        return score_business(b, self.w)

    def test_mobile_score_contributes_nothing(self):
        fast = self._score(mobile=98).score
        slow = self._score(mobile=3).score
        self.assertEqual(fast, slow)

    def test_each_staleness_signal_scores(self):
        self.assertEqual(self._score(has_viewport=False).breakdown["no_viewport"], 25)
        self.assertEqual(
            self._score(has_media_queries=False).breakdown["no_media_queries"], 15)
        self.assertEqual(self._score(copyright_age=5).breakdown["copyright_stale"], 15)
        self.assertEqual(
            self._score(uses_table_layout=True).breakdown["table_layout"], 15)
        self.assertEqual(self._score(has_https=False).breakdown["no_https"], 15)
        self.assertEqual(self._score(has_flash=True).breakdown["flash"], 10)

    def test_copyright_under_three_years_does_not_score(self):
        self.assertNotIn("copyright_stale", self._score(copyright_age=2).breakdown)
        self.assertIn("copyright_stale", self._score(copyright_age=3).breakdown)

    def test_modern_platform_is_penalised(self):
        result = self._score(platform_hint="squarespace")
        self.assertEqual(result.breakdown["modern_platform"], -15)

    def test_diy_platforms_are_neutral(self):
        for platform in ("wix", "godaddy", "weebly", "wordpress"):
            self.assertNotIn(
                "modern_platform", self._score(platform_hint=platform).breakdown,
                platform)

    def test_failed_fetch_scores_nothing(self):
        """Acceptance criterion 6: a failure never gains points."""
        b = business(website="https://down.example",
                     site_score={"verdict": "unknown", "mobile_score": None},
                     staleness={"fetch_ok": False})
        result = score_business(b, self.w)
        for signal in ("no_viewport", "no_media_queries", "copyright_stale",
                       "table_layout", "no_https", "flash"):
            self.assertNotIn(signal, result.breakdown)

    def test_established_threshold_is_now_four_point_zero(self):
        b = business(rating=4.1, review_count=30, website=None, site_score=None)
        self.assertIn("established_and_reputable", score_business(b, self.w).breakdown)

    def test_recent_review_activity_scores(self):
        from datetime import datetime, timedelta, timezone

        recent = (datetime.now(timezone.utc) - timedelta(days=10)).strftime("%Y-%m-%d")
        b = business(website=None, site_score=None,
                     reviews=[{"text": "x", "rating": 5, "time": f"{recent}T10:00:00Z"}])
        self.assertIn("recent_review_activity", score_business(b, self.w).breakdown)

    def test_dormant_business_gets_no_activity_points(self):
        b = business(website=None, site_score=None,
                     reviews=[{"text": "x", "rating": 5, "time": "2019-01-01T10:00:00Z"}])
        self.assertNotIn("recent_review_activity", score_business(b, self.w).breakdown)


class TestStalenessPointsColumn(unittest.TestCase):
    def setUp(self):
        self.w = Weights.load(Path(__file__).resolve().parent.parent / "weights.json")

    def test_subtotal_is_the_staleness_signals_only(self):
        b = business(
            website="https://x.co.uk", rating=4.8, review_count=60,
            site_score={"verdict": "dated", "mobile_score": 90},
            staleness=stale(has_viewport=False, uses_table_layout=True),
        )
        # 25 + 15 staleness, while lead_score also carries the +15 review bonus.
        self.assertEqual(staleness_points(b, self.w), 40)
        self.assertEqual(score_business(b, self.w).score, 55)

    def test_zero_when_nothing_is_stale(self):
        b = business(website="https://x.co.uk", site_score={"verdict": "fine"},
                     staleness=stale())
        self.assertEqual(staleness_points(b, self.w), 0)


class TestTiebreak(unittest.TestCase):
    """Mobile score separates equal lead scores and never ranks alone."""

    def test_lower_mobile_score_wins_a_tie(self):
        a = {"name": "a", "lead_score": 40, "site_score": {"mobile_score": 80}}
        b = {"name": "b", "lead_score": 40, "site_score": {"mobile_score": 20}}
        self.assertEqual([x["name"] for x in sorted([a, b], key=sort_key)], ["b", "a"])

    def test_lead_score_always_outranks_the_tiebreak(self):
        rich = {"name": "rich modern", "lead_score": 0,
                "site_score": {"mobile_score": 12}}
        stale_site = {"name": "stale", "lead_score": 55,
                      "site_score": {"mobile_score": 99}}
        order = [x["name"] for x in sorted([rich, stale_site], key=sort_key)]
        self.assertEqual(order, ["stale", "rich modern"])

    def test_missing_mobile_score_sorts_last_among_ties(self):
        known = {"name": "known", "lead_score": 40,
                 "site_score": {"mobile_score": 50}}
        unknown = {"name": "unknown", "lead_score": 40, "site_score": {}}
        self.assertEqual(
            [x["name"] for x in sorted([unknown, known], key=sort_key)],
            ["known", "unknown"])



class TestBriefAcceptanceCriterion1(unittest.TestCase):
    """The known-good site must no longer rank first.

    From the brief: 'a well-designed modern site with hero imagery, custom
    fonts, an embedded map and a booking widget scores badly and therefore
    ranks high. The signal is inverted.' This pins the fix.
    """

    def setUp(self):
        self.w = Weights.load(Path(__file__).resolve().parent.parent / "weights.json")

    def _ranked(self):
        candidates = [
            # The site the operator kept rejecting as site_fine.
            business(name="Known-good modern site", website="https://good",
                     rating=5.0, review_count=129,
                     site_score={"verdict": "fine", "mobile_score": 23},
                     staleness=stale(platform_hint="squarespace")),
            business(name="Dated 2009 build", website="https://dated",
                     rating=4.7, review_count=55,
                     site_score={"verdict": "dated", "mobile_score": 88},
                     staleness=stale(has_viewport=False, copyright_age=17)),
            business(name="Table layout, no https", website="http://old",
                     rating=4.5, review_count=32,
                     site_score={"verdict": "dated", "mobile_score": 95},
                     staleness=stale(has_viewport=False, uses_table_layout=True,
                                     has_https=False, copyright_age=19)),
        ]
        for candidate in candidates:
            candidate["lead_score"] = score_business(candidate, self.w).score
        return sorted(candidates, key=sort_key)

    def test_known_good_site_is_not_rank_one(self):
        self.assertNotEqual(self._ranked()[0]["name"], "Known-good modern site")

    def test_known_good_site_ranks_last(self):
        self.assertEqual(self._ranked()[-1]["name"], "Known-good modern site")

    def test_the_worst_site_ranks_first(self):
        self.assertEqual(self._ranked()[0]["name"], "Table layout, no https")

    def test_being_fast_no_longer_helps_or_hurts(self):
        """The old scoring gave the 95-scoring site nothing and the
        23-scoring site +25. Now neither is affected by speed."""
        ranked = self._ranked()
        fastest = max(ranked, key=lambda b: b["site_score"]["mobile_score"])
        self.assertEqual(fastest["name"], "Table layout, no https")
        self.assertEqual(ranked[0]["name"], fastest["name"])


if __name__ == "__main__":
    unittest.main()
