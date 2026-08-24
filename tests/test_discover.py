"""End-to-end tests for the discover stage, run offline against the fixture."""

import tempfile
import unittest
from pathlib import Path

from pipeline.discover import discover, merge_results, write_batch
from pipeline.fixtures import FixturePlacesClient
from pipeline.scoring import Weights
from pipeline.storage import SHORTLIST_COLUMNS, Batch

FIXTURE = Path(__file__).resolve().parent.parent / "examples" / "sample_fixture.json"


class StubChecker:
    """Site checker with canned verdicts, keyed by URL substring."""

    def __init__(self, mapping=None):
        self.mapping = mapping or {}

    def check(self, website):
        from pipeline.site_checks import SiteCheck, classify_url

        kind = classify_url(website)
        if kind == "none":
            c = SiteCheck(False, False)
            c.verdict = "none"
            return c
        if kind in ("social", "directory"):
            c = SiteCheck(True, True)
            c.verdict = "social_only"
            return c
        score = self.mapping.get(website, 45)
        c = SiteCheck(True, False, https=True, viewport=True, mobile_score=score)
        c.verdict = "poor" if score < 50 else ("dated" if score < 70 else "fine")
        return c


class TestDiscover(unittest.TestCase):
    def setUp(self):
        self.places = FixturePlacesClient.from_file(FIXTURE)
        self.weights = Weights.load(
            Path(__file__).resolve().parent.parent / "weights.json"
        )

    def test_ranks_worst_web_presence_first(self):
        result = discover(
            niche="physiotherapist",
            area="Hitchin",
            places_client=self.places,
            site_checker=StubChecker(),
            weights=self.weights,
            top=25,
        )
        names = [b["name"] for b in result.businesses]
        # No website + established beats a Facebook-only site, which beats a real site.
        self.assertEqual(names[0], "Bancroft Physio Rooms")
        self.assertIn("Walsworth Road Sports Injury Clinic", names)
        scores = [b["lead_score"] for b in result.businesses]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_excludes_chain_and_closed(self):
        result = discover(
            niche="physiotherapist",
            area="Hitchin",
            places_client=self.places,
            site_checker=StubChecker(),
            weights=self.weights,
            top=25,
        )
        names = [b["name"] for b in result.businesses]
        self.assertNotIn("Nuffield Health Hitchin Physiotherapy", names)
        self.assertNotIn("Old Mill Physio (closed)", names)
        reasons = {b["excluded"] for b in result.excluded}
        self.assertEqual(reasons, {"chain_or_franchise", "non_operational"})

    def test_new_business_penalised_but_kept(self):
        result = discover(
            niche="physiotherapist",
            area="Hitchin",
            places_client=self.places,
            site_checker=StubChecker(),
            weights=self.weights,
            top=25,
        )
        new_leaf = next(b for b in result.businesses if b["name"] == "New Leaf Physio")
        # +40 no website, -20 too few reviews
        self.assertEqual(new_leaf["lead_score"], 20)
        self.assertEqual(result.businesses[-1]["name"], "New Leaf Physio")

    def test_top_limits_rows(self):
        result = discover(
            niche="physiotherapist",
            area="Hitchin",
            places_client=self.places,
            site_checker=StubChecker(),
            weights=self.weights,
            top=2,
        )
        self.assertEqual(len(result.businesses), 2)
        self.assertEqual(len(result.rows), 2)

    def test_rows_have_every_column(self):
        result = discover(
            niche="physiotherapist",
            area="Hitchin",
            places_client=self.places,
            site_checker=StubChecker(),
            weights=self.weights,
        )
        for row in result.rows:
            self.assertEqual(set(row.keys()), set(SHORTLIST_COLUMNS))

    def test_url_classification_survives_no_site_checker(self):
        """--no-site-checks must still spot a Facebook-only 'site'.

        Classifying the URL needs no network, so the social_only signal and
        the site_verdict column stay populated even with checks disabled.
        """
        result = discover(
            niche="physiotherapist",
            area="Hitchin",
            places_client=self.places,
            site_checker=None,
            weights=self.weights,
        )
        by_name = {b["name"]: b for b in result.businesses}
        fb = by_name["Walsworth Road Sports Injury Clinic"]
        self.assertEqual(fb["site_score"]["verdict"], "social_only")
        # +30 social_only, +15 established
        self.assertEqual(fb["lead_score"], 45)
        self.assertEqual(by_name["Bancroft Physio Rooms"]["site_score"]["verdict"], "none")
        # A real site we could not check gets no penalty — honest, not guessed.
        real = by_name["Hitchin Osteopathy & Physiotherapy"]
        self.assertEqual(real["site_score"]["verdict"], "unknown")
        self.assertIsNone(real["site_score"]["mobile_score"])

    def test_no_owner_lookup_leaves_null(self):
        result = discover(
            niche="physiotherapist",
            area="Hitchin",
            places_client=self.places,
            site_checker=StubChecker(),
            weights=self.weights,
            companies_house=None,
        )
        self.assertTrue(all(b["owner"]["name"] is None for b in result.businesses))

    def test_write_batch_persists_csv_and_json(self):
        result = discover(
            niche="physiotherapist",
            area="Hitchin",
            places_client=self.places,
            site_checker=StubChecker(),
            weights=self.weights,
        )
        with tempfile.TemporaryDirectory() as tmp:
            batch = Batch.create(tmp, niche="physio", area="hitchin")
            write_batch(batch, result)
            self.assertTrue(batch.shortlist_path.is_file())
            rows = batch.read_shortlist()
            self.assertEqual(len(rows), len(result.businesses))
            first = result.businesses[0]["place_id"]
            self.assertIsNotNone(batch.read_business(first))
            self.assertEqual(
                batch.read_meta()["status_counts"]["shortlisted"], len(result.businesses)
            )


class TestMerge(unittest.TestCase):
    def test_dedupes_across_runs_keeping_higher_score(self):
        places = FixturePlacesClient.from_file(FIXTURE)
        weights = Weights.load(Path(__file__).resolve().parent.parent / "weights.json")
        seen = set()
        a = discover(
            niche="physiotherapist", area="Hitchin", places_client=places,
            site_checker=StubChecker(), weights=weights, seen_place_ids=seen,
        )
        b = discover(
            niche="physiotherapist", area="Hitchin", places_client=places,
            site_checker=StubChecker(), weights=weights, seen_place_ids=seen,
        )
        # The shared seen-set means the second run yields nothing new.
        self.assertEqual(len(b.businesses), 0)
        merged = merge_results([a, b])
        ids = [x["place_id"] for x in merged.businesses]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(ids), len(a.businesses))


if __name__ == "__main__":
    unittest.main()
