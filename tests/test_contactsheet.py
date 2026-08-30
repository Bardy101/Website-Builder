"""Tests for the contact sheet and its decision round-trip (brief part 2)."""

import json
import tempfile
import unittest
from pathlib import Path

from pipeline.contactsheet import build_cells, encode_thumbnail, render
from pipeline.cull import decisions_from_export
from pipeline.screenshots import ScreenshotCapturer, ShotResult


def png(path: Path, size=(390, 844)):
    from PIL import Image

    Image.new("RGB", size, (40, 90, 160)).save(path)
    return path


class TestThumbnails(unittest.TestCase):
    def test_encodes_a_data_uri(self):
        with tempfile.TemporaryDirectory() as tmp:
            shot = png(Path(tmp) / "a.png")
            uri = encode_thumbnail(str(shot))
            self.assertTrue(uri.startswith("data:image/jpeg;base64,"))

    def test_missing_file_is_none_not_an_error(self):
        self.assertIsNone(encode_thumbnail("/nope/missing.png"))
        self.assertIsNone(encode_thumbnail(""))
        self.assertIsNone(encode_thumbnail(None))

    def test_corrupt_image_is_none_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.png"
            bad.write_text("not an image", encoding="utf-8")
            self.assertIsNone(encode_thumbnail(str(bad)))


class TestCells(unittest.TestCase):
    def rows(self):
        return [
            {"place_id": "a", "name": "Alpha", "town": "Hitchin", "lead_score": "40",
             "site_verdict": "dated", "staleness_points": "40", "website_url": "https://a",
             "screenshot_path": ""},
            {"place_id": "b", "name": "Bravo", "town": "Hitchin", "lead_score": "90",
             "site_verdict": "poor", "staleness_points": "15", "website_url": "https://b",
             "screenshot_path": ""},
            {"place_id": "c", "name": "Charlie", "town": "Baldock", "lead_score": "5",
             "site_verdict": "none", "staleness_points": "0", "website_url": "",
             "screenshot_path": ""},
        ]

    def test_sorted_by_score_descending_by_default(self):
        cells = build_cells(self.rows(), {})
        self.assertEqual([c["name"] for c in cells], ["Bravo", "Alpha", "Charlie"])

    def test_sort_by_name(self):
        cells = build_cells(self.rows(), {}, sort="name")
        self.assertEqual([c["name"] for c in cells], ["Alpha", "Bravo", "Charlie"])

    def test_min_score_filters(self):
        cells = build_cells(self.rows(), {}, min_score=40)
        self.assertEqual([c["name"] for c in cells], ["Bravo", "Alpha"])

    def test_every_candidate_appears_including_no_website(self):
        """Criterion 3: the sheet shows every candidate."""
        self.assertEqual(len(build_cells(self.rows(), {})), 3)


class TestRender(unittest.TestCase):
    def test_self_contained_with_no_external_assets(self):
        cells = build_cells(
            [{"place_id": "a", "name": "Alpha", "town": "Hitchin", "lead_score": "40",
              "site_verdict": "dated", "staleness_points": "40",
              "website_url": "https://alpha.example", "screenshot_path": ""}], {})
        html = render(cells, batch_name="b1")
        self.assertNotIn("http://cdn", html)
        self.assertNotIn("<script src=", html)
        self.assertNotIn('<link rel="stylesheet"', html)

    def test_business_names_cannot_break_the_page(self):
        """Names are injected as JSON data, never as markup."""
        nasty = '</script><script>alert("xss")</script>'
        cells = build_cells(
            [{"place_id": "a", "name": nasty, "town": "T", "lead_score": "1",
              "site_verdict": "none", "staleness_points": "0", "website_url": "",
              "screenshot_path": ""}], {})
        html = render(cells, batch_name="b1")
        self.assertNotIn("</script><script>alert", html)
        self.assertIn("<\\/script>", html)

    def test_reason_codes_are_embedded_for_the_dropdown(self):
        html = render(build_cells([], {}), batch_name="b1")
        for code in ("chain", "site_fine", "too_small", "gut"):
            self.assertIn(code, html)


class TestDecisionsImport(unittest.TestCase):
    """Criterion 4: exports from the sheet import cleanly."""

    def test_culls_become_reasons_and_keeps_are_omitted(self):
        payload = [
            {"place_id": "a", "decision": "keep", "reason": ""},
            {"place_id": "b", "decision": "cull", "reason": "site_fine"},
        ]
        self.assertEqual(decisions_from_export(payload), {"b": "site_fine"})

    def test_cull_without_a_reason_is_refused(self):
        with self.assertRaises(ValueError) as ctx:
            decisions_from_export([{"place_id": "b", "decision": "cull", "reason": ""}])
        self.assertIn("no reason code", str(ctx.exception))

    def test_unknown_reason_code_is_refused(self):
        with self.assertRaises(ValueError) as ctx:
            decisions_from_export(
                [{"place_id": "b", "decision": "cull", "reason": "looked shabby"}])
        self.assertIn("unknown reason code", str(ctx.exception))

    def test_unknown_decision_is_refused(self):
        with self.assertRaises(ValueError):
            decisions_from_export([{"place_id": "b", "decision": "maybe"}])

    def test_missing_place_id_is_refused(self):
        with self.assertRaises(ValueError):
            decisions_from_export([{"decision": "cull", "reason": "chain"}])

    def test_non_list_payload_is_refused(self):
        with self.assertRaises(ValueError):
            decisions_from_export({"place_id": "a"})

    def test_empty_export_is_fine(self):
        self.assertEqual(decisions_from_export([]), {})


