"""Tests for fixture coverage reporting and the launcher's helpers."""

import json
import tempfile
import unittest
from pathlib import Path

from pipeline.fixtures import FixturePlacesClient

FIXTURE = Path(__file__).resolve().parent.parent / "examples" / "sample_fixture.json"


class TestFixtureCoverage(unittest.TestCase):
    def test_reports_what_it_covers(self):
        client = FixturePlacesClient.from_file(FIXTURE)
        self.assertEqual(client.available(), [("physiotherapist", "Hitchin")])

    def test_covers_is_case_insensitive(self):
        client = FixturePlacesClient.from_file(FIXTURE)
        self.assertTrue(client.covers("Physiotherapist", "hitchin"))
        self.assertTrue(client.covers("physiotherapist", "Hitchin"))

    def test_does_not_cover_other_niches(self):
        """The bug this guards: a plumber search silently returned nothing."""
        client = FixturePlacesClient.from_file(FIXTURE)
        self.assertFalse(client.covers("plumber", "Hitchin"))
        self.assertFalse(client.covers("physiotherapist", "Stevenage"))

    def test_untagged_fixture_matches_everything(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "f.json"
            path.write_text(json.dumps({"places": [{"id": "x"}]}), encoding="utf-8")
            client = FixturePlacesClient.from_file(path)
            self.assertEqual(client.available(), [])
            self.assertTrue(client.covers("anything", "anywhere"))


class TestFindRejectsUncoveredFixtureNiche(unittest.TestCase):
    def test_exits_with_a_useful_message(self):
        import find

        with tempfile.TemporaryDirectory() as tmp:
            import os

            os.environ["PIPELINE_BATCHES_DIR"] = tmp
            try:
                with self.assertRaises(SystemExit) as ctx:
                    find.main([
                        "--niche", "plumber", "--area", "Hitchin",
                        "--fixture", str(FIXTURE), "--quiet",
                    ])
            finally:
                os.environ.pop("PIPELINE_BATCHES_DIR", None)
        message = str(ctx.exception)
        self.assertIn("demo fixture has no 'plumber'", message)
        self.assertIn("physiotherapist", message)
        self.assertIn("GOOGLE_PLACES_API_KEY", message)


class TestLauncherHelpers(unittest.TestCase):
    def test_imports_without_side_effects(self):
        import run

        self.assertTrue(run.FIXTURE.is_file())
        self.assertTrue(any(key == "1" for key, _, _ in run.MENU))

    def test_menu_keys_are_unique(self):
        import run

        keys = [key for key, _, _ in run.MENU]
        self.assertEqual(len(keys), len(set(keys)))

    def test_list_batches_ignores_folders_without_a_shortlist(self):
        import os

        import run

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "empty_batch").mkdir()
            real = Path(tmp) / "real_batch"
            real.mkdir()
            (real / "shortlist.csv").write_text("lead_score\n", encoding="utf-8")
            os.environ["PIPELINE_BATCHES_DIR"] = tmp
            try:
                found = [p.name for p in run.list_batches()]
            finally:
                os.environ.pop("PIPELINE_BATCHES_DIR", None)
        self.assertEqual(found, ["real_batch"])


if __name__ == "__main__":
    unittest.main()
