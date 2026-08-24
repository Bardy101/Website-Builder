"""Tests for the cull loop and the weight-tuning frequency table."""

import tempfile
import unittest
from pathlib import Path

from pipeline.cull import REASON_CODES, gut_share, reason_counts, split_shortlist, validate_reason
from pipeline.storage import Batch
from pipeline.tune import compare, format_report, propose, signal_summary


class TestCull(unittest.TestCase):
    def test_reason_codes_are_the_spec_list(self):
        self.assertEqual(
            set(REASON_CODES),
            {"chain", "winding_down", "site_fine", "too_small", "wrong_niche",
             "duplicate", "no_owner_signal", "gut"},
        )

    def test_validate_rejects_free_text(self):
        with self.assertRaises(ValueError):
            validate_reason("looked a bit shabby")

    def test_split_keeps_unmarked_rows(self):
        rows = [{"place_id": "a"}, {"place_id": "b"}, {"place_id": "c"}]
        approved, rejected = split_shortlist(rows, {"b": "chain"})
        self.assertEqual([r["place_id"] for r in approved], ["a", "c"])
        self.assertEqual(rejected[0]["reason"], "chain")

    def test_reason_counts_and_gut_share(self):
        rejections = [{"reason": "gut"}, {"reason": "gut"}, {"reason": "chain"}]
        self.assertEqual(reason_counts(rejections), {"gut": 2, "chain": 1})
        self.assertAlmostEqual(gut_share(rejections), 2 / 3)

    def test_gut_share_empty(self):
        self.assertEqual(gut_share([]), 0.0)


class TestBatchRejections(unittest.TestCase):
    def test_rejections_roundtrip_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch = Batch.create(tmp, niche="physio", area="hitchin")
            batch.append_rejection({"place_id": "a", "reason": "chain"})
            batch.append_rejection({"place_id": "b", "reason": "gut"})
            out = batch.read_rejections()
            self.assertEqual(len(out), 2)
            self.assertEqual(out[1]["reason"], "gut")


class TestTune(unittest.TestCase):
    def test_signal_summary(self):
        records = [
            {"lead_score": "40", "review_count": "10"},
            {"lead_score": "20", "review_count": "30"},
        ]
        summary = signal_summary(records)
        self.assertEqual(summary["lead_score"]["mean"], 30.0)
        self.assertEqual(summary["review_count"]["median"], 20.0)

    def test_too_small_cluster_proposes_higher_bar(self):
        approved = [{"lead_score": "50", "review_count": "40"}]
        rejected = [
            {"reason": "too_small", "review_count": "9", "lead_score": "20"},
            {"reason": "too_small", "review_count": "11", "lead_score": "20"},
            {"reason": "too_small", "review_count": "12", "lead_score": "20"},
        ]
        comparison = compare(approved, rejected)
        weights = {"thresholds": {"too_few_reviews_max": 5}}
        proposals = propose(comparison, weights)
        fields = [p["field"] for p in proposals]
        self.assertIn("thresholds.too_few_reviews_max", fields)
        prop = next(p for p in proposals if p["field"] == "thresholds.too_few_reviews_max")
        self.assertEqual(prop["current"], 5)
        self.assertEqual(prop["suggested"], 11)

    def test_site_fine_cluster_proposes_tighter_band(self):
        approved = [{"lead_score": "50", "mobile_score": "30"}]
        rejected = [
            {"reason": "site_fine", "mobile_score": "60", "lead_score": "15"},
            {"reason": "site_fine", "mobile_score": "62", "lead_score": "15"},
            {"reason": "site_fine", "mobile_score": "65", "lead_score": "15"},
        ]
        comparison = compare(approved, rejected)
        proposals = propose(comparison, {"thresholds": {"mobile_score_dated_max": 70}})
        prop = next(
            p for p in proposals if p["field"] == "thresholds.mobile_score_dated_max"
        )
        self.assertEqual(prop["suggested"], 62)

    def test_too_few_cases_proposes_nothing(self):
        comparison = compare(
            [{"lead_score": "50", "review_count": "40"}],
            [{"reason": "too_small", "review_count": "9", "lead_score": "20"}],
        )
        proposals = propose(comparison, {"thresholds": {"too_few_reviews_max": 5}})
        self.assertEqual(
            [p for p in proposals if p["field"] == "thresholds.too_few_reviews_max"], []
        )

    def test_rejections_outscoring_approvals_flags_the_signals(self):
        approved = [{"lead_score": "10"}]
        rejected = [{"reason": "gut", "lead_score": "80"}]
        proposals = propose(compare(approved, rejected), {"thresholds": {}})
        self.assertTrue(any("Narrow the niche" in p["why"] for p in proposals))

    def test_report_renders(self):
        comparison = compare(
            [{"lead_score": "50"}], [{"reason": "chain", "lead_score": "20"}]
        )
        text = format_report(comparison, [])
        self.assertIn("Rejections by reason code", text)
        self.assertIn("chain", text)
        self.assertIn("None. Not enough evidence yet", text)


if __name__ == "__main__":
    unittest.main()
