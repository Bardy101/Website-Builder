"""Tests for who a letter should be addressed to.

The case that prompted this: Companies House named the director as "John
Madsen"; the practice website named a manager reachable at
jay@<domain>. Those are either one person filed under a middle name, or two
people — and the letter is wrong either way if the tool guesses.
"""

import unittest

from pipeline.addressee import (
    AGREE,
    DIFFER,
    LIKELY_SAME,
    NONE,
    OWNER_ONLY,
    SITE_ONLY,
    compare_names,
    decide,
)
from pipeline.companies_house import officer_name_parts


def owner(display, forenames, surname):
    return {
        "name": display,
        "parts": {"surname": surname, "forenames": forenames},
    }


def site(name, role="practice manager", email=None):
    return {"name": name, "role": role, "email": email}


class TestCompareNames(unittest.TestCase):
    def test_identical_names_agree(self):
        self.assertEqual(
            compare_names(owner("Sarah Smith", ["Sarah"], "Smith"), site("Sarah Smith")),
            AGREE,
        )

    def test_middle_name_on_the_website_agrees(self):
        """'MADSEN, John Jay' published as 'Jay Madsen' is one person."""
        self.assertEqual(
            compare_names(
                owner("John Madsen", ["John", "Jay"], "Madsen"), site("Jay Madsen")
            ),
            AGREE,
        )

    def test_same_surname_different_forename_is_likely_same(self):
        verdict = compare_names(
            owner("John Madsen", ["John"], "Madsen"), site("Jay Madsen")
        )
        self.assertEqual(verdict, LIKELY_SAME)

    def test_genuinely_different_people_differ(self):
        self.assertEqual(
            compare_names(owner("John Madsen", ["John"], "Madsen"), site("Jay Patel")),
            DIFFER,
        )

    def test_owner_only(self):
        self.assertEqual(
            compare_names(owner("John Madsen", ["John"], "Madsen"), None), OWNER_ONLY
        )

    def test_site_only(self):
        self.assertEqual(compare_names({}, site("Jay Patel")), SITE_ONLY)

    def test_nothing_found(self):
        self.assertEqual(compare_names({}, None), NONE)


class TestDecide(unittest.TestCase):
    def test_agreement_needs_no_human(self):
        result = decide(
            owner("John Madsen", ["John", "Jay"], "Madsen"),
            site("Jay Madsen"),
            company_type="ltd",
        )
        self.assertEqual(result["verdict"], AGREE)
        self.assertEqual(result["address_to"], "Jay Madsen")
        self.assertEqual(result["salutation"], "Dear Jay,")
        self.assertFalse(result["needs_human"])

    def test_conflict_is_flagged_for_a_human(self):
        result = decide(
            owner("John Madsen", ["John"], "Madsen"),
            site("Jay Patel"),
            company_type="ltd",
        )
        self.assertEqual(result["verdict"], DIFFER)
        self.assertTrue(result["needs_human"])
        self.assertIn("John Madsen", result["note"])
        self.assertIn("Jay Patel", result["note"])

    def test_conflict_defaults_to_the_person_who_runs_the_place(self):
        result = decide(
            owner("John Madsen", ["John"], "Madsen"), site("Jay Patel")
        )
        self.assertEqual(result["address_to"], "Jay Patel")

    def test_likely_same_asks_for_a_glance(self):
        result = decide(owner("John Madsen", ["John"], "Madsen"), site("Jay Madsen"))
        self.assertEqual(result["verdict"], LIKELY_SAME)
        self.assertTrue(result["needs_human"])

    def test_nothing_found_falls_back_to_the_spec_wording(self):
        result = decide(None, None)
        self.assertIsNone(result["address_to"])
        self.assertEqual(result["salutation"], "FAO the Owner / Practice Manager")
        self.assertFalse(result["needs_human"])

    def test_site_only_warns_about_sole_trader_email_rules(self):
        result = decide({}, site("Jay Patel"), company_type="unknown")
        self.assertEqual(result["verdict"], SITE_ONLY)
        self.assertIn("never email uninvited", result["note"])

    def test_site_only_on_a_ltd_does_not_warn(self):
        result = decide({}, site("Jay Patel"), company_type="ltd")
        self.assertNotIn("never email", result["note"])

    def test_owner_only_addresses_the_director(self):
        result = decide(owner("Sarah Smith", ["Sarah"], "Smith"), None)
        self.assertEqual(result["address_to"], "Sarah Smith")
        self.assertEqual(result["salutation"], "Dear Sarah,")


class TestOfficerNameParts(unittest.TestCase):
    def test_middle_names_are_kept(self):
        parts = officer_name_parts("MADSEN, John Jay")
        self.assertEqual(parts["forenames"], ["John", "Jay"])
        self.assertEqual(parts["surname"], "Madsen")
        self.assertEqual(parts["display"], "John Madsen")

    def test_single_forename(self):
        parts = officer_name_parts("SMITH, Sarah")
        self.assertEqual(parts["forenames"], ["Sarah"])

    def test_name_without_a_comma(self):
        parts = officer_name_parts("Jay Madsen")
        self.assertEqual(parts["surname"], "Madsen")
        self.assertEqual(parts["forenames"], ["Jay"])


if __name__ == "__main__":
    unittest.main()
