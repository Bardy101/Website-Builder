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



class TestEnvSetup(unittest.TestCase):
    """The setup menu writes .env; it must never lose an existing key."""

    def _with_env(self, content, fn):
        import run

        env_path = run.HERE / ".env"
        backup = env_path.read_text(encoding="utf-8") if env_path.is_file() else None
        try:
            if content is None:
                env_path.unlink(missing_ok=True)
            else:
                env_path.write_text(content, encoding="utf-8")
            return fn(run)
        finally:
            if backup is None:
                env_path.unlink(missing_ok=True)
            else:
                env_path.write_text(backup, encoding="utf-8")

    def test_reads_existing_values(self):
        values = self._with_env(
            "# comment\nGOOGLE_PLACES_API_KEY=abc123\n\nPAGESPEED_API_KEY=\n",
            lambda run: run.read_existing_env(),
        )
        self.assertEqual(values["GOOGLE_PLACES_API_KEY"], "abc123")
        self.assertEqual(values["PAGESPEED_API_KEY"], "")

    def test_missing_env_reads_as_empty(self):
        self.assertEqual(self._with_env(None, lambda run: run.read_existing_env()), {})

    def test_write_then_read_roundtrips_every_key(self):
        def go(run):
            run.write_env({
                "GOOGLE_PLACES_API_KEY": "places-key",
                "PAGESPEED_API_KEY": "speed-key",
                "COMPANIES_HOUSE_API_KEY": "ch-key",
            })
            return run.read_existing_env()

        values = self._with_env(None, go)
        self.assertEqual(values["GOOGLE_PLACES_API_KEY"], "places-key")
        self.assertEqual(values["PAGESPEED_API_KEY"], "speed-key")
        self.assertEqual(values["COMPANIES_HOUSE_API_KEY"], "ch-key")

    def test_writing_one_key_preserves_the_others(self):
        def go(run):
            existing = run.read_existing_env()
            existing["PAGESPEED_API_KEY"] = "new-speed-key"
            run.write_env(existing)
            return run.read_existing_env()

        values = self._with_env("GOOGLE_PLACES_API_KEY=keep-me\n", go)
        self.assertEqual(values["GOOGLE_PLACES_API_KEY"], "keep-me")
        self.assertEqual(values["PAGESPEED_API_KEY"], "new-speed-key")

    def test_quoted_values_are_unquoted(self):
        values = self._with_env(
            'GOOGLE_PLACES_API_KEY="quoted-key"\n',
            lambda run: run.read_existing_env(),
        )
        self.assertEqual(values["GOOGLE_PLACES_API_KEY"], "quoted-key")



class TestMenuStructure(unittest.TestCase):
    """Setup lives in its own submenu; the main menu is the daily workflow."""

    def test_setup_actions_are_not_on_the_main_menu(self):
        import run

        main_actions = {fn for _, _, fn in run.MENU}
        for setup_fn in (run.action_check, run.action_keys, run.action_test_keys):
            self.assertNotIn(setup_fn, main_actions)

    def test_config_submenu_holds_the_three_setup_actions(self):
        import run

        config_actions = [fn for _, _, fn in run.CONFIG_MENU]
        self.assertEqual(
            config_actions, [run.action_check, run.action_keys, run.action_test_keys]
        )

    def test_config_submenu_keys_are_unique(self):
        import run

        keys = [key for key, _, _ in run.CONFIG_MENU]
        self.assertEqual(len(keys), len(set(keys)))

    def test_submenu_action_paces_itself(self):
        import run

        self.assertTrue(getattr(run.action_config, "handles_own_pause", False))

    def test_main_menu_covers_the_workflow(self):
        import run

        labels = " ".join(label.lower() for _, label, _ in run.MENU)
        for word in ("find", "review", "combine", "open", "tune", "setup"):
            self.assertIn(word, labels)


class TestPairsConfig(unittest.TestCase):
    """The menu writes the multi-pair YAML so nobody authors one by hand."""

    def test_yaml_round_trips_through_find(self):
        import find
        import run

        path = run.write_pairs_config([
            ("physiotherapist", "Hitchin"),
            ("accountant", "Stevenage, Hertfordshire"),
        ])
        try:
            cfg = find.load_batch_config(str(path))
            self.assertEqual(
                [(p["niche"], p["area"]) for p in cfg["pairs"]],
                [("physiotherapist", "Hitchin"),
                 ("accountant", "Stevenage, Hertfordshire")],
            )
            self.assertEqual(cfg["label"], "physiotherapist-accountant")
        finally:
            path.unlink()

    def test_duplicate_niches_appear_once_in_the_label(self):
        import run

        path = run.write_pairs_config([
            ("physiotherapist", "Hitchin"),
            ("physiotherapist", "Stevenage"),
        ])
        try:
            self.assertIn("label: physiotherapist\n", path.read_text(encoding="utf-8"))
        finally:
            path.unlink()


if __name__ == "__main__":
    unittest.main()
