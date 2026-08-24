"""Tests for API call economy.

Place Details bills at Google's Enterprise tier (the field mask includes
reviews, rating and photos), which carries the smallest free monthly
allowance of the three tiers. Fetching details for a business we then
exclude is therefore the most expensive mistake this stage can make.
"""

import json
import tempfile
import unittest
from pathlib import Path

from pipeline.cache import Cache
from pipeline.discover import discover
from pipeline.places import SEARCH_FIELDS, PlacesClient
from pipeline.scoring import Weights

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = json.loads((ROOT / "examples" / "sample_fixture.json").read_text())["places"]


def stub_for(place):
    return {
        "id": place["id"],
        "displayName": place["displayName"],
        "formattedAddress": place.get("formattedAddress"),
        "businessStatus": place.get("businessStatus"),
    }


def make_client(cache=None, places=None):
    source = places if places is not None else FIXTURE

    def post(url, *, json_body, headers):
        return {"places": [stub_for(p) for p in source]}

    def get(url, *, headers):
        pid = url.rsplit("/", 1)[-1]
        return next((p for p in source if p["id"] == pid), {})

    return PlacesClient(api_key="k", post=post, get=get, cache=cache)


class TestSearchFieldMask(unittest.TestCase):
    def test_search_asks_for_business_status(self):
        """Without it, closed businesses can't be screened before details."""
        self.assertIn("places.businessStatus", SEARCH_FIELDS)

    def test_search_does_not_ask_for_expensive_fields(self):
        """Reviews/ratings on the search call would bill every search higher."""
        for pricey in ("places.reviews", "places.rating", "places.photos"):
            self.assertNotIn(pricey, SEARCH_FIELDS)


class TestStubScreening(unittest.TestCase):
    def setUp(self):
        self.weights = Weights.load(ROOT / "weights.json")

    def test_excluded_businesses_cost_no_details_call(self):
        client = make_client()
        result = discover(
            niche="physiotherapist", area="Hitchin", places_client=client,
            weights=self.weights, top=25,
        )
        screened = [e for e in result.excluded if e.get("screened_before_details")]
        self.assertEqual(len(screened), 2)  # the chain and the closed practice
        self.assertEqual(client.stats["details_fetched"], len(FIXTURE) - 2)

    def test_screened_exclusions_name_their_reason(self):
        client = make_client()
        result = discover(
            niche="physiotherapist", area="Hitchin", places_client=client,
            weights=self.weights, top=25,
        )
        reasons = {e["excluded"] for e in result.excluded}
        self.assertEqual(reasons, {"chain_or_franchise", "non_operational"})

    def test_kept_businesses_are_unaffected(self):
        client = make_client()
        result = discover(
            niche="physiotherapist", area="Hitchin", places_client=client,
            weights=self.weights, top=25,
        )
        names = [b["name"] for b in result.businesses]
        self.assertIn("Bancroft Physio Rooms", names)
        self.assertEqual(len(names), 4)

    def test_missing_business_status_still_fetches_details(self):
        """An older or partial response must not silently drop candidates."""
        places = [dict(p) for p in FIXTURE]
        for p in places:
            p.pop("businessStatus", None)
        client = make_client(places=places)
        result = discover(
            niche="physiotherapist", area="Hitchin", places_client=client,
            weights=self.weights, top=25,
        )
        # Only the chain is screenable by name; the closed one now needs details.
        screened = [e for e in result.excluded if e.get("screened_before_details")]
        self.assertEqual(len(screened), 1)
        self.assertTrue(any(b["name"] == "Bancroft Physio Rooms"
                            for b in result.businesses))


class TestCallCounting(unittest.TestCase):
    def setUp(self):
        self.weights = Weights.load(ROOT / "weights.json")

    def test_stats_start_at_zero(self):
        client = make_client()
        self.assertEqual(client.stats["details_fetched"], 0)
        self.assertEqual(client.stats["search_fetched"], 0)

    def test_clients_do_not_share_stats(self):
        a, b = make_client(), make_client()
        a.search("physio", "Hitchin")
        self.assertEqual(a.stats["search_fetched"], 1)
        self.assertEqual(b.stats["search_fetched"], 0)

    def test_second_run_is_served_entirely_from_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Cache(tmp, 30)
            first = make_client(cache=cache)
            discover(niche="physiotherapist", area="Hitchin", places_client=first,
                     weights=self.weights, top=25, seen_place_ids=set())
            self.assertGreater(first.stats["details_fetched"], 0)

            second = make_client(cache=cache)
            discover(niche="physiotherapist", area="Hitchin", places_client=second,
                     weights=self.weights, top=25, seen_place_ids=set())
            self.assertEqual(second.stats["details_fetched"], 0)
            self.assertEqual(second.stats["search_fetched"], 0)
            # But it still asked for them — the cache absorbed the requests.
            self.assertGreater(second.stats["details_requested"], 0)


if __name__ == "__main__":
    unittest.main()
