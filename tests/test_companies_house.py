"""Tests for the Companies House owner lookup."""

import unittest

from pipeline.companies_house import (
    CompaniesHouseClient,
    format_officer_name,
    name_similarity,
    normalise_company_name,
)


class TestNameHandling(unittest.TestCase):
    def test_normalise_strips_suffixes(self):
        self.assertEqual(
            normalise_company_name("Bancroft Physio Rooms Limited"),
            "bancroft physio rooms",
        )
        self.assertEqual(normalise_company_name("The Physio Co. Ltd"), "physio")

    def test_similarity(self):
        self.assertGreater(
            name_similarity("Bancroft Physio Rooms", "BANCROFT PHYSIO ROOMS LTD"), 0.9
        )
        self.assertLess(name_similarity("Bancroft Physio", "Royston Dental"), 0.2)

    def test_officer_name_formatting(self):
        self.assertEqual(format_officer_name("SMITH, Sarah Jane"), "Sarah Smith")
        self.assertEqual(format_officer_name("PATEL, Ravi"), "Ravi Patel")


class TestLookup(unittest.TestCase):
    def _client(self, search_result, officers_result=None):
        def get(url, params=None):
            if "search" in url:
                return search_result
            return officers_result

        return CompaniesHouseClient(api_key="k", get=get)

    def test_matched_company_returns_director(self):
        client = self._client(
            {
                "items": [
                    {
                        "title": "BANCROFT PHYSIO ROOMS LIMITED",
                        "company_number": "12345678",
                        "company_status": "active",
                        "company_type": "ltd",
                        "address": {"postal_code": "SG5 1JQ"},
                    }
                ]
            },
            {
                "items": [
                    {"name": "SMITH, Sarah Jane", "officer_role": "director"},
                ]
            },
        )
        out = client.lookup("Bancroft Physio Rooms", "SG5 1JQ")
        self.assertEqual(out["owner"]["name"], "Sarah Smith")
        self.assertEqual(out["owner"]["confidence"], "high")
        self.assertEqual(out["company"]["type"], "ltd")
        self.assertEqual(out["company"]["number"], "12345678")

    def test_resigned_directors_skipped(self):
        client = self._client(
            {
                "items": [
                    {
                        "title": "BANCROFT PHYSIO ROOMS LIMITED",
                        "company_number": "1",
                        "company_status": "active",
                        "company_type": "ltd",
                        "address": {"postal_code": "SG5 1JQ"},
                    }
                ]
            },
            {
                "items": [
                    {"name": "OLD, Owner", "officer_role": "director",
                     "resigned_on": "2020-01-01"},
                    {"name": "NEW, Owner", "officer_role": "director"},
                ]
            },
        )
        out = client.lookup("Bancroft Physio Rooms", "SG5 1JQ")
        self.assertEqual(out["owner"]["name"], "Owner New")

    def test_weak_match_is_discarded_not_guessed(self):
        client = self._client(
            {
                "items": [
                    {
                        "title": "TOTALLY UNRELATED HOLDINGS LIMITED",
                        "company_number": "9",
                        "company_status": "active",
                        "company_type": "ltd",
                        "address": {"postal_code": "EC1A 1BB"},
                    }
                ]
            }
        )
        out = client.lookup("Bancroft Physio Rooms", "SG5 1JQ")
        self.assertIsNone(out["owner"]["name"])
        self.assertEqual(out["owner"]["confidence"], "none")
        self.assertEqual(out["company"]["type"], "unknown")

    def test_no_results_returns_blank(self):
        client = self._client({"items": []})
        out = client.lookup("Nobody", "SG5 1JQ")
        self.assertIsNone(out["owner"]["name"])

    def test_no_api_key_returns_blank_without_crashing(self):
        client = CompaniesHouseClient(api_key=None)
        out = client.lookup("Bancroft Physio Rooms", "SG5 1JQ")
        self.assertIsNone(out["owner"]["name"])
        self.assertEqual(out["company"]["type"], "unknown")

    def test_postcode_match_rescues_partial_name(self):
        client = self._client(
            {
                "items": [
                    {
                        "title": "BANCROFT ROOMS LIMITED",
                        "company_number": "5",
                        "company_status": "active",
                        "company_type": "ltd",
                        "address": {"postal_code": "SG5 1JQ"},
                    }
                ]
            },
            {"items": [{"name": "JONES, Alice", "officer_role": "director"}]},
        )
        out = client.lookup("Bancroft Physio Rooms", "SG5 1JQ")
        self.assertEqual(out["owner"]["name"], "Alice Jones")


if __name__ == "__main__":
    unittest.main()
