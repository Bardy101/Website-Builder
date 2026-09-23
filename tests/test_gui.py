"""Tests for the window's command builders.

The widgets only collect values; these functions turn them into the exact
command lines the CLIs get. They import without tkinter, so this runs on
any interpreter — the window itself is checked by rendering it under Xvfb.
"""

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import gui
from pipeline.history import PastSearch
from pipeline.storage import Batch, business_to_row


class TestFindCommand(unittest.TestCase):
    def test_single_pair_defaults(self):
        argv = gui.find_command([("physiotherapist", "Hitchin")], radius=8000, top=25)
        self.assertEqual(argv[:5], ["find.py", "--niche", "physiotherapist", "--area", "Hitchin"])
        self.assertIn("--from-menu", argv)
        for flag in ("--no-screenshots", "--keep-dormant", "--keep-fine", "--refresh"):
            self.assertNotIn(flag, argv)

    def test_options_map_to_flags(self):
        argv = gui.find_command(
            [("plumber", "Stevenage")], radius=5000, top=40,
            screenshots=False, site_checks=False, owner_lookup=False,
            site_contacts=False, keep_dormant=True, keep_fine=True,
            dormant_days=180, refresh=True, fixture=Path("f.json"))
        for flag in ("--no-screenshots", "--no-site-checks", "--no-owner-lookup",
                     "--no-site-contacts", "--keep-dormant", "--keep-fine", "--refresh"):
            self.assertIn(flag, argv)
        self.assertEqual(argv[argv.index("--dormant-days") + 1], "180")
        self.assertEqual(argv[argv.index("--fixture") + 1], "f.json")
        self.assertEqual(argv[argv.index("--radius") + 1], "5000")
        self.assertEqual(argv[argv.index("--top") + 1], "40")

    def test_several_pairs_go_through_a_config(self):
        argv = gui.find_command(
            [("a", "x"), ("b", "y")], radius=8000, top=25, config_path=Path("pairs.yaml"))
        self.assertEqual(argv[:3], ["find.py", "--batch-config", "pairs.yaml"])
        self.assertNotIn("--niche", argv)

    def test_several_pairs_without_a_config_is_an_error(self):
        with self.assertRaises(ValueError):
            gui.find_command([("a", "x"), ("b", "y")], radius=8000, top=25)

    def test_no_pairs_is_an_error(self):
        with self.assertRaises(ValueError):
            gui.find_command([], radius=8000, top=25)


class TestOtherCommands(unittest.TestCase):
    def test_contactsheet(self):
        self.assertEqual(gui.contactsheet_command(Path("b")), ["contactsheet.py", "--batch", "b"])
        self.assertIn("--approved", gui.contactsheet_command(Path("b"), approved_only=True))

    def test_cull_import_and_start_over(self):
        argv = gui.cull_import_command(Path("b"), Path("d.json"))
        self.assertEqual(argv, ["cull.py", "--batch", "b", "--import", "d.json"])
        self.assertIn("--full", gui.cull_import_command(Path("b"), Path("d.json"), start_over=True))

    def test_cull_from_csv(self):
        argv = gui.cull_csv_command(Path("b"), "approved.csv")
        self.assertEqual(argv, ["cull.py", "--batch", "b", "--from-csv", "approved.csv"])

    def test_combine_filters_and_full(self):
        argv = gui.combine_command([Path("b1"), Path("b2")], niche="physio", town=" Hitchin ",
                                   name="all", use_culls=False)
        self.assertEqual(argv[:3], ["combine.py", "b1", "b2"])
        self.assertEqual(argv[argv.index("--niche") + 1], "physio")
        self.assertEqual(argv[argv.index("--town") + 1], "Hitchin")
        self.assertEqual(argv[argv.index("--name") + 1], "all")
        self.assertIn("--full", argv)
        self.assertNotIn("--full", gui.combine_command([Path("b1")]))
        self.assertNotIn("--niche", gui.combine_command([Path("b1")]))

    def test_tune_and_setup(self):
        self.assertEqual(gui.tune_command([Path("b1"), Path("b2")]),
                         ["tune.py", "--batch", "b1", "b2"])
        self.assertEqual(gui.check_setup_command()[0], "-c")
        self.assertIn("action_test_keys", gui.test_keys_command()[1])


