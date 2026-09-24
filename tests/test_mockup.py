"""The Webflow brief: facts only, their words first, the banner always.

The mockup is posted to the business it depicts, so the thing most worth
pinning is what the prompt must never do — invent — and what it must
always carry: the concept banner and a visible placeholder for every gap.
"""

import json
import tempfile
import unittest
from pathlib import Path

from pipeline import mockup

try:
    from PIL import Image
except ImportError:  # pragma: no cover — Pillow is in requirements.txt
    Image = None

DATED_SITE = """<html><head><title>Bancroft Physio | Hitchin</title>
<meta name="description" content="Physiotherapy in Hitchin since 1998."></head>
<body><nav><a href="/">Home</a><a href="/back">Back pain</a><a href="/sport">Sports injuries</a>
<a href="/contact">Contact us</a></nav>
<h1>Welcome</h1><h1>Hands-on physiotherapy in the heart of Hitchin</h1>
<h2>Welcome to our website</h2><h2>Back pain</h2><h3>Post-operative rehab</h3>
<p>We treat back and neck pain, sports injuries and post-operative recovery, with
same-week appointments for new patients at our Bancroft clinic.</p>
<p>This site uses cookies to improve your experience of browsing and you accept this.</p>
<p>&copy; 2016 Bancroft Physio Rooms. All rights reserved and more words here.</p>
<script>var tracking = "Hands-on physiotherapy treatment for everyone here";</script>
</body></html>"""


def record(**overrides):
    base = {
        "place_id": "P1", "name": "Bancroft Physio", "niche": "physiotherapist",
        "address": {"town": "Hitchin", "formatted": "12 Bancroft, Hitchin SG5 1AA"},
        "phone": "01462 000101", "website": "https://bancroft.example",
        "rating": 4.8, "review_count": 74,
        "opening_hours": ["Monday: 9:00 AM – 5:00 PM", "Tuesday: Closed"],
        "reviews": [
            {"text": "Very knowledgeable. Booking by phone only is a pain though.", "rating": 5},
            {"text": "Good, but the waiting room was cold and cramped.", "rating": 3},
            {"text": "Fixed my shoulder when nobody else could. Brilliant.", "rating": 4},
            {"text": "Sorted my lower back out in three sessions. Lovely team.", "rating": 5},
            {"text": "Great.", "rating": 5},
        ],
        "site_contact": {"name": "Mark Patel", "role": "clinic manager"},
    }
    base.update(overrides)
    return base


def row(**overrides):
    base = {"place_id": "P1", "name": "Bancroft Physio", "town": "Hitchin"}
    base.update(overrides)
    return base


class TestLayoutShape(unittest.TestCase):
    def test_niches_map_to_the_four_masters(self):
        self.assertEqual(mockup.layout_shape("physiotherapist"), "clinic")
        self.assertEqual(mockup.layout_shape("Accountants"), "professional")
        self.assertEqual(mockup.layout_shape("plumber"), "trades")
        self.assertEqual(mockup.layout_shape("florist"), "generic")

    def test_places_types_count_when_the_niche_says_nothing(self):
        self.assertEqual(mockup.layout_shape("", ["dentist", "health"]), "clinic")
        self.assertEqual(mockup.layout_shape(""), "generic")


class TestExtractSiteContent(unittest.TestCase):
    def setUp(self):
        self.site = mockup.extract_site_content(DATED_SITE)

    def test_headline_skips_a_bare_welcome(self):
        self.assertEqual(self.site["headline"],
                         "Hands-on physiotherapy in the heart of Hitchin")

    def test_services_come_from_headings_and_nav_without_chrome(self):
        self.assertEqual(self.site["services"],
                         ["Back pain", "Post-operative rehab", "Sports injuries"])

    def test_paragraphs_drop_cookie_and_copyright_boilerplate(self):
        self.assertEqual(len(self.site["paragraphs"]), 1)
        self.assertIn("same-week appointments", self.site["paragraphs"][0])

    def test_title_and_description(self):
        self.assertEqual(self.site["title"], "Bancroft Physio | Hitchin")
        self.assertEqual(self.site["description"], "Physiotherapy in Hitchin since 1998.")

    def test_empty_or_broken_html_gives_empty_material(self):
        for html in ("", "<html><body><p>short</p>", None):
            site = mockup.extract_site_content(html)
            self.assertEqual((site["headline"], site["services"], site["paragraphs"]),
                             ("", [], []))


class TestPickReviews(unittest.TestCase):
    def test_best_first_and_no_complaints_or_scraps(self):
        picked = mockup.pick_reviews(record()["reviews"])
        self.assertEqual(picked, [
            "Sorted my lower back out in three sessions. Lovely team.",
            "Fixed my shoulder when nobody else could. Brilliant.",
        ])

    def test_verbatim_apart_from_whitespace(self):
        picked = mockup.pick_reviews([{"text": "  Superb   care\nfrom the whole team, thank you. ",
                                       "rating": 5}])
        self.assertEqual(picked, ["Superb care from the whole team, thank you."])

    def test_an_unrated_review_is_usable(self):
        self.assertEqual(len(mockup.pick_reviews(
            [{"text": "Sorted my lower back out in three sessions."}])), 1)


