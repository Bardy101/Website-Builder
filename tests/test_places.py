"""Tests for Places normalisation and the client's caching/paging."""

import unittest

from pipeline.places import (
    PlacesClient,
    PlacesError,
    most_recent_review_date,
    normalise_place,
)

DETAIL = {
    "id": "ChIJabc",
    "displayName": {"text": "Bancroft Physio Rooms"},
    "formattedAddress": "12 Bancroft, Hitchin SG5 1JQ, UK",
    "addressComponents": [
        {"longText": "12", "types": ["street_number"]},
        {"longText": "Bancroft", "types": ["route"]},
        {"longText": "Hitchin", "types": ["postal_town"]},
        {"longText": "SG5 1JQ", "types": ["postal_code"]},
    ],
    "nationalPhoneNumber": "01462 000101",
    "websiteUri": "https://example.co.uk",
    "googleMapsUri": "https://maps.google.com/?cid=1",
    "rating": 4.9,
    "userRatingCount": 74,
    "businessStatus": "OPERATIONAL",
    "regularOpeningHours": {"weekdayDescriptions": ["Monday: 08:00 – 18:00"]},
    "photos": [{"name": "p/1"}, {"name": "p/2"}],
    "reviews": [
        {"originalText": {"text": "Great"}, "rating": 5, "publishTime": "2026-08-02T10:00:00Z"},
        {"originalText": {"text": "Good"}, "rating": 4, "publishTime": "2026-05-01T10:00:00Z"},
    ],
}


class TestNormalise(unittest.TestCase):
    def test_flattens_expected_fields(self):
        b = normalise_place(DETAIL)
        self.assertEqual(b["place_id"], "ChIJabc")
        self.assertEqual(b["name"], "Bancroft Physio Rooms")
        self.assertEqual(b["address"]["line1"], "12 Bancroft")
        self.assertEqual(b["address"]["town"], "Hitchin")
        self.assertEqual(b["address"]["postcode"], "SG5 1JQ")
        self.assertEqual(b["phone"], "01462 000101")
        self.assertEqual(b["review_count"], 74)
        self.assertEqual(b["photo_count"], 2)
        self.assertEqual(len(b["reviews"]), 2)

    def test_missing_fields_are_none_not_guessed(self):
        b = normalise_place({"id": "X"})
        self.assertIsNone(b["name"])
        self.assertIsNone(b["website"])
        self.assertIsNone(b["address"]["postcode"])
        self.assertEqual(b["photo_count"], 0)
        self.assertEqual(b["reviews"], [])

    def test_recent_review_date(self):
        b = normalise_place(DETAIL)
        self.assertEqual(most_recent_review_date(b), "2026-08-02")

    def test_recent_review_date_none_when_no_reviews(self):
        self.assertIsNone(most_recent_review_date(normalise_place({"id": "X"})))

    def test_reviews_capped_at_five(self):
        detail = dict(DETAIL)
        detail["reviews"] = [
            {"originalText": {"text": f"r{i}"}, "rating": 5, "publishTime": "2026-01-0"
             f"{i}T10:00:00Z"}
            for i in range(1, 9)
        ]
        self.assertEqual(len(normalise_place(detail)["reviews"]), 5)


class TestPlacesClient(unittest.TestCase):
    def test_missing_key_raises_clear_error(self):
        client = PlacesClient(api_key=None)
        with self.assertRaises(PlacesError) as ctx:
            client.search("physio", "Hitchin")
        self.assertIn("GOOGLE_PLACES_API_KEY", str(ctx.exception))

    def test_search_follows_pagination(self):
        pages = [
            {"places": [{"id": f"a{i}"} for i in range(20)], "nextPageToken": "t1"},
            {"places": [{"id": f"b{i}"} for i in range(20)]},
        ]
        search_calls = []

        def post(url, *, json_body, headers):
            # The centre lookup searches for the bare area name.
            if json_body.get("textQuery") == "Hitchin":
                return {"places": [{"location": {"latitude": 51.9, "longitude": -0.28}}]}
            search_calls.append(json_body)
            return pages[len(search_calls) - 1]

        client = PlacesClient(api_key="k", post=post)
        results = client.search("physio", "Hitchin", max_results=40)
        self.assertEqual(len(results), 40)
        self.assertEqual(search_calls[1]["pageToken"], "t1")

    def test_search_respects_max_results(self):
        def post(url, *, json_body, headers):
            if json_body.get("textQuery") == "H":
                return {"places": [{"location": {"latitude": 1.0, "longitude": 2.0}}]}
            return {"places": [{"id": f"a{i}"} for i in range(20)], "nextPageToken": "t"}

        client = PlacesClient(api_key="k", post=post)
        self.assertEqual(len(client.search("p", "H", max_results=5)), 5)

    def test_details_uses_cache(self):
        from pipeline.cache import Cache
        import tempfile

        calls = []

        def get(url, *, headers):
            calls.append(url)
            return DETAIL

        with tempfile.TemporaryDirectory() as tmp:
            client = PlacesClient(api_key="k", get=get, cache=Cache(tmp))
            client.details("ChIJabc")
            client.details("ChIJabc")
            self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
