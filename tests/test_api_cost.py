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



class TestRadiusReachesTheAPI(unittest.TestCase):
    """The radius must bias the search, not merely change the cache key.

    Before this was wired up, widening 8000 -> 10000 busted the cache, paid
    for a fresh search, and returned exactly the same places.
    """

    def _client(self, recorder):
        def post(url, *, json_body, headers):
            if json_body.get("textQuery") == "Hitchin":
                return {"places": [{"location": {"latitude": 51.9, "longitude": -0.28}}]}
            recorder.append(json_body)
            return {"places": [{"id": "a1"}]}

        return PlacesClient(api_key="k", post=post)

    def test_radius_is_sent_as_a_location_bias(self):
        sent = []
        self._client(sent).search("physio", "Hitchin", radius_m=10000)
        bias = sent[0].get("locationBias", {}).get("circle", {})
        self.assertEqual(bias.get("radius"), 10000.0)
        self.assertEqual(bias["center"]["latitude"], 51.9)

    def test_different_radii_send_different_requests(self):
        a, b = [], []
        self._client(a).search("physio", "Hitchin", radius_m=8000)
        self._client(b).search("physio", "Hitchin", radius_m=10000)
        self.assertNotEqual(
            a[0]["locationBias"]["circle"]["radius"],
            b[0]["locationBias"]["circle"]["radius"],
        )

    def test_area_center_is_cached_across_niches(self):
        centre_calls = []

        def post(url, *, json_body, headers):
            if json_body.get("textQuery") in ("Hitchin",):
                centre_calls.append(json_body)
                return {"places": [{"location": {"latitude": 51.9, "longitude": -0.28}}]}
            return {"places": [{"id": "a1"}]}

        with tempfile.TemporaryDirectory() as tmp:
            cache = Cache(tmp, 30)
            for niche in ("physio", "accountant", "plumber"):
                PlacesClient(api_key="k", post=post, cache=cache).search(niche, "Hitchin")
            self.assertEqual(len(centre_calls), 1)

    def test_search_still_works_without_a_centre(self):
        """A town Google can't locate must not break the search."""
        sent = []

        def post(url, *, json_body, headers):
            if json_body.get("textQuery") == "Nowheresville":
                return {"places": []}
            sent.append(json_body)
            return {"places": [{"id": "a1"}]}

        results = PlacesClient(api_key="k", post=post).search("physio", "Nowheresville")
        self.assertEqual(len(results), 1)
        self.assertNotIn("locationBias", sent[0])


class TestRefreshBypassesCache(unittest.TestCase):
    """--refresh re-fetches on purpose; it costs real calls."""

    def test_bypass_forces_a_refetch(self):
        with tempfile.TemporaryDirectory() as tmp:
            warm = Cache(tmp, 30)
            first = make_client(cache=warm)
            discover(niche="physiotherapist", area="Hitchin", places_client=first,
                     weights=Weights.load(ROOT / "weights.json"), top=25,
                     seen_place_ids=set())
            self.assertGreater(first.stats["details_fetched"], 0)

            cached = make_client(cache=Cache(tmp, 30))
            discover(niche="physiotherapist", area="Hitchin", places_client=cached,
                     weights=Weights.load(ROOT / "weights.json"), top=25,
                     seen_place_ids=set())
            self.assertEqual(cached.stats["details_fetched"], 0)

            forced = make_client(cache=Cache(tmp, 30, bypass=True))
            discover(niche="physiotherapist", area="Hitchin", places_client=forced,
                     weights=Weights.load(ROOT / "weights.json"), top=25,
                     seen_place_ids=set())
            self.assertEqual(
                forced.stats["details_fetched"], first.stats["details_fetched"]
            )

    def test_bypass_still_writes_fresh_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            Cache(tmp, 30).set("ns", "k", {"v": "old"})
            bypassing = Cache(tmp, 30, bypass=True)
            self.assertIsNone(bypassing.get("ns", "k"))
            bypassing.get_or_fetch("ns", "k", lambda: {"v": "new"})
            self.assertEqual(Cache(tmp, 30).get("ns", "k"), {"v": "new"})



class TestSearchCacheSupersets(unittest.TestCase):
    """Changing --top must not re-buy a search the cache can already answer."""

    def _client_counting(self, cache, hits):
        def post(url, *, json_body, headers):
            if json_body.get("textQuery") == "Hitchin":
                return {"places": [{"location": {"latitude": 51.9, "longitude": -0.28}}]}
            hits.append(json_body)
            return {"places": [{"id": f"p{i}"} for i in range(20)]}

        return PlacesClient(api_key="k", post=post, cache=cache)

    def test_smaller_request_served_from_larger_cached_search(self):
        hits = []
        with tempfile.TemporaryDirectory() as tmp:
            cache = Cache(tmp, 30)
            self._client_counting(cache, hits).search("physio", "Hitchin", max_results=75)
            first = len(hits)
            out = self._client_counting(cache, hits).search(
                "physio", "Hitchin", max_results=60
            )
            self.assertEqual(len(hits), first)  # no new billable search
            self.assertEqual(len(out), 20)

    def test_exhausted_search_serves_any_larger_request(self):
        """20 results from a 75 cap means the town ran dry — asking for 90
        cannot find more, so it must not refetch."""
        hits = []
        with tempfile.TemporaryDirectory() as tmp:
            cache = Cache(tmp, 30)
            self._client_counting(cache, hits).search("physio", "Hitchin", max_results=75)
            first = len(hits)
            self._client_counting(cache, hits).search("physio", "Hitchin", max_results=90)
            self.assertEqual(len(hits), first)

    def test_genuinely_larger_request_refetches(self):
        hits = []
        with tempfile.TemporaryDirectory() as tmp:
            cache = Cache(tmp, 30)

            def post(url, *, json_body, headers):
                if json_body.get("textQuery") == "Hitchin":
                    return {"places": [{"location": {"latitude": 1.0, "longitude": 2.0}}]}
                hits.append(json_body)
                n = json_body["maxResultCount"]
                return {"places": [{"id": f"p{len(hits)}-{i}"} for i in range(n)],
                        "nextPageToken": "t"}

            client = PlacesClient(api_key="k", post=post, cache=cache)
            client.search("physio", "Hitchin", max_results=20)
            before = len(hits)
            client.search("physio", "Hitchin", max_results=40)
            self.assertGreater(len(hits), before)