@unittest.skipIf(Image is None, "needs Pillow")
class TestBrandColour(unittest.TestCase):
    def shot(self, draw):
        folder = Path(tempfile.mkdtemp())
        path = folder / "P1_desktop.png"
        im = Image.new("RGB", (1280, 800), (250, 250, 250))
        draw(im)
        im.save(path)
        return path

    def test_the_brand_colour_not_the_white_background(self):
        def header(im):
            im.paste((0, 51, 102), (0, 0, 1280, 160))
            im.paste((30, 30, 30), (100, 300, 1100, 700))  # dark text block
        colour = mockup.brand_colour(self.shot(header))
        r, g, b = (int(colour[i:i + 2], 16) for i in (1, 3, 5))
        self.assertLess(abs(r - 0) + abs(g - 51) + abs(b - 102), 30)

    def test_greys_only_gives_none(self):
        self.assertIsNone(mockup.brand_colour(
            self.shot(lambda im: im.paste((128, 128, 128), (0, 0, 640, 800)))))

    def test_missing_or_unreadable_file_gives_none(self):
        self.assertIsNone(mockup.brand_colour(None))
        self.assertIsNone(mockup.brand_colour(Path("/nonexistent/x.png")))
        bad = Path(tempfile.mkdtemp()) / "bad.png"
        bad.write_bytes(b"not a png")
        self.assertIsNone(mockup.brand_colour(bad))


class TestBuildBrief(unittest.TestCase):
    def test_the_banner_is_always_there_and_names_you(self):
        brief = mockup.build_brief(row(), record(), your_business="Hitchin Web Studio")
        self.assertEqual(brief.banner, "Design concept for Bancroft Physio, prepared by "
                                       "Hitchin Web Studio. Not a live website.")
        self.assertIn(brief.banner, brief.prompt)

    def test_without_your_name_the_banner_says_so_visibly(self):
        brief = mockup.build_brief(row(), record())
        self.assertIn("[your business name]", brief.banner)

    def test_the_prompt_forbids_inventing_and_asks_for_placeholders(self):
        prompt = mockup.build_brief(row(), record()).prompt
        self.assertIn("Use ONLY the facts below", prompt)
        self.assertIn("never invent services", prompt)
        self.assertIn("Do NOT create any CMS Collections or Collection Lists", prompt)

    def test_facts_reach_the_prompt(self):
        prompt = mockup.build_brief(row(), record()).prompt
        for fact in ("01462 000101", "12 Bancroft, Hitchin SG5 1AA",
                     "4.8 stars from 74 Google reviews", "Monday: 9:00 AM – 5:00 PM",
                     "Mark Patel, clinic manager", "Physiotherapist in Hitchin"):
            self.assertIn(fact, prompt)

    def test_quotes_are_verbatim_and_a_complaint_never_is_quoted(self):
        prompt = mockup.build_brief(row(), record()).prompt
        self.assertIn('"Sorted my lower back out in three sessions. Lovely team."', prompt)
        self.assertNotIn("pain though", prompt)
        self.assertNotIn("waiting room", prompt)

    def test_every_missing_fact_becomes_a_visible_placeholder(self):
        bare = record(phone=None, opening_hours=[], reviews=[], address={"town": "Hitchin"})
        brief = mockup.build_brief(row(), bare)
        facts = dict(brief.facts)
        self.assertEqual(facts["Phone"], "[Add their phone number]")
        self.assertEqual(facts["Opening hours"], "[Add their opening hours]")
        self.assertEqual(facts["Address"], "[Add their address]")
        self.assertTrue(facts["Services"].startswith("[List their services"))
        self.assertEqual(len(brief.placeholders), 5)  # the four above, plus reviews
        self.assertIn("- Phone: [Add their phone number]", brief.prompt)
        self.assertNotIn("Real review quotes", brief.prompt)

    def test_their_own_wording_is_offered(self):
        brief = mockup.build_brief(row(), record(), site_html=DATED_SITE)
        self.assertIn("Hands-on physiotherapy in the heart of Hitchin", brief.prompt)
        self.assertIn("Back pain; Post-operative rehab; Sports injuries", brief.prompt)
        self.assertNotIn("Services", dict(brief.facts))  # found, so no placeholder

    def test_a_social_page_is_not_their_brand_or_their_words(self):
        brief = mockup.build_brief(
            row(), record(website="https://www.facebook.com/bancroft"),
            site_html=DATED_SITE, screenshot=Path("/nonexistent/fb.png"))
        self.assertEqual(brief.site, {})
        self.assertEqual(brief.accent, mockup.PLANS["clinic"]["palette"])
        self.assertIn("no website of their own", brief.accent_source)
        self.assertNotIn("from their current branding", brief.prompt)

    def test_the_shape_sets_the_call_to_action(self):
        self.assertIn("'Book an appointment'", mockup.build_brief(row(), record()).prompt)
        self.assertIn("'Get a quote'",
                      mockup.build_brief(row(), record(niche="plumber")).prompt)

    def test_a_row_alone_is_enough(self):
        brief = mockup.build_brief({"place_id": "P9", "name": "Dell Physio",
                                    "town": "Hitchin", "phone": "01462 1"}, None)
        self.assertEqual(dict(brief.facts)["Phone"], "01462 1")
        self.assertEqual(brief.shape, "generic")


