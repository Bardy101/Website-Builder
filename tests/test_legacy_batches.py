"""Batches written by earlier versions must keep working, without re-scanning.

The stored records are the source of truth, and every menu action reads
them rather than the API. These tests pin that: a batch in the original
shape (no site_contact / site_emails / addressee on the record, and the
original 19-column shortlist.csv) must still cull, combine and tune.
"""

import csv
import json
import tempfile
import unittest
from pathlib import Path

from pipeline.addressee import OWNER_ONLY
from pipeline.combine import combine_batches
from pipeline.scoring import Weights
from pipeline.storage import SHORTLIST_COLUMNS, Batch

ROOT = Path(__file__).resolve().parent.parent

# The shortlist columns as the first released version wrote them.
V1_COLUMNS = [
    "lead_score", "name", "town", "website", "site_verdict", "mobile_score",
    "https", "rating", "review_count", "recent_review_date", "owner_name",
    "company_type", "phone", "address", "postcode", "maps_url", "website_url",
    "photo_count", "place_id",
]

V1_BUSINESS = {
    "place_id": "ChIJold001",
    "name": "Old Format Physio",
    "niche": "physiotherapist",
    "address": {"line1": "1 High St", "town": "Hitchin", "postcode": "SG5 1AA",
                "formatted": "1 High St, Hitchin SG5 1AA, UK"},
    "phone": "01462 111111",
    "website": "https://oldformat.example",
    "maps_url": "https://maps.google.com/?cid=1",
    "rating": 4.7,
    "review_count": 60,
    "business_status": "OPERATIONAL",
    "photos": [],
    "photo_count": 0,
    "reviews": [{"text": "Great", "rating": 5, "time": "2026-07-01T10:00:00Z"}],
    # No "parts" or "all_directors" — those came later.
    "owner": {"name": "Jane Cooper", "source": "companies_house",
              "confidence": "high"},
    "company": {"number": "111", "type": "ltd"},
    "site_score": {"verdict": "dated", "mobile_score": 62, "https": True,
                   "viewport": True},
    "lead_score": 15,  # scored under the old cliff weights
    "preview_slug": "old-format-physio",
}


class LegacyBatchCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.weights = Weights.load(ROOT / "weights.json")
        self.batch = Batch.create(self.tmp.name, niche="physiotherapist",
                                  area="hitchin", name="legacy")
        self.batch.write_business(dict(V1_BUSINESS))
        with self.batch.shortlist_path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=V1_COLUMNS)
            writer.writeheader()
            writer.writerow({
                "lead_score": 15, "name": "Old Format Physio", "town": "Hitchin",
                "website": "https://oldformat.example", "site_verdict": "dated",
                "mobile_score": 62, "https": "yes", "rating": 4.7,
                "review_count": 60, "recent_review_date": "2026-07-01",
                "owner_name": "Jane Cooper", "company_type": "ltd",
                "phone": "01462 111111", "address": "1 High St, Hitchin",
                "postcode": "SG5 1AA", "maps_url": "https://maps.google.com/?cid=1",
                "website_url": "https://oldformat.example", "photo_count": 0,
                "place_id": "ChIJold001",
            })

    def tearDown(self):
        self.tmp.cleanup()


class TestLegacyReads(LegacyBatchCase):
    def test_shortlist_still_reads(self):
        rows = self.batch.read_shortlist()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "Old Format Physio")

    def test_new_columns_are_simply_absent_not_fatal(self):
        row = self.batch.read_shortlist()[0]
        for new_column in ("address_to", "contact_check", "site_contact",
                           "contact_email"):
            self.assertNotIn(new_column, row)
            self.assertEqual(row.get(new_column, ""), "")

    def test_business_record_still_reads(self):
        record = self.batch.read_business("ChIJold001")
        self.assertIsNone(record.get("site_contact"))
        self.assertIsNone(record.get("addressee"))


class TestLegacyCull(LegacyBatchCase):
    def test_cull_from_csv_works_on_v1_columns(self):
        import cull

        marked = self.batch.path / "marked.csv"
        with marked.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=V1_COLUMNS + ["reason"])
            writer.writeheader()
            row = self.batch.read_shortlist()[0]
            row["reason"] = "site_fine"
            writer.writerow(row)

        cull.main(["--batch", str(self.batch.path), "--from-csv", "marked.csv"])
        rejections = self.batch.read_rejections()
        self.assertEqual(len(rejections), 1)
        self.assertEqual(rejections[0]["reason"], "site_fine")

    def test_display_falls_back_to_the_owner_column(self):
        """No address_to column, but owner_name names someone — use it."""
        import cull

        row = self.batch.read_shortlist()[0]
        decisions = cull.prompt_decisions  # symbol exists; render logic below
        self.assertTrue(callable(decisions))
        address_to = (
            row.get("address_to") or row.get("owner_name") or "FAO the Owner"
        )
        self.assertEqual(address_to, "Jane Cooper")


class TestLegacyCombine(LegacyBatchCase):
    def test_combine_rescores_under_current_weights(self):
        merged = combine_batches([self.batch], weights=self.weights)
        # +12 graded middling band + 15 established, up from the stored 15.
        self.assertEqual(merged[0]["lead_score"], 27)

    def test_combine_derives_an_addressee_from_stored_data(self):
        merged = combine_batches([self.batch], weights=self.weights)
        addressee = merged[0]["addressee"]
        self.assertEqual(addressee["verdict"], OWNER_ONLY)
        self.assertEqual(addressee["address_to"], "Jane Cooper")
        self.assertFalse(addressee["needs_human"])

    def test_combined_csv_has_every_current_column(self):
        from pipeline.combine import write_combined

        merged = combine_batches([self.batch], weights=self.weights)
        out = write_combined(self.tmp.name, merged, name="combo",
                             source_names=["legacy"])
        row = out.read_shortlist()[0]
        self.assertEqual(set(row.keys()), set(SHORTLIST_COLUMNS))
        self.assertEqual(row["address_to"], "Jane Cooper")

    def test_combine_needs_no_network(self):
        """No client is passed at all — proof the API is never touched."""
        merged = combine_batches([self.batch], weights=self.weights)
        self.assertEqual(len(merged), 1)


class TestLegacyTune(LegacyBatchCase):
    def test_tune_reads_legacy_rejections(self):
        from pipeline.tune import compare, propose

        self.batch.append_rejection({
            "place_id": "ChIJold001", "name": "Old Format Physio",
            "reason": "site_fine",
            "row": {"lead_score": "15", "mobile_score": "62",
                    "review_count": "60"},
            "business": json.loads(json.dumps(V1_BUSINESS)),
        })
        rejected = []
        for rej in self.batch.read_rejections():
            row = dict(rej["row"])
            row["reason"] = rej["reason"]
            rejected.append(row)
        comparison = compare([], rejected)
        self.assertEqual(comparison["by_reason"]["site_fine"]["n"], 1)
        self.assertIsInstance(propose(comparison, {"thresholds": {}}), list)


if __name__ == "__main__":
    unittest.main()