class TestCostNote(unittest.TestCase):
    def setUp(self):
        now = datetime.now(timezone.utc)
        self.history = [PastSearch("physiotherapist", "Hitchin", 8000, now - timedelta(days=3), 30)]

    def test_exact_match_is_free(self):
        free, msg = gui.cost_note([("physiotherapist", "Hitchin")], 8000, self.history, refresh=False)
        self.assertTrue(free)
        self.assertIn("cache", msg)

    def test_case_is_ignored_but_wording_is_not(self):
        free, _ = gui.cost_note([("Physiotherapist", "hitchin")], 8000, self.history, refresh=False)
        self.assertTrue(free)
        free, _ = gui.cost_note([("physiotherapist", "Hitchin, Hertfordshire")], 8000,
                                self.history, refresh=False)
        self.assertFalse(free)

    def test_different_radius_bills(self):
        free, _ = gui.cost_note([("physiotherapist", "Hitchin")], 10000, self.history, refresh=False)
        self.assertFalse(free)

    def test_refresh_always_bills(self):
        free, msg = gui.cost_note([("physiotherapist", "Hitchin")], 8000, self.history, refresh=True)
        self.assertFalse(free)
        self.assertIn("bills", msg)

    def test_mixed_pairs_counted(self):
        free, msg = gui.cost_note([("physiotherapist", "Hitchin"), ("plumber", "Hitchin")],
                                  8000, self.history, refresh=False)
        self.assertFalse(free)
        self.assertIn("1 of 2", msg)


