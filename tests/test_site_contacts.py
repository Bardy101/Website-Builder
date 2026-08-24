"""Tests for reading a named contact off the business's own website.

Spec section 5 step 2. Everything here is confidence 'low' by design — it is
pattern matching over marketing copy, offered to a human, never trusted.
"""

import unittest

from pipeline.site_contacts import (
    SiteContactFinder,
    choose_site_contact,
    classify_email,
    find_emails,
    find_people,
)

TEAM_PAGE = """
<html><head><title>MVMNT Physio</title><style>.x{color:red}</style></head>
<body>
<h1>Meet the team</h1>
<div><h3>Jay Madsen</h3><p>Practice Manager</p>
<a href="mailto:jay@example.co.uk">Email Jay</a></div>
<div><h3>Sarah Whitfield</h3><p>Lead Physiotherapist</p></div>
<p>General enquiries: info@example.co.uk</p>
<script>var email = "tracker@analytics.com";</script>
</body></html>
"""


class TestEmailClassification(unittest.TestCase):
    def test_personal_mailbox(self):
        self.assertEqual(classify_email("jay@example.co.uk"), "personal")
        self.assertEqual(classify_email("sarah.smith@example.co.uk"), "personal")

    def test_generic_mailbox(self):
        for address in ("info@x.co.uk", "hello@x.co.uk", "reception@x.co.uk",
                        "bookings@x.co.uk", "enquiries@x.co.uk"):
            self.assertEqual(classify_email(address), "generic", address)

    def test_dotted_generic_is_still_generic(self):
        self.assertEqual(classify_email("info.desk@x.co.uk"), "generic")


class TestExtraction(unittest.TestCase):
    def test_finds_named_people_with_roles(self):
        people = find_people(TEAM_PAGE)
        pairs = {(p["name"], p["role"]) for p in people}
        self.assertIn(("Jay Madsen", "practice manager"), pairs)
        self.assertIn(("Sarah Whitfield", "lead physiotherapist"), pairs)

    def test_job_titles_are_not_mistaken_for_names(self):
        names = {p["name"] for p in find_people(TEAM_PAGE)}
        for title in ("Practice Manager", "Lead Physiotherapist", "Meet The"):
            self.assertNotIn(title, names)

    def test_names_do_not_span_separate_elements(self):
        """'...Physio' + 'Meet the team' must not become a person."""
        names = {p["name"] for p in find_people(TEAM_PAGE)}
        self.assertFalse(any("Physio" in n for n in names), names)

    def test_finds_both_emails(self):
        found = {e["email"]: e["kind"] for e in find_emails(TEAM_PAGE)}
        self.assertEqual(found.get("jay@example.co.uk"), "personal")
        self.assertEqual(found.get("info@example.co.uk"), "generic")

    def test_script_contents_are_ignored(self):
        found = {e["email"] for e in find_emails(TEAM_PAGE)}
        self.assertNotIn("tracker@analytics.com", found)

    def test_empty_html_is_safe(self):
        self.assertEqual(find_people(""), [])
        self.assertEqual(find_emails(""), [])


class TestChooseContact(unittest.TestCase):
    def test_prefers_the_more_senior_role(self):
        people = [
            {"name": "Sarah Whitfield", "role": "lead physiotherapist"},
            {"name": "Jay Madsen", "role": "owner"},
        ]
        chosen = choose_site_contact(people, [])
        self.assertEqual(chosen["name"], "Jay Madsen")

    def test_pairs_a_matching_personal_email(self):
        chosen = choose_site_contact(
            [{"name": "Jay Madsen", "role": "owner"}],
            [{"email": "jay@example.co.uk", "kind": "personal"},
             {"email": "info@example.co.uk", "kind": "generic"}],
        )
        self.assertEqual(chosen["email"], "jay@example.co.uk")

    def test_does_not_invent_an_email(self):
        chosen = choose_site_contact(
            [{"name": "Jay Madsen", "role": "owner"}],
            [{"email": "info@example.co.uk", "kind": "generic"}],
        )
        self.assertIsNone(chosen["email"])

    def test_no_people_means_no_contact(self):
        self.assertIsNone(choose_site_contact([], [{"email": "info@x.uk", "kind": "generic"}]))

    def test_other_names_exclude_the_chosen_person(self):
        chosen = choose_site_contact(
            [{"name": "Jay Madsen", "role": "owner"},
             {"name": "Jay Madsen", "role": "director"},
             {"name": "Sarah Whitfield", "role": "lead physiotherapist"}],
            [],
        )
        self.assertEqual(chosen["other_names"], ["Sarah Whitfield"])


class TestFinder(unittest.TestCase):
    def test_end_to_end_against_a_stub_site(self):
        finder = SiteContactFinder(
            http_get=lambda url: "" if "robots" in url else TEAM_PAGE
        )
        result = finder.find("https://example.co.uk/")
        self.assertEqual(result["contact"]["name"], "Jay Madsen")
        self.assertEqual(result["contact"]["confidence"], "low")

    def test_no_website_means_no_lookup(self):
        def boom(url):
            raise AssertionError("should not fetch without a website")

        self.assertIsNone(SiteContactFinder(http_get=boom).find(None))

    def test_robots_disallow_is_respected(self):
        def get(url):
            if "robots" in url:
                return "User-agent: *\nDisallow: /"
            raise AssertionError("must not fetch a disallowed page")

        finder = SiteContactFinder(http_get=get)
        result = finder.find("https://example.co.uk/")
        self.assertIsNone(result["contact"])
        self.assertEqual(result["pages_read"], [])
        self.assertGreater(finder.stats["skipped_by_robots"], 0)

    def test_unreachable_site_degrades_quietly(self):
        finder = SiteContactFinder(http_get=lambda url: None)
        result = finder.find("https://down.example/")
        self.assertIsNone(result["contact"])

    def test_page_budget_is_respected(self):
        calls = []

        def get(url):
            if "robots" in url:
                return ""
            calls.append(url)
            return TEAM_PAGE

        finder = SiteContactFinder(http_get=get, max_pages=2)
        finder.find("https://example.co.uk/")
        self.assertLessEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
