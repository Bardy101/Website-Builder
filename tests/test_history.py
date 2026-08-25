"""Tests for reading past searches back off the cache.

A repeat search is free only on an exact niche + area + radius match, so the
menu offers what is genuinely cached rather than asking the user to remember
which wording they typed last time.
"""

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pipeline.history import PastSearch, past_searches


def write_entry(root, name, *, key, days_ago, niche=None, area=None,
                radius=None, results=3):
    folder = Path(root) / "places_search"
    folder.mkdir(parents=True, exist_ok=True)
    data = {"requested": 75, "places": [{"id": f"p{i}"} for i in range(results)]}
    if niche is not None:
        data.update({"niche": niche, "area": area, "radius_m": radius})
    (folder / name).write_text(json.dumps({
        "fetched": (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(),
        "key": key, "data": data,
    }), encoding="utf-8")


class TestPastSearches(unittest.TestCase):
    def test_no_cache_is_empty_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(past_searches(tmp), [])

    def test_reads_stored_niche_and_area(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_entry(tmp, "a.json", key="physiotherapist in Hitchin|r=8000",
                        days_ago=0, niche="physiotherapist", area="Hitchin",
                        radius=8000)
            found = past_searches(tmp)
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0].niche, "physiotherapist")
            self.assertEqual(found[0].area, "Hitchin")
            self.assertEqual(found[0].radius_m, 8000)

    def test_legacy_entry_is_parsed_from_its_key(self):
        """Entries cached before niche/area were stored must still list."""
        with tempfile.TemporaryDirectory() as tmp:
            write_entry(tmp, "a.json", key="dentist in Letchworth|r=6000",
                        days_ago=1)
            found = past_searches(tmp)
            self.assertEqual(found[0].niche, "dentist")
            self.assertEqual(found[0].area, "Letchworth")
            self.assertEqual(found[0].radius_m, 6000)

    def test_expired_entries_are_excluded(self):
        """Past 30 days it would bill again, so it is not 'already paid for'."""
        with tempfile.TemporaryDirectory() as tmp:
            write_entry(tmp, "old.json", key="plumber in Baldock|r=6000",
                        days_ago=45, niche="plumber", area="Baldock", radius=6000)
            write_entry(tmp, "new.json", key="plumber in Royston|r=6000",
                        days_ago=2, niche="plumber", area="Royston", radius=6000)
            areas = [s.area for s in past_searches(tmp)]
            self.assertEqual(areas, ["Royston"])

    def test_newest_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_entry(tmp, "a.json", key="a in X|r=1", days_ago=5,
                        niche="a", area="X", radius=1)
            write_entry(tmp, "b.json", key="b in Y|r=1", days_ago=1,
                        niche="b", area="Y", radius=1)
            self.assertEqual([s.niche for s in past_searches(tmp)], ["b", "a"])

    def test_same_search_at_two_radii_lists_separately(self):
        """Radius is part of the cache key, so it is a different search."""
        with tempfile.TemporaryDirectory() as tmp:
            write_entry(tmp, "a.json", key="physio in Hitchin|r=8000", days_ago=1,
                        niche="physio", area="Hitchin", radius=8000)
            write_entry(tmp, "b.json", key="physio in Hitchin|r=10000", days_ago=1,
                        niche="physio", area="Hitchin", radius=10000)
            self.assertEqual(len(past_searches(tmp)), 2)

    def test_differently_typed_areas_list_separately(self):
        """The exact wording is what makes a repeat free — show both."""
        with tempfile.TemporaryDirectory() as tmp:
            write_entry(tmp, "a.json", key="physio in Hitchin|r=8000", days_ago=1,
                        niche="physio", area="Hitchin", radius=8000)
            write_entry(tmp, "b.json",
                        key="physio in Hitchin, Hertfordshire|r=8000", days_ago=1,
                        niche="physio", area="Hitchin, Hertfordshire", radius=8000)
            areas = sorted(s.area for s in past_searches(tmp))
            self.assertEqual(areas, ["Hitchin", "Hitchin, Hertfordshire"])

    def test_corrupt_file_is_skipped_not_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_entry(tmp, "good.json", key="a in X|r=1", days_ago=1,
                        niche="a", area="X", radius=1)
            (Path(tmp) / "places_search" / "bad.json").write_text("{not json",
                                                                  encoding="utf-8")
            self.assertEqual(len(past_searches(tmp)), 1)

    def test_describe_reads_as_english(self):
        entry = PastSearch(niche="physio", area="Hitchin", radius_m=8000,
                           fetched=datetime.now(timezone.utc), results=12)
        self.assertIn("physio in Hitchin", entry.describe())
        self.assertIn("cached today", entry.describe())


