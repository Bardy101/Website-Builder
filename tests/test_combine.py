"""Tests for combining batches after the fact.

The two slices that matter: one niche across every town, and every niche
inside one town. Zero API calls either way — it re-reads batch folders.
"""

import tempfile
import unittest
from pathlib import Path

from pipeline.combine import combine_batches, write_combined
from pipeline.scoring import Weights
from pipeline.storage import Batch

ROOT = Path(__file__).resolve().parent.parent


def record(place_id, name, niche, town, score_hint=None, **overrides):
    base = {
        "place_id": place_id,
        "name": name,
        "niche": niche,
        "business_status": "OPERATIONAL",
        "address": {"town": town, "postcode": "SG5 1AA", "formatted": f"1 High St, {town}"},
        "website": None,
        "rating": 4.8,
        "review_count": 50,
        "site_score": {"verdict": "none", "mobile_score": None,
                       "https": None, "viewport": None},
        "photo_count": 0,
        "reviews": [],
        "owner": {"name": None, "source": None, "confidence": "none"},
        "company": {"number": None, "type": "unknown"},
        "lead_score": 0,
    }
    base.update(overrides)
    return base


class TestCombine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.weights = Weights.load(ROOT / "weights.json")

        self.hitchin_physio = Batch.create(self.tmp.name, niche="physiotherapist",
                                           area="hitchin", name="b1")
        self.hitchin_physio.write_business(
            record("P1", "Hitchin Physio", "physiotherapist", "Hitchin"))
        self.hitchin_physio.write_business(
            record("P2", "Bancroft Backs", "physiotherapist", "Hitchin"))

        self.stevenage_physio = Batch.create(self.tmp.name, niche="physiotherapist",
                                             area="stevenage", name="b2")
        self.stevenage_physio.write_business(
            record("P3", "Stevenage Physio", "physiotherapist", "Stevenage"))
        # P1 found again from the neighbouring search — must appear once.
        self.stevenage_physio.write_business(
            record("P1", "Hitchin Physio", "physiotherapist", "Hitchin"))

        self.hitchin_plumbers = Batch.create(self.tmp.name, niche="plumber",
                                             area="hitchin", name="b3")
        self.hitchin_plumbers.write_business(
            record("PL1", "Hitchin Plumbing", "plumber", "Hitchin"))

        self.all_batches = [self.hitchin_physio, self.stevenage_physio,
                            self.hitchin_plumbers]

    def tearDown(self):
        self.tmp.cleanup()

    def test_niche_slice_spans_towns(self):
        merged = combine_batches(self.all_batches, niche="physiotherapist",
                                 weights=self.weights)
        names = {b["name"] for b in merged}
        self.assertEqual(names, {"Hitchin Physio", "Bancroft Backs", "Stevenage Physio"})

    def test_niche_filter_is_substring_both_ways(self):
        merged = combine_batches(self.all_batches, niche="physio", weights=self.weights)
        self.assertEqual(len(merged), 3)

    def test_town_slice_spans_niches(self):
        merged = combine_batches(self.all_batches, town="Hitchin", weights=self.weights)
        names = {b["name"] for b in merged}
        self.assertEqual(names, {"Hitchin Physio", "Bancroft Backs", "Hitchin Plumbing"})

    def test_duplicates_across_batches_appear_once(self):
        merged = combine_batches(self.all_batches, weights=self.weights)
        ids = [b["place_id"] for b in merged]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(ids.count("P1"), 1)

    def test_records_are_rescored_with_current_weights(self):
        """A stale lead_score of 0 on disk must not survive combining."""
        merged = combine_batches([self.hitchin_physio], weights=self.weights)
        # no website (+40) + established (+15) under current weights.
        self.assertTrue(all(b["lead_score"] == 55 for b in merged))

    def test_rescoring_applies_the_graded_band_to_old_records(self):
        old_style = Batch.create(self.tmp.name, niche="dentist", area="x", name="b4")
        old_style.write_business(record(
            "D1", "Dated Dental", "dentist", "Hitchin",
            website="https://dated.example",
            site_score={"verdict": "dated", "mobile_score": 60,
                        "https": True, "viewport": True},
            lead_score=15,  # what the cliff scoring stored
        ))
        merged = combine_batches([old_style], weights=self.weights)
        # +12 middling band + 15 established = 27 under v2 weights.
        self.assertEqual(merged[0]["lead_score"], 27)

    def test_sorted_worst_presence_first(self):
        merged = combine_batches(self.all_batches, weights=self.weights)
        scores = [b["lead_score"] for b in merged]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_write_combined_is_an_ordinary_batch(self):
        merged = combine_batches(self.all_batches, weights=self.weights)
        out = write_combined(self.tmp.name, merged, name="combo",
                             source_names=["b1", "b2", "b3"])
        rows = out.read_shortlist()
        self.assertEqual(len(rows), len(merged))
        meta = out.read_meta()
        self.assertEqual(meta["combined_from"], ["b1", "b2", "b3"])
        # And it can itself be read back business-by-business.
        self.assertEqual(len(list(out.iter_businesses())), len(merged))

    def test_excluded_records_stay_out(self):
        closed = Batch.create(self.tmp.name, niche="physio", area="x", name="b5")
        closed.write_business(record(
            "C1", "Closed Clinic", "physiotherapist", "Hitchin",
            business_status="CLOSED_PERMANENTLY",
        ))
        merged = combine_batches([closed], weights=self.weights)
        self.assertEqual(merged, [])

    def test_no_filter_no_match_towns_are_left_alone(self):
        merged = combine_batches(self.all_batches, town="Letchworth",
                                 weights=self.weights)
        self.assertEqual(merged, [])


if __name__ == "__main__":
    unittest.main()