class TestBatchSummary(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def rec(self, pid, name):
        return {"place_id": pid, "name": name, "niche": "x", "business_status": "OPERATIONAL",
                "address": {"town": "T"}, "website": None, "rating": 4.5, "review_count": 10,
                "site_score": None, "owner": {}, "company": {}, "lead_score": 0}

    def test_found_only(self):
        b = Batch.create(self.tmp.name, niche="x", area="y", name="b")
        for pid in ("P1", "P2", "P3"):
            b.write_business(self.rec(pid, pid))
        b.write_shortlist(business_to_row(b.read_business(p)) for p in ("P1", "P2", "P3"))
        s = gui.batch_summary(b.path)
        self.assertEqual((s["found"], s["approved"], s["excluded"], s["status"]), (3, None, 0, "found"))

    def test_culled_and_excluded_counts(self):
        b = Batch.create(self.tmp.name, niche="x", area="y", name="b")
        for pid in ("P1", "P2"):
            b.write_business(self.rec(pid, pid))
        b.write_shortlist(business_to_row(b.read_business(p)) for p in ("P1", "P2"))
        b.write_shortlist([business_to_row(b.read_business("P1"))], path=b.approved_path)
        b.excluded_path.write_text("name,reason\nA,dormant\nB,site_fine\n", encoding="utf-8")
        s = gui.batch_summary(b.path)
        self.assertEqual((s["found"], s["approved"], s["excluded"], s["status"]), (2, 1, 2, "culled"))

    def test_combined_status(self):
        b = Batch.create(self.tmp.name, niche="x", area="y", name="c")
        b.write_shortlist([])
        b.write_meta({**b.read_meta(), "combined_from": ["a", "b"]})
        self.assertEqual(gui.batch_summary(b.path)["status"], "combined")


if __name__ == "__main__":
    unittest.main()


class TestPairsForRun(unittest.TestCase):
    """Bug 8: once the run list had entries, what was typed was ignored."""

    def test_no_list_uses_the_typed_pair(self):
        self.assertEqual(gui.pairs_for_run([], ("physio", "Hitchin")),
                         ([("physio", "Hitchin")], False))

    def test_nothing_typed_nothing_listed(self):
        self.assertEqual(gui.pairs_for_run([], ("", "Hitchin")), ([], False))

    def test_typed_pair_not_in_list_is_flagged(self):
        pairs, left_out = gui.pairs_for_run([("physio", "Hitchin")], ("plumber", "Stevenage"))
        self.assertEqual(pairs, [("physio", "Hitchin")])
        self.assertTrue(left_out)

    def test_typed_pair_already_listed_is_not_flagged(self):
        _, left_out = gui.pairs_for_run([("physio", "Hitchin")], ("Physio", "hitchin"))
        self.assertFalse(left_out)

    def test_blank_fields_with_a_list_are_not_flagged(self):
        _, left_out = gui.pairs_for_run([("physio", "Hitchin")], ("", ""))
        self.assertFalse(left_out)


class TestHistoryRespectsTtl(unittest.TestCase):
    """Bug 9: the "free to repeat" list assumed a 30-day cache."""

    def test_search_older_than_a_shortened_ttl_is_not_offered(self):
        from pipeline.cache import Cache
        from pipeline.history import past_searches

        with tempfile.TemporaryDirectory() as tmp:
            cache = Cache(tmp)
            cache.set("places_search", "physio in Hitchin|r=8000",
                      {"places": [{"id": "a"}], "niche": "physio",
                       "area": "Hitchin", "radius_m": 8000})
            path = next((Path(tmp) / "places_search").glob("*.json"))
            import json

            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["fetched"] = (datetime.now(timezone.utc)
                                  - timedelta(days=10)).isoformat()
            path.write_text(json.dumps(payload), encoding="utf-8")

            self.assertEqual(len(past_searches(tmp, ttl_days=30)), 1)
            self.assertEqual(past_searches(tmp, ttl_days=7), [])


class TestStopKillsTheWholeTree(unittest.TestCase):
    """Bug 10: Stop killed Python but left the browser running."""

    def test_descendants_from_a_snapshot(self):
        table = [(10, 1), (11, 10), (12, 11), (13, 10), (99, 1)]
        self.assertEqual(sorted(gui.descendants(10, table)), [11, 12, 13])
        self.assertEqual(gui.descendants(99, table), [])

    @unittest.skipIf(os.name == "nt", "the POSIX path; Windows uses taskkill /T")
    def test_detached_grandchild_is_killed(self):
        """Mimics Playwright exactly: the browser is spawned detached, in its
        own session, so a process-group kill cannot reach it."""
        import subprocess
        import sys
        import time

        with tempfile.TemporaryDirectory() as tmp:
            pidfile = Path(tmp) / "grandchild.pid"
            script = (
                "import subprocess, sys, time\n"
                "p = subprocess.Popen(['sleep', '300'], start_new_session=True)\n"
                f"open({str(pidfile)!r}, 'w').write(str(p.pid))\n"
                "time.sleep(300)\n"
            )
            done = []
            runner = gui.Runner(lambda line: None, done.append)
            runner.start(["-c", script])
            for _ in range(100):
                if pidfile.is_file() and pidfile.read_text():
                    break
                time.sleep(0.05)
            grandchild = int(pidfile.read_text())
            self.assertTrue(gui._alive(grandchild))

            runner.stop()
            for _ in range(60):
                if not gui._alive(grandchild):
                    break
                time.sleep(0.05)
            self.assertFalse(gui._alive(grandchild), "detached browser survived Stop")
            self.assertFalse(runner.busy)


class TestPresentation(unittest.TestCase):
    """The small pure helpers the window's look depends on."""

    def test_status_text(self):
        self.assertEqual(gui.status_text("keep", ""), "✓ kept")
        self.assertEqual(gui.status_text("cull", "site_fine"), "✗ site fine")
        self.assertEqual(gui.status_text("cull", ""), "✗ culled")
        self.assertEqual(gui.status_text("", ""), "")

    def test_excluded_reasons_are_short(self):
        self.assertEqual(gui.status_text("", "chain_or_franchise", excluded=True), "⊘ chain")
        self.assertEqual(gui.status_text("", "non_operational", excluded=True), "⊘ closed")
        self.assertEqual(gui.status_text("", "dormant", excluded=True), "⊘ dormant")

    def test_log_tags(self):
        self.assertEqual(gui.log_tag("Traceback (most recent call last):"), ("err",))
        self.assertEqual(gui.log_tag("  site check failed for Odd Clinic"), ("err",))
        self.assertEqual(gui.log_tag("[finished with exit code 1]"), ("err",))
        self.assertEqual(gui.log_tag("[finished with exit code 0]"), ("ok",))
        self.assertEqual(gui.log_tag("b1: saved 4 approved, 2 rejected"), ("ok",))
        self.assertEqual(gui.log_tag("Searching Places for 'physio'…"), ())


class TestTuningCommands(unittest.TestCase):
    def test_min_site_points_flag(self):
        argv = gui.find_command([("physio", "Hitchin")], radius=8000, top=25,
                                min_site_points=35)
        self.assertEqual(argv[argv.index("--min-site-points") + 1], "35")
        self.assertNotIn("--min-site-points",
                         gui.find_command([("physio", "Hitchin")], radius=8000, top=25))

    def test_min_site_points_zero_is_passed(self):
        # 0 is a real choice (keep any flawed site), not "unset".
        argv = gui.find_command([("physio", "Hitchin")], radius=8000, top=25,
                                min_site_points=0)
        self.assertIn("--min-site-points", argv)

    def test_tune_apply(self):
        self.assertEqual(gui.tune_command([Path("b1")], apply=True),
                         ["tune.py", "--batch", "b1", "--apply"])
        self.assertNotIn("--apply", gui.tune_command([Path("b1")]))
