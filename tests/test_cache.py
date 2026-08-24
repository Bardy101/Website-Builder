"""Tests for the 30-day on-disk cache."""

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pipeline.cache import Cache


class TestCache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache = Cache(self.tmp.name, ttl_days=30)

    def tearDown(self):
        self.tmp.cleanup()

    def test_roundtrip(self):
        self.cache.set("places", "ChIJabc", {"name": "X"})
        self.assertEqual(self.cache.get("places", "ChIJabc"), {"name": "X"})

    def test_miss_returns_none(self):
        self.assertIsNone(self.cache.get("places", "nope"))

    def test_expiry(self):
        self.cache.set("places", "old", {"name": "X"})
        path = Path(self.tmp.name) / "places" / "old.json"
        payload = json.loads(path.read_text())
        payload["fetched"] = (
            datetime.now(timezone.utc) - timedelta(days=31)
        ).isoformat()
        path.write_text(json.dumps(payload))
        self.assertIsNone(self.cache.get("places", "old"))

    def test_get_or_fetch_calls_once(self):
        calls = []

        def fetch():
            calls.append(1)
            return {"v": len(calls)}

        first = self.cache.get_or_fetch("ns", "k", fetch)
        second = self.cache.get_or_fetch("ns", "k", fetch)
        self.assertEqual(first, second)
        self.assertEqual(len(calls), 1)

    def test_none_result_is_not_cached(self):
        """A failed API call must not poison the cache for 30 days."""
        calls = []

        def fetch():
            calls.append(1)
            return None

        self.cache.get_or_fetch("ns", "k", fetch)
        self.cache.get_or_fetch("ns", "k", fetch)
        self.assertEqual(len(calls), 2)

    def test_awkward_keys_are_safe_filenames(self):
        key = "physiotherapist in Hitchin/Herts?|r=8000 " + "x" * 200
        self.cache.set("search", key, ["a"])
        self.assertEqual(self.cache.get("search", key), ["a"])

    def test_distinct_long_keys_do_not_collide(self):
        a = "physiotherapist in Hitchin " + "x" * 200 + "A"
        b = "physiotherapist in Hitchin " + "x" * 200 + "B"
        self.cache.set("search", a, ["first"])
        self.cache.set("search", b, ["second"])
        self.assertEqual(self.cache.get("search", a), ["first"])
        self.assertEqual(self.cache.get("search", b), ["second"])


if __name__ == "__main__":
    unittest.main()
