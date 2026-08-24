"""Tests for site classification and the site_verdict ladder."""

import unittest

from pipeline.site_checks import SiteCheck, SiteChecker, classify_url, verdict_from


class TestClassifyUrl(unittest.TestCase):
    def test_none(self):
        self.assertEqual(classify_url(None), "none")
        self.assertEqual(classify_url(""), "none")
        self.assertEqual(classify_url("   "), "none")

    def test_social(self):
        self.assertEqual(classify_url("https://www.facebook.com/clinic"), "social")
        self.assertEqual(classify_url("https://instagram.com/clinic"), "social")

    def test_directory(self):
        self.assertEqual(classify_url("https://www.yell.com/biz/x"), "directory")
        self.assertEqual(classify_url("https://x.business.site"), "real")

    def test_real(self):
        self.assertEqual(classify_url("https://hitchinphysio.co.uk"), "real")
        self.assertEqual(classify_url("hitchinphysio.co.uk"), "real")


class TestVerdict(unittest.TestCase):
    def test_no_site(self):
        c = SiteCheck(has_website=False, is_social_or_directory=False)
        self.assertEqual(verdict_from(c), "none")

    def test_social_only(self):
        c = SiteCheck(has_website=True, is_social_or_directory=True)
        self.assertEqual(verdict_from(c), "social_only")

    def test_no_https_is_poor(self):
        c = SiteCheck(True, False, https=False, viewport=True, mobile_score=90)
        self.assertEqual(verdict_from(c), "poor")

    def test_slow_mobile_is_poor(self):
        c = SiteCheck(True, False, https=True, viewport=True, mobile_score=31)
        self.assertEqual(verdict_from(c), "poor")

    def test_middling_is_dated(self):
        c = SiteCheck(True, False, https=True, viewport=True, mobile_score=62)
        self.assertEqual(verdict_from(c), "dated")

    def test_no_viewport_is_dated_even_when_fast(self):
        c = SiteCheck(True, False, https=True, viewport=False, mobile_score=95)
        self.assertEqual(verdict_from(c), "dated")

    def test_good_is_fine(self):
        c = SiteCheck(True, False, https=True, viewport=True, mobile_score=85)
        self.assertEqual(verdict_from(c), "fine")

    def test_unknown_score_falls_back_to_cheap_signals(self):
        c = SiteCheck(True, False, https=True, viewport=False, mobile_score=None)
        self.assertEqual(verdict_from(c), "dated")


class TestSiteChecker(unittest.TestCase):
    def test_no_website_skips_network(self):
        def boom(*a, **k):
            raise AssertionError("should not hit the network")

        checker = SiteChecker(http_get=boom, pagespeed=boom)
        result = checker.check(None)
        self.assertEqual(result.verdict, "none")
        self.assertFalse(result.has_website)

    def test_social_skips_network(self):
        def boom(*a, **k):
            raise AssertionError("should not hit the network")

        checker = SiteChecker(http_get=boom, pagespeed=boom)
        self.assertEqual(checker.check("https://facebook.com/x").verdict, "social_only")

    def test_real_site_uses_injected_transports(self):
        html = '<html><head><meta name="viewport" content="width=device-width"></head></html>'
        checker = SiteChecker(
            http_get=lambda url: (200, "https://example.co.uk", html),
            pagespeed=lambda url: 42,
        )
        result = checker.check("https://example.co.uk")
        self.assertEqual(result.mobile_score, 42)
        self.assertTrue(result.https)
        self.assertTrue(result.viewport)
        self.assertEqual(result.verdict, "poor")

    def test_failed_fetch_degrades_gracefully(self):
        def fails(url):
            raise OSError("connection refused")

        checker = SiteChecker(http_get=fails, pagespeed=lambda url: None)
        result = checker.check("https://down.example")
        self.assertIsNone(result.https)
        self.assertIsNone(result.mobile_score)
        # Unknown must never be rated worse than it is, nor silently "fine"
        self.assertEqual(result.verdict, "fine")


if __name__ == "__main__":
    unittest.main()
