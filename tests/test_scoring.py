"""Tests for the lead-score table in spec section 4."""

import unittest
from pathlib import Path

from pipeline.scoring import Weights, looks_like_chain, score_business


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

    def test_slow_mobile_and_no_https_stack(self):
        b = business(
            website="http://example.co.uk",
            site_score={"verdict": "poor", "mobile_score": 31, "https": False},
        )
        result = score_business(b, self.w)
        self.assertEqual(result.breakdown["mobile_score_below_50"], 25)
        self.assertEqual(result.breakdown["no_https"], 15)
        self.assertEqual(result.score, 25 + 15 + 15)

    def test_good_site_scores_only_reputation(self):
        b = business(
            website="https://example.co.uk",
            site_score={"verdict": "fine", "mobile_score": 88, "https": True},
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
        # 25 reviews but only 4.0 rating -> no bonus
        result = score_business(business(review_count=25, rating=4.0), self.w)
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


if __name__ == "__main__":
    unittest.main()
