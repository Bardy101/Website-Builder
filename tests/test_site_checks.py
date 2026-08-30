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


def stale(**overrides):
    """A fully healthy analysis; override to set individual signals."""
    base = {"fetch_ok": True, "has_viewport": True, "has_media_queries": True,
            "copyright_year": 2026, "copyright_age": 0, "uses_table_layout": False,
            "has_https": True, "has_flash": False, "platform_hint": None}
    base.update(overrides)
    return base


class TestVerdict(unittest.TestCase):
    """Buckets come from staleness signals, not mobile score (brief 1.4)."""

    def _check(self, **overrides):
        return SiteCheck(True, False, mobile_score=overrides.pop("mobile", None),
                         staleness=stale(**overrides))

    def test_no_site(self):
        c = SiteCheck(has_website=False, is_social_or_directory=False)
        self.assertEqual(verdict_from(c), "none")

    def test_social_only(self):
        c = SiteCheck(has_website=True, is_social_or_directory=True)
        self.assertEqual(verdict_from(c), "social_only")

    def test_no_staleness_signals_is_fine(self):
        self.assertEqual(verdict_from(self._check()), "fine")

    def test_one_signal_is_poor(self):
        self.assertEqual(verdict_from(self._check(has_https=False)), "poor")

    def test_two_signals_is_dated(self):
        c = self._check(has_viewport=False, has_media_queries=False)
        self.assertEqual(verdict_from(c), "dated")

    def test_stale_copyright_counts(self):
        self.assertEqual(verdict_from(self._check(copyright_age=7)), "poor")

    def test_fresh_copyright_does_not_count(self):
        self.assertEqual(verdict_from(self._check(copyright_age=1)), "fine")

    def test_a_fast_site_is_not_rewarded_for_being_fast(self):
        """The inversion the brief exists to fix: heavy modern site stays fine,
        light dated site does not."""
        modern = self._check(mobile=23)
        dated = self._check(mobile=95, has_viewport=False, uses_table_layout=True)
        self.assertEqual(verdict_from(modern), "fine")
        self.assertEqual(verdict_from(dated), "dated")

    def test_failed_fetch_is_unknown_never_fine(self):
        c = SiteCheck(True, False, staleness={"fetch_ok": False})
        self.assertEqual(verdict_from(c), "unknown")


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
        html = ('<html><head><meta name="viewport" content="width=device-width">'
                '<style>@media(min-width:1px){a{color:red}}</style></head>'
                '<body><footer>&copy; 2026</footer></body></html>')
        checker = SiteChecker(
            http_get=lambda url: (200, "https://example.co.uk", html),
            pagespeed=lambda url: 42,
        )
        result = checker.check("https://example.co.uk")
        self.assertEqual(result.mobile_score, 42)  # kept for reference
        self.assertTrue(result.https)
        self.assertTrue(result.viewport)
        # A clean modern page: slow, but nothing stale about it.
        self.assertEqual(result.verdict, "fine")

    def test_one_fetch_serves_every_page_signal(self):
        """The brief forbids fetching the same homepage twice."""
        fetches = []

        def http_get(url):
            fetches.append(url)
            return 200, "https://example.co.uk", "<html><head></head></html>"

        SiteChecker(http_get=http_get, pagespeed=lambda url: 50).check(
            "https://example.co.uk")
        self.assertEqual(len(fetches), 1, fetches)

    def test_failed_fetch_degrades_gracefully(self):
        def fails(url):
            raise OSError("connection refused")

        checker = SiteChecker(http_get=fails, pagespeed=lambda url: None)
        result = checker.check("https://down.example")
        self.assertIsNone(result.https)
        self.assertIsNone(result.mobile_score)
        # Nothing could be established, so nothing is claimed.
        self.assertEqual(result.verdict, "unknown")
        self.assertFalse(result.staleness["fetch_ok"])



class TestPagespeedFailureCaching(unittest.TestCase):
    """A page Lighthouse cannot score is cached; a transport error is not."""

    def test_unscorable_page_is_cached_and_not_retried(self):
        import tempfile

        from pipeline.cache import Cache

        calls = []

        def pagespeed(url):
            calls.append(url)
            return {"scored": True, "score": None}  # ran, no score possible

        with tempfile.TemporaryDirectory() as tmp:
            for _ in range(2):
                checker = SiteChecker(
                    http_get=lambda u: (200, "https://x.co.uk", "<html></html>"),
                    pagespeed=pagespeed,
                    cache=Cache(tmp),
                )
                self.assertIsNone(checker.check("https://x.co.uk").mobile_score)
            self.assertEqual(len(calls), 1)

    def test_transport_error_is_retried_next_run(self):
        import tempfile

        from pipeline.cache import Cache

        calls = []

        def pagespeed(url):
            calls.append(url)
            return None  # quota blip / network error

        with tempfile.TemporaryDirectory() as tmp:
            for _ in range(2):
                SiteChecker(
                    http_get=lambda u: (200, "https://x.co.uk", "<html></html>"),
                    pagespeed=pagespeed,
                    cache=Cache(tmp),
                ).check("https://x.co.uk")
            self.assertEqual(len(calls), 2)

    def test_bare_int_from_a_test_double_still_works(self):
        checker = SiteChecker(
            http_get=lambda u: (200, "https://x.co.uk", "<html></html>"),
            pagespeed=lambda u: 42,
        )
        self.assertEqual(checker.check("https://x.co.uk").mobile_score, 42)


if __name__ == "__main__":
    unittest.main()