class TestFiles(unittest.TestCase):
    def test_writes_prompt_json_and_sheet(self):
        folder = Path(tempfile.mkdtemp()) / "P1" / "mockup"
        brief = mockup.build_brief(row(), record(), site_html=DATED_SITE)
        paths = mockup.write(folder, brief)
        self.assertEqual(paths["prompt"].read_text(encoding="utf-8").strip(), brief.prompt)
        data = json.loads(paths["json"].read_text(encoding="utf-8"))
        self.assertEqual(data["banner"], brief.banner)
        self.assertEqual(data["shape"], "clinic")
        html = paths["html"].read_text(encoding="utf-8")
        self.assertIn("copyBlock(0, this)", html)
        self.assertIn("https://webflow.com/dashboard", html)

    def test_the_sheet_escapes_what_it_shows(self):
        brief = mockup.build_brief(row(name="Smith & <Sons>"), record(name="Smith & <Sons>"))
        html = mockup.render_html(brief)
        self.assertIn("Smith &amp; &lt;Sons&gt;", html)
        self.assertNotIn("<Sons>", html)


class TestCommandLine(unittest.TestCase):
    """mockup.py over a real batch folder: rows, records, cache, output."""

    def setUp(self):
        import os

        from pipeline.storage import Batch, business_to_row

        self.root = Path(tempfile.mkdtemp())
        self.batch = Batch(self.root / "2026-09-23_physio_hitchin")
        self.batch.path.mkdir(parents=True)
        self.cache = self.root / "cache"
        (self.cache / "screenshots").mkdir(parents=True)
        (self.cache / "screenshots" / "P1_page.json").write_text(
            json.dumps({"url": "https://bancroft.example", "html": DATED_SITE}),
            encoding="utf-8")
        second = record(place_id="P2", name="Dell Physio", website="")
        for rec in (record(), second):
            self.batch.write_business(rec)
        rows = [business_to_row(record()), business_to_row(second)]
        self.batch.write_shortlist(rows)
        self.batch.write_shortlist(rows[:1], path=self.batch.approved_path)
        self._env = {k: os.environ.get(k) for k in ("PIPELINE_CACHE_DIR", "YOUR_BUSINESS_NAME")}
        os.environ["PIPELINE_CACHE_DIR"] = str(self.cache)
        os.environ["YOUR_BUSINESS_NAME"] = "Hitchin Web Studio"

    def tearDown(self):
        import os

        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def run_cli(self, *argv):
        import contextlib
        import io

        import mockup as cli

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(["--batch", str(self.batch.path), *argv])
        return code, out.getvalue()

    def test_approved_briefs_only_the_kept_ones(self):
        code, _out = self.run_cli("--approved")
        self.assertEqual(code, 0)
        prompt = (self.batch.path / "P1" / "mockup" / "prompt.txt").read_text(encoding="utf-8")
        self.assertIn("prepared by Hitchin Web Studio", prompt)
        # Their own wording came from the saved page in the cache.
        self.assertIn("Hands-on physiotherapy in the heart of Hitchin", prompt)
        self.assertFalse((self.batch.path / "P2" / "mockup").exists())

    def test_named_places_and_an_unknown_one(self):
        code, out = self.run_cli("--place", "P2", "--place", "NOPE")
        self.assertEqual(code, 0)
        self.assertTrue((self.batch.path / "P2" / "mockup" / "brief.html").is_file())
        self.assertIn("NOPE: not in this batch", out)

    def test_your_business_flag_beats_the_env(self):
        self.run_cli("--place", "P1", "--your-business", "Pat Devine Design")
        data = json.loads((self.batch.path / "P1" / "mockup" / "mockup.json")
                          .read_text(encoding="utf-8"))
        self.assertIn("prepared by Pat Devine Design", data["banner"])

    def test_nothing_kept_is_a_clear_message(self):
        self.batch.approved_path.unlink()
        with self.assertRaises(SystemExit) as caught:
            self.run_cli("--approved")
        self.assertIn("approved.csv", str(caught.exception.code))


if __name__ == "__main__":
    unittest.main()
