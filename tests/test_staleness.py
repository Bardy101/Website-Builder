"""Tests for staleness detection (brief part 1.1).

The governing rule: every check fails open. A fetch that times out, a
stylesheet that 404s, a missing element — none of them may add a point.
"""

import unittest

from pipeline.staleness import (
    analyse_staleness,
    detect_platform,
    find_copyright_year,
    has_flash,
    has_media_queries,
    signal_count,
    staleness_flags,
    uses_table_layout,
)

MODERN = """<html><head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="generator" content="Squarespace 7.1">
<style>@media (min-width: 600px) { .hero { padding: 4rem } }</style>
</head><body><footer>&copy; 2026 MVMNT Physio</footer></body></html>"""

DATED = """<html><head><title>Old</title></head><body>
<table><tr><td>Home</td></tr><tr><td>About</td></tr><tr><td>Us</td></tr></table>
<object data="intro.swf" type="application/x-shockwave-flash"></object>
<footer>Copyright 2009 Old Physio</footer></body></html>"""


def fetch_of(pages: dict):
    """A (final_url, text) fetcher, as analyse_staleness expects."""
    def fetch(url, timeout):
        if url in pages:
            return url, pages[url]
        return None
    return fetch


def text_fetcher(pages: dict):
    """has_media_queries takes a text-only fetcher, not the tuple form."""
    def fetch_text(url, timeout):
        return pages.get(url)
    return fetch_text


class TestCopyrightYear(unittest.TestCase):
    def test_finds_year_next_to_a_mark(self):
        self.assertEqual(find_copyright_year(DATED, now_year=2026), 2009)

    def test_takes_the_newest_of_a_range(self):
        html = "<footer>&copy; 2008-2019 Someone</footer>"
        self.assertEqual(find_copyright_year(html, now_year=2026), 2019)

    def test_ignores_years_with_no_copyright_mark(self):
        html = "<p>Established 1998. Serving the town since then.</p>"
        self.assertIsNone(find_copyright_year(html, now_year=2026))

    def test_ignores_implausible_future_years(self):
        html = "<footer>&copy; 2099 Time Travellers</footer>"
        self.assertIsNone(find_copyright_year(html, now_year=2026))

    def test_absent_is_none_not_zero(self):
        self.assertIsNone(find_copyright_year("<html></html>", now_year=2026))


class TestTableLayout(unittest.TestCase):
    def test_layout_table_detected(self):
        self.assertTrue(uses_table_layout(DATED))

    def test_data_table_with_headers_is_not_layout(self):
        html = ("<table><tr><th>Day</th></tr><tr><td>Mon</td></tr>"
                "<tr><td>Tue</td></tr></table>")
        self.assertFalse(uses_table_layout(html))

    def test_small_table_is_not_layout(self):
        self.assertFalse(uses_table_layout("<table><tr><td>x</td></tr></table>"))

    def test_table_inside_article_is_ignored(self):
        html = ("<article><table><tr><td>a</td></tr><tr><td>b</td></tr>"
                "<tr><td>c</td></tr></table></article>")
        self.assertFalse(uses_table_layout(html))


class TestFlash(unittest.TestCase):
    def test_object_with_swf(self):
        self.assertTrue(has_flash(DATED))

    def test_embed_with_swf(self):
        self.assertTrue(has_flash('<embed src="banner.swf">'))

    def test_modern_page_has_none(self):
        self.assertFalse(has_flash(MODERN))


class TestMediaQueries(unittest.TestCase):
    def test_inline_media_query_found(self):
        self.assertTrue(has_media_queries(MODERN, "https://x", text_fetcher({})))

    def test_found_in_a_first_party_stylesheet(self):
        html = '<html><head><link rel="stylesheet" href="/a.css"></head></html>'
        fetch = text_fetcher({"https://x.co.uk/a.css": "@media(min-width:1px){a{}}"})
        self.assertTrue(has_media_queries(html, "https://x.co.uk/", fetch))

    def test_absent_when_stylesheet_readable_and_has_none(self):
        html = '<html><head><link rel="stylesheet" href="/a.css"></head></html>'
        fetch = text_fetcher({"https://x.co.uk/a.css": "body{color:red}"})
        self.assertFalse(has_media_queries(html, "https://x.co.uk/", fetch))

    def test_unreadable_stylesheet_fails_open_to_true(self):
        """Absent evidence must never award the +15."""
        html = '<html><head><link rel="stylesheet" href="/a.css"></head></html>'
        self.assertTrue(has_media_queries(html, "https://x.co.uk/", text_fetcher({})))

    def test_no_stylesheet_at_all_fails_open_to_true(self):
        self.assertTrue(has_media_queries("<html></html>", "https://x", text_fetcher({})))


class TestPlatform(unittest.TestCase):
    def test_generator_meta(self):
        self.assertEqual(detect_platform(MODERN), "squarespace")

    def test_asset_url_pattern(self):
        self.assertEqual(
            detect_platform('<img src="https://static.wixstatic.com/a.png">'), "wix")

    def test_wordpress_from_paths(self):
        self.assertEqual(
            detect_platform('<link href="/wp-content/themes/x/style.css">'),
            "wordpress")

    def test_unknown_is_none(self):
        self.assertIsNone(detect_platform("<html><body>hi</body></html>"))


class TestAnalyse(unittest.TestCase):
    def test_modern_site_sets_no_flags(self):
        result = analyse_staleness(
            "https://x", prefetched=("https://x", MODERN), now_year=2026)
        self.assertTrue(result["fetch_ok"])
        self.assertEqual(signal_count(result), 0)
        self.assertEqual(result["platform_hint"], "squarespace")

    def test_dated_site_sets_several(self):
        result = analyse_staleness(
            "https://x", prefetched=("http://x", DATED), now_year=2026)
        flags = staleness_flags(result)
        self.assertTrue(flags["no_viewport"])
        self.assertTrue(flags["table_layout"])
        self.assertTrue(flags["flash"])
        self.assertTrue(flags["copyright_stale"])
        self.assertTrue(flags["no_https"])
        self.assertGreaterEqual(signal_count(result), 4)

    def test_failed_fetch_sets_nothing(self):
        """Acceptance criterion 6."""
        result = analyse_staleness("https://down", fetch=lambda u, t: None)
        self.assertFalse(result["fetch_ok"])
        self.assertEqual(signal_count(result), 0)
        self.assertTrue(all(v is False or v is None for k, v in result.items()))

    def test_no_url_sets_nothing(self):
        self.assertEqual(signal_count(analyse_staleness(None)), 0)

    def test_prefetched_html_is_not_refetched(self):
        def boom(url, timeout):
            raise AssertionError("must not fetch when html was supplied")

        result = analyse_staleness(
            "https://x", fetch=boom, prefetched=("https://x", MODERN))
        self.assertTrue(result["fetch_ok"])


class TestFlagsAreEvidenceOnly(unittest.TestCase):
    def test_unknown_values_never_become_flags(self):
        unknown = {"fetch_ok": True, "has_viewport": None, "has_media_queries": None,
                   "copyright_age": None, "uses_table_layout": None,
                   "has_https": None, "has_flash": None}
        self.assertEqual(signal_count(unknown), 0)

    def test_copyright_threshold_is_inclusive(self):
        self.assertFalse(staleness_flags({"copyright_age": 2})["copyright_stale"])
        self.assertTrue(staleness_flags({"copyright_age": 3})["copyright_stale"])


if __name__ == "__main__":
    unittest.main()