class TestFindFlowHasNoDemoQuestion(unittest.TestCase):
    def test_demo_is_decided_by_the_key_not_a_prompt(self):
        source = (Path(__file__).resolve().parent.parent / "run.py").read_text(
            encoding="utf-8")
        self.assertNotIn("Use the demo data instead of the real API?", source)
        self.assertIn("demo = not has_key", source)



def write_legacy_entry(root, name, *, key, days_ago, places=3):
    """The shape written before the search cache key dropped its |n= suffix:
    a bare list under `data`, and max_results baked into the key."""
    folder = Path(root) / "places_search"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(json.dumps({
        "fetched": (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(),
        "key": key,
        "data": [{"id": f"ChIJ{i}"} for i in range(places)],
    }), encoding="utf-8")


class TestPreKeyChangeEntries(unittest.TestCase):
    """Searches paid for before the cache key changed must stay visible.

    Changing how keys are built orphaned these entries: the history showed
    nothing and a repeat search re-billed for data already bought.
    """

    def test_legacy_entry_is_listed(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_legacy_entry(tmp, "old.json",
                               key="physiotherapist in Hitchin|r=8000|n=75",
                               days_ago=0, places=12)
            found = past_searches(tmp)
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0].niche, "physiotherapist")
            self.assertEqual(found[0].area, "Hitchin")
            self.assertEqual(found[0].radius_m, 8000)
            self.assertEqual(found[0].results, 12)

    def test_legacy_radius_is_not_confused_by_the_n_suffix(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_legacy_entry(tmp, "old.json", key="plumber in Royston|r=6000|n=30",
                               days_ago=1)
            self.assertEqual(past_searches(tmp)[0].radius_m, 6000)

    def test_expired_legacy_entry_is_still_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_legacy_entry(tmp, "old.json", key="a in X|r=1|n=5", days_ago=45)
            self.assertEqual(past_searches(tmp), [])


class TestLegacyEntriesAreStillReused(unittest.TestCase):
    """The paid-for search must serve the repeat, not just appear in a list."""

    def _client(self, cache, billed):
        from pipeline.places import PlacesClient

        def post(url, *, json_body, headers):
            query = json_body.get("textQuery")
            if query == "Hitchin":
                return {"places": [{"location": {"latitude": 51.9, "longitude": -0.3}}]}
            billed.append(query)
            return {"places": [{"id": "freshly-billed"}]}

        return PlacesClient(api_key="k", post=post, cache=cache)

    def test_repeat_search_costs_nothing_and_returns_the_cached_places(self):
        from pipeline.cache import Cache

        with tempfile.TemporaryDirectory() as tmp:
            write_legacy_entry(tmp, "old.json",
                               key="physiotherapist in Hitchin|r=8000|n=75",
                               days_ago=0, places=12)
            billed = []
            got = self._client(Cache(tmp, 30), billed).search(
                "physiotherapist", "Hitchin", radius_m=8000, max_results=75)
            self.assertEqual(billed, [])
            self.assertEqual(len(got), 12)

    def test_legacy_entry_is_upgraded_to_the_current_key(self):
        from pipeline.cache import Cache

        with tempfile.TemporaryDirectory() as tmp:
            write_legacy_entry(tmp, "old.json",
                               key="physiotherapist in Hitchin|r=8000|n=75",
                               days_ago=0, places=12)
            billed = []
            self._client(Cache(tmp, 30), billed).search(
                "physiotherapist", "Hitchin", radius_m=8000, max_results=75)
            # Written under the current key, so later runs hit it directly.
            upgraded = Cache(tmp, 30).get(
                "places_search", "physiotherapist in Hitchin|r=8000")
            self.assertIsInstance(upgraded, dict)
            self.assertEqual(len(upgraded["places"]), 12)
            self.assertEqual(upgraded["niche"], "physiotherapist")

    def test_a_genuinely_different_search_still_bills(self):
        from pipeline.cache import Cache

        with tempfile.TemporaryDirectory() as tmp:
            write_legacy_entry(tmp, "old.json",
                               key="physiotherapist in Hitchin|r=8000|n=75",
                               days_ago=0, places=12)
            billed = []
            self._client(Cache(tmp, 30), billed).search(
                "plumber", "Hitchin", radius_m=8000, max_results=75)
            self.assertEqual(len(billed), 1)


if __name__ == "__main__":
    unittest.main()