class TestConcurrentEnrichment(unittest.TestCase):
    """Enrichment runs threaded; results must match the serial path."""

    def _run(self, workers):
        from tests.test_discover import StubChecker

        client = make_client()
        return discover(
            niche="physiotherapist", area="Hitchin", places_client=client,
            site_checker=StubChecker(), weights=Weights.load(ROOT / "weights.json"),
            top=25, workers=workers,
        )

    def test_threaded_matches_serial(self):
        serial = self._run(workers=1)
        threaded = self._run(workers=8)
        self.assertEqual(
            [(b["name"], b["lead_score"]) for b in serial.businesses],
            [(b["name"], b["lead_score"]) for b in threaded.businesses],
        )

    def test_one_failing_site_does_not_kill_the_run(self):
        class ExplodingChecker:
            def check(self, website):
                if website:  # the Facebook-only clinic has one
                    raise OSError("TLS handshake exploded")
                from pipeline.site_checks import SiteCheck

                c = SiteCheck(False, False)
                c.verdict = "none"
                return c

        client = make_client()
        result = discover(
            niche="physiotherapist", area="Hitchin", places_client=client,
            site_checker=ExplodingChecker(),
            weights=Weights.load(ROOT / "weights.json"), top=25, workers=8,
        )
        names = {b["name"] for b in result.businesses}
        self.assertIn("Bancroft Physio Rooms", names)  # unaffected rows survive
        broken = next(b for b in result.businesses
                      if b["name"] == "Walsworth Road Sports Injury Clinic")
        self.assertIn("enrich_error", broken)
        # The URL-only baseline verdict still stands for the broken row.
        self.assertEqual(broken["site_score"]["verdict"], "social_only")



class TestReRunForContactData(unittest.TestCase):
    """Re-running to pick up a lookup that did not exist before must not
    re-buy the Places data it already has."""

    def setUp(self):
        self.weights = Weights.load(ROOT / "weights.json")
        self.page = "<html><body><h3>Jay Madsen</h3><p>Practice Manager</p></body></html>"

    def _finder(self, cache, fetched):
        from pipeline.site_contacts import SiteContactFinder

        def http_get(url):
            fetched.append(url)
            return "" if "robots" in url else self.page

        return SiteContactFinder(http_get=http_get, cache=cache)

    def test_contacts_arrive_without_a_single_google_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Cache(tmp, 30)
            first = make_client(cache=cache)
            discover(niche="physiotherapist", area="Hitchin", places_client=first,
                     weights=self.weights, top=25, seen_place_ids=set())
            self.assertGreater(first.stats["details_fetched"], 0)

            fetched = []
            second = make_client(cache=cache)
            result = discover(
                niche="physiotherapist", area="Hitchin", places_client=second,
                weights=self.weights, top=25, seen_place_ids=set(),
                site_contacts=self._finder(cache, fetched),
            )
            # Not one billable Google call on the second run...
            self.assertEqual(second.stats["details_fetched"], 0)
            self.assertEqual(second.stats["search_fetched"], 0)
            # ...yet the contact data is now present.
            self.assertGreater(fetched.__len__(), 0)
            with_contacts = [b for b in result.businesses if b.get("site_contact")]
            self.assertTrue(with_contacts)
            self.assertEqual(with_contacts[0]["site_contact"]["name"], "Jay Madsen")

    def test_a_differently_typed_area_costs_a_fresh_search(self):
        """The cache key is the query text, so 'Hitchin' and
        'Hitchin, Hertfordshire' are different searches."""
        with tempfile.TemporaryDirectory() as tmp:
            cache = Cache(tmp, 30)
            discover(niche="physiotherapist", area="Hitchin",
                     places_client=make_client(cache=cache), weights=self.weights,
                     top=25, seen_place_ids=set())
            second = make_client(cache=cache)
            discover(niche="physiotherapist", area="Hitchin, Hertfordshire",
                     places_client=second, weights=self.weights, top=25,
                     seen_place_ids=set())
            self.assertGreater(second.stats["search_fetched"], 0)
            # The businesses themselves are still cached by place id.
            self.assertEqual(second.stats["details_fetched"], 0)


if __name__ == "__main__":
    unittest.main()
