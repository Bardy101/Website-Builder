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
        by_name = {b["name"]: b for b in result.businesses}
        # +40 no website, -20 too few reviews, +5 reviewed recently
        self.assertEqual(by_name["New Leaf Physio"]["lead_score"], 25)
        # Still well below the established no-website practice.
        self.assertGreater(
            by_name["Bancroft Physio Rooms"]["lead_score"],
            by_name["New Leaf Physio"]["lead_score"],
        )
        # And the site with nothing wrong with it now ranks last, which is
        # the whole point of the visual-quality amendment.
        self.assertEqual(
            result.businesses[-1]["name"], "Hitchin Osteopathy & Physiotherapy")

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
        # +30 social_only, +15 established, +5 reviewed recently
        self.assertEqual(fb["lead_score"], 50)
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


class TestExcludedList(unittest.TestCase):
    """Every exclusion is written down — the rules tightening must never
    make a business vanish without a trace."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.places = FixturePlacesClient.from_file(FIXTURE)
        self.weights = Weights.load(
            Path(__file__).resolve().parent.parent / "weights.json")

    def tearDown(self):
        self.tmp.cleanup()

    # The one real site in the fixture, rated clean so it trips site_fine.
    FINE_SITE = "https://hitchin-osteo-physio.co.uk"

    def run_discover(self, weights):
        return discover(
            niche="physiotherapist", area="Hitchin",
            places_client=self.places,
            site_checker=StubChecker({self.FINE_SITE: 90}),
            weights=weights, top=25,
        )

    def test_excluded_csv_written_with_reason_and_detail(self):
        import csv

        result = self.run_discover(self.weights)
        self.assertTrue(result.excluded, "fixture should trip at least one rule")
        batch = Batch.create(self.tmp.name, niche="physiotherapist", area="Hitchin")
        write_batch(batch, result)
        self.assertTrue(batch.excluded_path.is_file())
        with batch.excluded_path.open(encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual(len(rows), len(result.excluded))
        for row in rows:
            self.assertTrue(row["name"])
            self.assertTrue(row["reason"])
            self.assertTrue(row["place_id"])
        by_reason = {row["reason"]: row for row in rows}
        # A chain screened before details, a closed business, and the one
        # real site rated clean — each with the detail a human would want.
        self.assertIn("chain_or_franchise", by_reason)
        self.assertIn("non_operational", by_reason)
        self.assertIn("site_fine", by_reason)
        fine = by_reason["site_fine"]
        self.assertEqual(fine["website"], self.FINE_SITE)
        self.assertEqual(fine["site_verdict"], "fine")
        self.assertEqual(fine["detail"], "no staleness signals")

    def test_no_exclusions_no_file(self):
        batch = Batch.create(self.tmp.name, niche="x", area="y")
        from pipeline.discover import DiscoverResult

        write_batch(batch, DiscoverResult(rows=[], businesses=[], excluded=[]))
        self.assertFalse(batch.excluded_path.is_file())

    def test_keeping_fine_sites_restores_them(self):
        loose = Weights.load(
            Path(__file__).resolve().parent.parent / "weights.json")
        loose.exclude["site_fine"] = False
        strict = self.run_discover(self.weights)
        loosened = self.run_discover(loose)
        fine_now_kept = [
            b for b in loosened.businesses
            if (b.get("site_score") or {}).get("verdict") == "fine"
        ]
        self.assertTrue(fine_now_kept)
        self.assertGreater(len(loosened.businesses), len(strict.businesses))


class TestLookupIsolation(unittest.TestCase):
    """Bug 4: one failed lookup took the others down with it, silently."""

    class Boom:
        def check(self, website):
            raise RuntimeError("parser choked on this page")

    class CH:
        def lookup(self, name, postcode):
            return {"owner": {"name": "Jane Doe", "confidence": "high"},
                    "company": {"type": "ltd"}}

    class Contacts:
        def find(self, url):
            return {"contact": {"name": "Jay"}, "emails": []}

    def business(self):
        return {"name": "Odd HTML Clinic", "website": "https://odd.example",
                "address": {"postcode": "SG5 1AA"}, "place_id": "X1",
                "owner": {"name": None}, "company": {"type": "unknown"},
                "site_score": {"verdict": "unknown"}}

    def enrich(self, b, **kw):
        from pipeline.discover import _enrich_all

        args = dict(site_checker=self.Boom(), companies_house=self.CH(),
                    site_contacts=self.Contacts())
        args.update(kw)
        _enrich_all([b], workers=1, say=lambda m: None, **args)

    def test_site_check_failure_does_not_cost_the_owner(self):
        b = self.business()
        self.enrich(b)
        self.assertEqual(b["owner"]["name"], "Jane Doe")
        self.assertEqual(b["site_contact"], {"name": "Jay"})

    def test_failed_step_is_recorded_and_fails_open(self):
        b = self.business()
        self.enrich(b)
        self.assertIn("site check", b["enrich_errors"])
        self.assertIn("parser choked", b["enrich_errors"]["site check"])
        self.assertEqual(b["site_score"]["verdict"], "unknown")

    def test_owner_failure_does_not_cost_the_site_check(self):
        class GoodChecker:
            def check(self, website):
                from pipeline.site_checks import SiteCheck

                c = SiteCheck(True, False, https=True)
                c.verdict = "dated"
                return c

        class BadCH:
            def lookup(self, name, postcode):
                raise ConnectionError("companies house timed out")

        b = self.business()
        self.enrich(b, site_checker=GoodChecker(), companies_house=BadCH())
        self.assertEqual(b["site_score"]["verdict"], "dated")
        self.assertEqual(list(b["enrich_errors"]), ["owner lookup"])

    def test_clean_run_leaves_no_error_record(self):
        class GoodChecker:
            def check(self, website):
                from pipeline.site_checks import SiteCheck

                c = SiteCheck(True, False)
                c.verdict = "fine"
                return c

        b = self.business()
        b["enrich_errors"] = {"site check": "from a previous run"}
        self.enrich(b, site_checker=GoodChecker())
        self.assertNotIn("enrich_errors", b)

    def test_errors_written_to_lookup_errors_csv(self):
        import csv

        from pipeline.discover import DiscoverResult

        b = self.business()
        self.enrich(b)
        with tempfile.TemporaryDirectory() as tmp:
            batch = Batch.create(tmp, niche="x", area="y")
            write_batch(batch, DiscoverResult(rows=[], businesses=[b], excluded=[]))
            path = batch.path / "lookup_errors.csv"
            with path.open(encoding="utf-8") as fh:
                rows = list(csv.DictReader(fh))
            self.assertEqual(rows[0]["step"], "site check")
            self.assertEqual(rows[0]["name"], "Odd HTML Clinic")

    def test_no_failures_no_file(self):
        from pipeline.discover import DiscoverResult

        with tempfile.TemporaryDirectory() as tmp:
            batch = Batch.create(tmp, niche="x", area="y")
            write_batch(batch, DiscoverResult(
                rows=[], businesses=[{"name": "ok", "place_id": "OK1"}], excluded=[]))
            self.assertFalse((batch.path / "lookup_errors.csv").is_file())


class TestBrowserRecheck(unittest.TestCase):
    """Sites that blocked the direct check are measured from the browser's page,
    instead of passing through unjudged as 'unknown'."""

    DATED = ("<html><body><table><tr><td>a</td></tr><tr><td>b</td></tr>"
             "<tr><td>&copy; 2015 Old Clinic</td></tr></table></body></html>")
    MODERN = ("<html><head><meta name='viewport' content='width=device-width'>"
              "<style>@media (max-width:600px){h1{font-size:20px}}</style></head>"
              "<body><h1>New Clinic</h1><p>&copy; 2026</p></body></html>")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        from pipeline.screenshots import ScreenshotCapturer

        self.capturer = ScreenshotCapturer(cache_dir=Path(self.tmp.name))
        self.capturer.root.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def save_page(self, pid, html, url="https://x.example/", media=None):
        import json

        self.capturer.page_path_for(pid).write_text(json.dumps(
            {"url": url, "html": html, "media_queries": media}), encoding="utf-8")

    def unknown(self, pid, website="https://x.example"):
        return {"place_id": pid, "website": website,
                "site_score": {"verdict": "unknown", "mobile_score": 55},
                "staleness": {"fetch_ok": False}}

    def recheck(self, businesses):
        from pipeline.discover import recheck_from_browser

        return recheck_from_browser(businesses, self.capturer)

    def test_dated_site_gets_a_real_verdict(self):
        b = self.unknown("D1")
        self.save_page("D1", self.DATED, url="http://x.example/")
        self.assertEqual(self.recheck([b]), 1)
        self.assertEqual(b["site_score"]["verdict"], "dated")
        self.assertEqual(b["staleness"]["source"], "browser")
        self.assertFalse(b["staleness"]["has_viewport"])
        self.assertFalse(b["site_score"]["https"])
        self.assertEqual(b["site_score"]["mobile_score"], 55)   # untouched

    def test_modern_site_comes_out_fine_and_is_then_excluded(self):
        from pipeline.scoring import Weights, score_business

        b = self.unknown("M1")
        self.save_page("M1", self.MODERN)
        self.recheck([b])
        self.assertEqual(b["site_score"]["verdict"], "fine")
        b.update({"name": "New Clinic", "business_status": "OPERATIONAL",
                  "rating": 4.8, "review_count": 40})
        self.assertEqual(score_business(b, Weights.load(
            Path(__file__).resolve().parent.parent / "weights.json")).exclude_reason,
            "site_fine")

    def test_browser_media_query_answer_is_used(self):
        b = self.unknown("Q1")
        self.save_page("Q1", self.MODERN, media=False)
        self.recheck([b])
        self.assertFalse(b["staleness"]["has_media_queries"])

    def test_unreadable_answer_leaves_the_html_check(self):
        b = self.unknown("Q2")
        self.save_page("Q2", self.MODERN, media=None)
        self.recheck([b])
        self.assertTrue(b["staleness"]["has_media_queries"])

    def test_no_saved_page_stays_unknown(self):
        b = self.unknown("N1")
        self.assertEqual(self.recheck([b]), 0)
        self.assertEqual(b["site_score"]["verdict"], "unknown")

    def test_already_measured_sites_are_left_alone(self):
        b = self.unknown("K1")
        b["staleness"] = {"fetch_ok": True, "has_viewport": True}
        b["site_score"]["verdict"] = "fine"
        self.save_page("K1", self.DATED)
        self.assertEqual(self.recheck([b]), 0)
        self.assertEqual(b["site_score"]["verdict"], "fine")

    def test_social_and_missing_sites_are_skipped(self):
        fb = self.unknown("F1", website="https://facebook.com/clinic")
        none = self.unknown("X1", website=None)
        self.save_page("F1", self.DATED)
        self.assertEqual(self.recheck([fb, none]), 0)

    def test_corrupt_page_file_is_ignored(self):
        b = self.unknown("C1")
        self.capturer.page_path_for("C1").write_text("{ not json", encoding="utf-8")
        self.assertEqual(self.recheck([b]), 0)