class TestScreenshotCaching(unittest.TestCase):
    """Criterion 5: a second run recaptures nothing."""

    def test_cached_pair_is_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            calls = []

            def fake(url, desktop, mobile):
                calls.append(url)
                desktop.parent.mkdir(parents=True, exist_ok=True)
                png(desktop, (1440, 900))
                png(mobile)

            first = ScreenshotCapturer(cache_dir=Path(tmp), capture_one=fake)
            first.capture_all([("p1", "https://x")])
            second = ScreenshotCapturer(cache_dir=Path(tmp), capture_one=fake)
            result = second.capture_all([("p1", "https://x")])
            self.assertEqual(len(calls), 1)
            self.assertTrue(result["p1"].ok)

    def test_refresh_forces_recapture(self):
        with tempfile.TemporaryDirectory() as tmp:
            calls = []

            def fake(url, desktop, mobile):
                calls.append(url)
                desktop.parent.mkdir(parents=True, exist_ok=True)
                png(desktop, (1440, 900))
                png(mobile)

            ScreenshotCapturer(cache_dir=Path(tmp), capture_one=fake).capture_all(
                [("p1", "https://x")])
            ScreenshotCapturer(cache_dir=Path(tmp), capture_one=fake,
                               refresh=True).capture_all([("p1", "https://x")])
            self.assertEqual(len(calls), 2)

    def test_no_website_is_skipped_not_attempted(self):
        def boom(*a):
            raise AssertionError("must not capture without a website")

        with tempfile.TemporaryDirectory() as tmp:
            result = ScreenshotCapturer(
                cache_dir=Path(tmp), capture_one=boom).capture_all([("p1", "")])
            self.assertFalse(result["p1"].ok)
            self.assertEqual(result["p1"].reason, "no website")

    def test_a_failure_writes_no_file_and_logs_a_reason(self):
        """Criterion 6, screenshot half: failure never becomes a result."""
        def fails(url, desktop, mobile):
            raise TimeoutError("navigation timed out")

        with tempfile.TemporaryDirectory() as tmp:
            cap = ScreenshotCapturer(cache_dir=Path(tmp), capture_one=fails)
            result = cap.capture_all([("p1", "https://x")])
            self.assertFalse(result["p1"].ok)
            self.assertIsNone(result["p1"].desktop)
            self.assertIn("timed out", result["p1"].reason)
            self.assertTrue(any("p1" in line for line in cap.log))
            self.assertEqual(list((Path(tmp) / "screenshots").glob("*.png")), [])

    def test_one_failure_does_not_stop_the_others(self):
        def flaky(url, desktop, mobile):
            if "bad" in url:
                raise OSError("dns")
            desktop.parent.mkdir(parents=True, exist_ok=True)
            png(desktop, (1440, 900))
            png(mobile)

        with tempfile.TemporaryDirectory() as tmp:
            result = ScreenshotCapturer(
                cache_dir=Path(tmp), capture_one=flaky, concurrency=4
            ).capture_all([("good1", "https://ok"), ("bad", "https://bad"),
                           ("good2", "https://ok2")])
            self.assertTrue(result["good1"].ok)
            self.assertTrue(result["good2"].ok)
            self.assertFalse(result["bad"].ok)



class TestMissingBrowser(unittest.TestCase):
    """A browser that was never installed is one setup problem.

    Reported after a real install where the playwright package landed but
    `playwright install chromium` had not been run, and the Scripts directory
    was not on PATH so the console script could not be found either.
    """

    def test_every_site_fails_once_with_a_setup_message(self):
        import pipeline.screenshots as shots_mod

        original = shots_mod.browser_ready
        shots_mod.browser_ready = lambda: (False, "Executable doesn't exist")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                cap = ScreenshotCapturer(cache_dir=Path(tmp))
                result = cap.capture_all(
                    [(f"p{i}", "https://example.com") for i in range(5)])
                self.assertEqual(len(result), 5)
                self.assertTrue(all(not r.ok for r in result.values()))
                self.assertTrue(
                    all(r.reason == "chromium not installed" for r in result.values()))
                # One log line for the run, not one per site.
                self.assertEqual(len(cap.log), 1)
                self.assertIn("playwright install chromium", cap.log[0])
        finally:
            shots_mod.browser_ready = original

    def test_no_browser_is_never_a_scoring_signal(self):
        """A run with no screenshots must still score exactly as before."""
        from pipeline.scoring import Weights, score_business

        weights = Weights.default()
        business = {
            "name": "X", "business_status": "OPERATIONAL",
            "website": "https://x.co.uk", "rating": 4.8, "review_count": 60,
            "site_score": {"verdict": "fine"},
            "staleness": {"fetch_ok": True, "has_viewport": True,
                          "has_media_queries": True, "copyright_age": 0,
                          "uses_table_layout": False, "has_https": True,
                          "has_flash": False, "platform_hint": None},
            "screenshot_path": "",
        }
        with_shot = dict(business, screenshot_path="/tmp/a.png")
        self.assertEqual(
            score_business(business, weights).score,
            score_business(with_shot, weights).score,
        )

    def test_injected_capturer_skips_the_browser_check(self):
        """Tests and offline runs must not need a browser at all."""
        calls = []

        def fake(url, desktop, mobile):
            calls.append(url)
            desktop.parent.mkdir(parents=True, exist_ok=True)
            png(desktop, (1440, 900))
            png(mobile)

        with tempfile.TemporaryDirectory() as tmp:
            result = ScreenshotCapturer(
                cache_dir=Path(tmp), capture_one=fake
            ).capture_all([("p1", "https://x")])
            self.assertTrue(result["p1"].ok)
            self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
