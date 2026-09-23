"""The detail pane's content: everything the spreadsheet showed, and more.

Pure — no window — so what the pane says about a business is checked here.
"""

import unittest

from pipeline import details
from pipeline.storage import business_to_row


def record(**overrides):
    base = {
        "place_id": "P1", "name": "Bancroft Physio", "niche": "physiotherapist",
        "address": {"town": "Hitchin", "postcode": "SG5 1AA",
                    "formatted": "12 Bancroft, Hitchin SG5 1AA"},
        "phone": "01462 000101", "website": "https://bancroft.example",
        "maps_url": "https://maps.google.com/?cid=1",
        "rating": 4.9, "review_count": 74,
        "reviews": [{"text": "Sorted my back in two sessions.", "time": "2026-09-01T10:00:00Z"}],
        "site_score": {"verdict": "dated", "mobile_score": 41, "https": False},
        "staleness": {"has_viewport": False, "has_media_queries": False,
                      "copyright_year": 2016, "uses_table_layout": True,
                      "platform_hint": "wordpress"},
        "owner": {"name": "Jane Cooper"}, "company": {"type": "ltd"},
        "site_contact": {"name": "Jay Madsen", "role": "manager"},
        "addressee": {"address_to": "Jay Madsen", "note": "Website names the manager.",
                      "needs_human": True, "verdict": "differ"},
        "opening_hours": ["Monday: 9:00 AM – 5:00 PM", "Tuesday: Closed"],
        "score_breakdown": {"no_viewport": 25, "no_media_queries": 15,
                            "established_and_reputable": 15, "too_few_reviews": -20},
        "lead_score": 35,
    }
    base.update(overrides)
    return base


def section(d, heading):
    return dict(next(rows for h, rows in d.sections if h == heading))


class TestBuild(unittest.TestCase):
    def setUp(self):
        self.b = record()
        self.d = details.build(business_to_row(self.b), self.b)

    def test_header(self):
        self.assertEqual(self.d.name, "Bancroft Physio")
        self.assertEqual(self.d.subtitle, "physiotherapist · Hitchin")
        self.assertEqual(self.d.score, "35")
        self.assertEqual(self.d.verdict_label, "Dated site")
        self.assertEqual(self.d.verdict_tone, "good")

    def test_every_spreadsheet_contact_field_is_shown(self):
        contact = section(self.d, "Contact")
        self.assertEqual(contact["Phone"], "01462 000101")
        self.assertIn("Bancroft", contact["Address"])
        self.assertEqual(contact["Postcode"], "SG5 1AA")
        self.assertEqual(contact["Website"], "https://bancroft.example")

    def test_who_to_write_to_names_both_sources(self):
        who = section(self.d, "Who to write to")
        self.assertEqual(who["Address to"], "Jay Madsen")
        self.assertEqual(who["Companies House"], "Jane Cooper — ltd")
        self.assertEqual(who["On their website"], "Jay Madsen (manager)")
        self.assertTrue(any("different people" in w for w in self.d.warnings))

    def test_email_is_labelled_not_for_outreach(self):
        b = record(site_contact={"name": "Jay", "role": "manager", "email": "jay@x.example"})
        contact = section(details.build(business_to_row(b), b), "Contact")
        self.assertIn("not to email", contact["Email on site"])

    def test_website_evidence_is_plain_english(self):
        site = section(self.d, "The website")
        self.assertEqual(site["Works on phones"], "No")
        self.assertEqual(site["Adapts to screen"], "No")
        self.assertEqual(site["HTTPS"], "No")
        self.assertEqual(site["Copyright year"], "2016")
        self.assertEqual(site["Table layout"], "Yes")
        self.assertEqual(site["Platform"], "wordpress")

    def test_breakdown_explains_the_score_biggest_first(self):
        self.assertEqual(self.d.breakdown[0], ("Not built for phones (no viewport tag)", 25))
        self.assertIn(("Very few reviews", -20), self.d.breakdown)
        self.assertEqual(sum(p for _, p in self.d.breakdown), 35)

    def test_quotes_and_hours(self):
        self.assertEqual(self.d.quotes, ["Sorted my back in two sessions."])
        self.assertIn("Tuesday: Closed", dict(section(self.d, "Opening hours"))[""])

    def test_long_quote_is_trimmed(self):
        b = record(reviews=[{"text": "x" * 500}])
        self.assertLessEqual(len(details.build({}, b).quotes[0]), 240)

    def test_unknown_is_blank_not_guessed(self):
        b = record(staleness={}, site_score={"verdict": "unknown"})
        row = business_to_row(b)
        site = section(details.build(row, b), "The website")
        self.assertNotIn("Works on phones", site)
        self.assertNotIn("Table layout", site)

    def test_empty_sections_are_dropped(self):
        d = details.build({"place_id": "X", "name": "Bare"}, {})
        headings = [h for h, _ in d.sections]
        self.assertNotIn("Opening hours", headings)
        self.assertNotIn("Contact", headings)

    def test_failed_lookup_is_a_warning(self):
        b = record(enrich_errors={"owner lookup": "ConnectionError: timed out"})
        d = details.build(business_to_row(b), b)
        self.assertTrue(any("owner lookup failed" in w for w in d.warnings))

    def test_row_alone_is_enough(self):
        # No business.json (old combined batch): the row still tells a story.
        d = details.build(business_to_row(self.b), None)
        self.assertEqual(section(d, "Contact")["Phone"], "01462 000101")
        self.assertEqual(d.breakdown, [])

    def test_maps_link_built_from_place_id_when_missing(self):
        d = details.build({"place_id": "ChIJabc", "name": "X"}, {})
        self.assertIn("place_id:ChIJabc", d.maps_url)


class TestExcluded(unittest.TestCase):
    def test_excluded_row_leads_with_why(self):
        d = details.build(
            {"place_id": "E1", "name": "Old Mill", "town": "Hitchin"}, None,
            excluded={"reason": "dormant", "detail": "last review 2023-02-01",
                      "review_count": "12", "last_review": "2023-02-01",
                      "site_verdict": "none"})
        self.assertIn("dormant — last review 2023-02-01", d.warnings[0])
        known = dict(d.sections[0][1])
        self.assertEqual(known["Last review"], "2023-02-01")
        self.assertEqual(known["Site"], "No website")


if __name__ == "__main__":
    unittest.main()
