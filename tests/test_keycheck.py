"""Tests for API key testing and error diagnosis.

The failure these guard against is a key restricted to HTTP referrers
("Websites" in the Google console). It looks like the cautious choice but
blocks every server-side call, and the raw API error doesn't say so.
"""

import unittest

from pipeline.keycheck import (
    diagnose_google,
    test_companies_house,
    test_pagespeed,
    test_places,
)


class TestDiagnose(unittest.TestCase):
    def test_referrer_restriction_is_named(self):
        detail, hint = diagnose_google(
            403, '{"error":{"status":"PERMISSION_DENIED",'
                 '"message":"API_KEY_HTTP_REFERRER_BLOCKED"}}'
        )
        self.assertIn("restricted to websites", detail)
        self.assertIn("'None'", hint)

    def test_ip_restriction_is_named(self):
        detail, hint = diagnose_google(403, "API_KEY_IP_ADDRESS_BLOCKED")
        self.assertIn("IP addresses", detail)
        self.assertIn("changed", hint)

    def test_api_not_ticked_is_named(self):
        detail, hint = diagnose_google(403, "API_KEY_SERVICE_BLOCKED")
        self.assertIn("API restrictions", detail)
        self.assertIn("Places API (New)", hint)

    def test_api_disabled_is_named(self):
        detail, _ = diagnose_google(
            403, "Places API (New) has not been used in project 123 before"
        )
        self.assertIn("isn't enabled", detail)

    def test_invalid_key_is_named(self):
        detail, _ = diagnose_google(400, '{"error":{"message":"API key not valid"}}')
        self.assertIn("isn't valid", detail)

    def test_billing_is_named(self):
        detail, hint = diagnose_google(403, "billing account is required")
        self.assertIn("billing", detail)
        self.assertIn("budget cap", hint)

    def test_quota_is_named(self):
        detail, _ = diagnose_google(429, "RESOURCE_EXHAUSTED")
        self.assertIn("quota", detail)

    def test_generic_403_still_suggests_restrictions(self):
        detail, hint = diagnose_google(403, "forbidden")
        self.assertIn("permission denied", detail)
        self.assertIn("restriction", hint)

    def test_unknown_status_is_reported_verbatim(self):
        detail, hint = diagnose_google(500, "internal blew up")
        self.assertIn("500", detail)
        self.assertIn("blew up", hint)


class TestPlacesKeyTest(unittest.TestCase):
    def test_success(self):
        result = test_places("k", post=lambda *a, **k: (200, '{"places":[]}'))
        self.assertTrue(result.ok)
        self.assertIn("works", result.detail)

    def test_missing_key_does_not_call_out(self):
        def boom(*a, **k):
            raise AssertionError("should not call the API without a key")

        result = test_places(None, post=boom)
        self.assertFalse(result.ok)
        self.assertIn("no key set", result.detail)

    def test_referrer_blocked_gives_the_fix(self):
        result = test_places(
            "k", post=lambda *a, **k: (403, "API_KEY_HTTP_REFERRER_BLOCKED")
        )
        self.assertFalse(result.ok)
        self.assertIn("restricted to websites", result.detail)
        self.assertIn("Application restrictions", result.hint)

    def test_network_failure_is_caught(self):
        def fails(*a, **k):
            raise OSError("no route to host")

        result = test_places("k", post=fails)
        self.assertFalse(result.ok)
        self.assertIn("could not reach", result.detail)

    def test_result_renders_with_hint(self):
        result = test_places("k", post=lambda *a, **k: (403, "API_KEY_SERVICE_BLOCKED"))
        text = str(result)
        self.assertTrue(text.startswith("FAIL"))
        self.assertIn("Places API (New)", text)

    def test_success_renders_as_ok(self):
        self.assertTrue(str(test_places("k", post=lambda *a, **k: (200, "{}"))).startswith("OK"))


class TestOtherKeyTests(unittest.TestCase):
    def test_pagespeed_success(self):
        self.assertTrue(test_pagespeed("k", get=lambda *a, **k: (200, "{}")).ok)

    def test_pagespeed_missing_key_is_flagged_optional(self):
        result = test_pagespeed(None)
        self.assertFalse(result.ok)
        self.assertIn("optional", result.detail)

    def test_companies_house_success(self):
        self.assertTrue(test_companies_house("k", get=lambda *a, **k: (200, "{}")).ok)

    def test_companies_house_rejected_key_explains_live_vs_test(self):
        result = test_companies_house("k", get=lambda *a, **k: (401, ""))
        self.assertFalse(result.ok)
        self.assertIn("rejected", result.detail)
        self.assertIn("sandbox", result.hint)



class TestPagespeedGuidance(unittest.TestCase):
    """PageSpeed needs no key of its own — the Places key serves both."""

    def test_missing_key_explains_the_shared_key(self):
        result = test_pagespeed(None)
        self.assertFalse(result.ok)
        self.assertIn("Places key works here too", result.hint)
        self.assertIn("without a key", result.hint)

    def test_missing_key_does_not_call_out(self):
        def boom(*a, **k):
            raise AssertionError("should not call the API without a key")

        test_pagespeed(None, get=boom)


if __name__ == "__main__":
    unittest.main()
