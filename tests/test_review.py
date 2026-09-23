"""The editable review sheet: load, decide, save, reopen, find it as left.

The point of these is the round trip. A sheet that saves but comes back
blank, or that forgets an un-cull, is worse than the console prompt it
replaces, because it looks like it worked.
"""

import json
import tempfile
import unittest
from pathlib import Path

from pipeline import review
from pipeline.storage import Batch, business_to_row, utcnow


def business(place_id, name, score=50, **overrides):
    base = {
        "place_id": place_id,
        "name": name,
        "niche": "physiotherapist",
        "business_status": "OPERATIONAL",
        "address": {"town": "Hitchin", "postcode": "SG5 1AA",
                    "formatted": "1 High St, Hitchin"},
        "website": None,
        "rating": 4.8,
        "review_count": 40,
        "site_score": {"verdict": "none"},
        "photo_count": 0,
        "reviews": [],
        "owner": {"name": "Jane Doe"},
        "company": {"type": "ltd"},
        "addressee": {"address_to": "Jane Doe"},
        "lead_score": score,
    }
    base.update(overrides)
    return base


class ReviewTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.batch = Batch.create(self.tmp.name, niche="physiotherapist",
                                  area="hitchin", name="b1")
        self.people = [
            business("P1", "Alpha Physio", 70),
            business("P2", "Beta Backs", 60),
            business("P3", "Gamma Clinic", 50),
        ]
        for b in self.people:
            self.batch.write_business(b)
        self.batch.write_shortlist(business_to_row(b) for b in self.people)

    def tearDown(self):
        self.tmp.cleanup()

    def reload(self):
        return review.load(Batch(self.batch.path))


class TestLoad(ReviewTestCase):
    def test_fresh_batch_is_all_undecided(self):
        sheet = self.reload()
        self.assertEqual(len(sheet), 3)
        self.assertFalse(sheet.culled_before)
        self.assertEqual(sheet.counts(), {review.KEEP: 0, review.CULL: 0,
                                          review.UNDECIDED: 3})

    def test_rows_keep_shortlist_order(self):
        self.assertEqual([r.name for r in self.reload().rows],
                         ["Alpha Physio", "Beta Backs", "Gamma Clinic"])

    def test_reads_decisions_made_by_cull_py(self):
        # cull.py's artefacts, written exactly as it writes them.
        self.batch.write_shortlist(
            [business_to_row(self.people[0]), business_to_row(self.people[2])],
            path=self.batch.approved_path)
        self.batch.append_rejection({
            "place_id": "P2", "name": "Beta Backs", "reason": "site_fine",
            "rejected_at": "2026-09-01T10:00:00Z", "row": {}, "business": {}})

        sheet = self.reload()
        self.assertTrue(sheet.culled_before)
        self.assertEqual(sheet.by_id("P1").decision, review.KEEP)
        self.assertEqual(sheet.by_id("P3").decision, review.KEEP)
        beta = sheet.by_id("P2")
        self.assertEqual(beta.decision, review.CULL)
        self.assertEqual(beta.reason, "site_fine")

    def test_last_word_wins_for_a_repeated_rejection(self):
        for reason in ("gut", "too_small"):
            self.batch.append_rejection({
                "place_id": "P2", "name": "Beta Backs", "reason": reason,
                "rejected_at": utcnow(), "row": {}, "business": {}})
        self.assertEqual(self.reload().by_id("P2").reason, "too_small")

    def test_approved_row_missing_from_shortlist_is_still_shown(self):
        # An old batch culled, then its shortlist regenerated smaller.
        self.batch.write_shortlist(
            [business_to_row(b) for b in self.people],
            path=self.batch.approved_path)
        self.batch.write_shortlist([business_to_row(self.people[0])])
        sheet = self.reload()
        self.assertEqual(len(sheet), 3)
        self.assertEqual(sheet.by_id("P3").decision, review.KEEP)


class TestSaveRoundTrip(ReviewTestCase):
    def test_decisions_survive_a_reload(self):
        sheet = self.reload()
        sheet.by_id("P1").decision = review.KEEP
        sheet.by_id("P2").decision = review.CULL
        sheet.by_id("P2").reason = "chain"
        result = review.save(self.batch, sheet)
        self.assertEqual((result["approved"], result["rejected"]), (2, 1))

        back = self.reload()
        self.assertEqual(back.by_id("P1").decision, review.KEEP)
        self.assertEqual(back.by_id("P2").decision, review.CULL)
        self.assertEqual(back.by_id("P2").reason, "chain")

    def test_notes_survive_a_reload(self):
        sheet = self.reload()
        sheet.by_id("P1").set("notes", "rang, ask for Dave")
        review.save(self.batch, sheet)
        self.assertEqual(self.reload().by_id("P1").notes, "rang, ask for Dave")

    def test_edited_addressee_survives_a_reload(self):
        sheet = self.reload()
        sheet.by_id("P1").set("address_to", "Jay Madsen")
        review.save(self.batch, sheet)
        self.assertEqual(self.reload().by_id("P1").get("address_to"), "Jay Madsen")

    def test_notes_survive_a_regenerated_shortlist(self):
        # The side-car exists precisely for this: discover rewrites
        # shortlist.csv and approved.csv is not consulted for a fresh row.
        sheet = self.reload()
        sheet.by_id("P2").set("notes", "second site, same owner")
        review.save(self.batch, sheet)
        self.batch.write_shortlist(business_to_row(b) for b in self.people)
        self.assertEqual(self.reload().by_id("P2").notes, "second site, same owner")

    def test_un_culling_removes_the_rejection(self):
        sheet = self.reload()
        sheet.by_id("P2").decision = review.CULL
        sheet.by_id("P2").reason = "gut"
        review.save(self.batch, sheet)
        self.assertEqual(len(self.batch.read_rejections()), 1)

        back = self.reload()
        back.by_id("P2").decision = review.KEEP
        back.by_id("P2").reason = ""
        review.save(self.batch, back)

        self.assertEqual(self.batch.read_rejections(), [])
        self.assertEqual(self.reload().by_id("P2").decision, review.KEEP)

    def test_original_rejected_at_is_not_restated(self):
        sheet = self.reload()
        sheet.by_id("P2").decision = review.CULL
        sheet.by_id("P2").reason = "gut"
        review.save(self.batch, sheet)
        first = self.batch.read_rejections()[0]["rejected_at"]

        again = self.reload()
        again.by_id("P3").decision = review.CULL
        again.by_id("P3").reason = "too_small"
        review.save(self.batch, again)

        by_id = {r["place_id"]: r for r in self.batch.read_rejections()}
        self.assertEqual(by_id["P2"]["rejected_at"], first)

    def test_undecided_rows_are_kept_not_rejected(self):
        sheet = self.reload()
        sheet.by_id("P1").decision = review.CULL
        sheet.by_id("P1").reason = "chain"
        result = review.save(self.batch, sheet)
        self.assertEqual(result["approved"], 2)
        self.assertEqual(
            {r["place_id"] for r in self.batch.read_shortlist(
                path=self.batch.approved_path)}, {"P2", "P3"})

    def test_rejection_carries_the_full_business_record(self):
        # tune.py learns from these; a rejection without its record is noise.
        sheet = self.reload()
        sheet.by_id("P2").decision = review.CULL
        sheet.by_id("P2").reason = "site_fine"
        review.save(self.batch, sheet)
        rej = self.batch.read_rejections()[0]
        self.assertEqual(rej["business"]["name"], "Beta Backs")
        self.assertEqual(rej["business"]["lead_score"], 60)

    def test_meta_counts_updated(self):
        sheet = self.reload()
        sheet.by_id("P2").decision = review.CULL
        sheet.by_id("P2").reason = "gut"
        review.save(self.batch, sheet)
        meta = self.batch.read_meta()
        self.assertEqual(meta["status_counts"]["approved"], 2)
        self.assertEqual(meta["status_counts"]["rejected"], 1)
        self.assertEqual(meta["rejection_reasons"], {"gut": 1})


class TestValidation(ReviewTestCase):
    def test_cull_without_a_reason_is_refused(self):
        sheet = self.reload()
        sheet.by_id("P1").decision = review.CULL
        self.assertTrue(review.validate(sheet))
        with self.assertRaises(ValueError) as caught:
            review.save(self.batch, sheet)
        self.assertIn("no reason code", str(caught.exception))

    def test_unknown_reason_code_is_refused(self):
        sheet = self.reload()
        sheet.by_id("P1").decision = review.CULL
        sheet.by_id("P1").reason = "did not like the logo"
        with self.assertRaises(ValueError):
            review.save(self.batch, sheet)

    def test_nothing_is_written_when_validation_fails(self):
        sheet = self.reload()
        sheet.by_id("P1").decision = review.CULL
        with self.assertRaises(ValueError):
            review.save(self.batch, sheet)
        self.assertFalse(self.batch.approved_path.is_file())

    def test_measured_columns_cannot_be_edited(self):
        row = self.reload().rows[0]
        with self.assertRaises(ValueError):
            row.set("lead_score", "999")


class TestLegacySidecar(ReviewTestCase):
    """review.json is retired: read as a fallback, removed on the next save."""

    def write_legacy(self, rows):
        path = review.sidecar_path(Batch(self.batch.path))
        path.write_text(json.dumps({"saved": utcnow(), "rows": rows}), encoding="utf-8")
        return path

    def test_legacy_notes_are_read_when_the_row_has_no_notes_column(self):
        # approved.csv written by an old cull.py, before the notes column.
        self.batch.approved_path.write_text(
            "lead_score,name,place_id\n70,Alpha Physio,P1\n", encoding="utf-8")
        self.write_legacy({"P1": {"notes": "from the old side-car"}})
        self.assertEqual(self.reload().by_id("P1").notes, "from the old side-car")

    def test_legacy_sidecar_removed_on_save_and_notes_kept(self):
        self.batch.approved_path.write_text(
            "lead_score,name,place_id\n70,Alpha Physio,P1\n", encoding="utf-8")
        path = self.write_legacy({"P1": {"notes": "keep me"}})
        review.save(self.batch, self.reload())
        self.assertFalse(path.is_file())
        self.assertEqual(self.reload().by_id("P1").notes, "keep me")

    def test_corrupt_sidecar_is_ignored(self):
        sheet = self.reload()
        sheet.by_id("P1").decision = review.CULL
        sheet.by_id("P1").reason = "chain"
        review.save(self.batch, sheet)
        review.sidecar_path(Batch(self.batch.path)).write_text("{ not json",
                                                              encoding="utf-8")
        self.assertEqual(self.reload().by_id("P1").decision, review.CULL)

    def test_save_writes_no_sidecar(self):
        sheet = self.reload()
        sheet.by_id("P1").set("notes", "n")
        review.save(self.batch, sheet)
        self.assertFalse(review.sidecar_path(Batch(self.batch.path)).is_file())


class TestSpreadsheetEdits(ReviewTestCase):
    """Bug 1: edits made in the spreadsheet were ignored, then overwritten."""

    def excel_edit(self, place_id, **changes):
        """Open approved.csv, change cells, save — what the operator does."""
        import csv

        rows = self.batch.read_shortlist(path=self.batch.approved_path)
        for row in rows:
            if row["place_id"] == place_id:
                row.update(changes)
        with self.batch.approved_path.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)

    def test_spreadsheet_edit_is_what_the_sheet_loads(self):
        review.save(self.batch, self.reload())
        self.excel_edit("P1", address_to="Jay (clinic manager)", notes="call first")
        back = self.reload()
        self.assertEqual(back.by_id("P1").get("address_to"), "Jay (clinic manager)")
        self.assertEqual(back.by_id("P1").notes, "call first")

    def test_spreadsheet_edit_survives_a_later_save(self):
        review.save(self.batch, self.reload())
        self.excel_edit("P1", address_to="Jay (clinic manager)")
        sheet = self.reload()
        sheet.by_id("P3").decision = review.CULL
        sheet.by_id("P3").reason = "too_small"
        review.save(self.batch, sheet)
        self.assertEqual(self.reload().by_id("P1").get("address_to"),
                         "Jay (clinic manager)")

    def test_clearing_a_note_in_the_spreadsheet_clears_it(self):
        sheet = self.reload()
        sheet.by_id("P1").set("notes", "old note")
        review.save(self.batch, sheet)
        self.excel_edit("P1", notes="")
        self.assertEqual(self.reload().by_id("P1").notes, "")

    def test_clearing_a_note_in_the_sheet_clears_it(self):
        sheet = self.reload()
        sheet.by_id("P1").set("notes", "old note")
        review.save(self.batch, sheet)
        back = self.reload()
        back.by_id("P1").set("notes", "")
        review.save(self.batch, back)
        self.assertEqual(self.reload().by_id("P1").notes, "")

    def test_measurements_are_not_taken_from_the_spreadsheet(self):
        # A re-save can reformat a phone number or date; only the columns
        # that are yours are read back from the file you edited.
        review.save(self.batch, self.reload())
        self.excel_edit("P1", lead_score="999", phone="1462000000")
        self.assertEqual(self.reload().by_id("P1").get("lead_score"), "70")

    def test_edit_on_a_culled_row_is_kept_in_its_rejection(self):
        sheet = self.reload()
        sheet.by_id("P2").decision = review.CULL
        sheet.by_id("P2").reason = "gut"
        sheet.by_id("P2").set("notes", "second site, same owner")
        review.save(self.batch, sheet)
        back = self.reload()
        self.assertEqual(back.by_id("P2").notes, "second site, same owner")
        # Un-cull it and the note comes with it.
        back.by_id("P2").decision = review.KEEP
        back.by_id("P2").reason = ""
        review.save(self.batch, back)
        self.assertEqual(self.reload().by_id("P2").notes, "second site, same owner")

    def test_excel_utf8_with_bom_loads(self):
        review.save(self.batch, self.reload())
        text = self.batch.approved_path.read_text(encoding="utf-8")
        self.batch.approved_path.write_bytes(("\ufeff" + text).encode("utf-8"))
        back = self.reload()
        self.assertEqual(back.counts()[review.KEEP], 3)

    def test_excel_plain_csv_in_windows_1252_loads(self):
        self.people[0]["name"] = "Café Physio – Hitchin"
        self.batch.write_shortlist(business_to_row(b) for b in self.people)
        review.save(self.batch, self.reload())
        text = self.batch.approved_path.read_text(encoding="utf-8")
        self.batch.approved_path.write_bytes(text.encode("cp1252"))
        back = self.reload()
        self.assertEqual(back.by_id("P1").name, "Café Physio – Hitchin")

    def test_disk_change_is_detected(self):
        sheet = self.reload()
        self.assertFalse(review.changed_on_disk(self.batch, sheet))
        review.save(self.batch, sheet)
        # A save updates the sheet's own stamp: not a change from outside.
        self.assertFalse(review.changed_on_disk(self.batch, sheet))
        self.excel_edit("P1", notes="edited elsewhere")
        self.assertTrue(review.changed_on_disk(self.batch, sheet))


class TestStartOver(ReviewTestCase):
    """Bug 3: starting over left the old rejection in force."""

    def test_start_over_clears_an_earlier_rejection(self):
        sheet = self.reload()
        sheet.by_id("P2").decision = review.CULL
        sheet.by_id("P2").reason = "gut"
        review.save(self.batch, sheet)

        again = self.reload()
        review.apply_decisions(again, {}, start_over=True)
        review.save(self.batch, again)

        from pipeline.combine import approved_ids, rejected_ids

        b = Batch(self.batch.path)
        self.assertEqual(approved_ids(b), {"P1", "P2", "P3"})
        self.assertEqual(rejected_ids(b), set())
        self.assertEqual(self.reload().by_id("P2").decision, review.KEEP)

    def test_without_start_over_earlier_culls_stand(self):
        sheet = self.reload()
        sheet.by_id("P2").decision = review.CULL
        sheet.by_id("P2").reason = "gut"
        review.save(self.batch, sheet)
        again = self.reload()
        review.apply_decisions(again, {"P3": "chain"})
        review.save(self.batch, again)
        back = self.reload()
        self.assertEqual(back.by_id("P2").reason, "gut")
        self.assertEqual(back.by_id("P3").reason, "chain")

    def test_files_left_inconsistent_by_the_old_bug_load_as_keep(self):
        # What an old cull.py --full left behind: in approved.csv AND rejected.
        self.batch.write_shortlist((business_to_row(b) for b in self.people),
                                   path=self.batch.approved_path)
        self.batch.append_rejection({"place_id": "P2", "name": "Beta Backs",
                                     "reason": "gut", "rejected_at": utcnow(),
                                     "row": {}, "business": {}})
        sheet = self.reload()
        self.assertEqual(sheet.by_id("P2").decision, review.KEEP)
        review.save(self.batch, sheet)  # and the next save repairs the files
        self.assertEqual(self.batch.read_rejections(), [])


class TestSorting(ReviewTestCase):
    def set_reviews(self, counts):
        sheet = self.reload()
        for row, n in zip(sheet.rows, counts):
            row.row["review_count"] = n
        return sheet

    def test_numbers_sort_as_numbers(self):
        sheet = self.set_reviews(["9", "120", "22"])
        review.sort_rows(sheet.rows, "review_count", descending=False)
        self.assertEqual([r.get("review_count") for r in sheet.rows], ["9", "22", "120"])
        review.sort_rows(sheet.rows, "review_count", descending=True)
        self.assertEqual([r.get("review_count") for r in sheet.rows], ["120", "22", "9"])

    def test_blanks_sort_last_both_ways(self):
        sheet = self.set_reviews(["9", "", "22"])
        for descending in (False, True):
            review.sort_rows(sheet.rows, "review_count", descending=descending)
            self.assertEqual(sheet.rows[-1].get("review_count"), "")

    def test_decision_column_sorts_undecided_first(self):
        sheet = self.reload()
        sheet.by_id("P1").decision = review.CULL
        sheet.by_id("P2").decision = review.KEEP
        review.sort_rows(sheet.rows, "decision", descending=False)
        self.assertEqual([r.decision for r in sheet.rows],
                         [review.UNDECIDED, review.KEEP, review.CULL])

    def test_text_sorts_case_insensitively(self):
        sheet = self.reload()
        sheet.by_id("P1").row["name"] = "beta"
        sheet.by_id("P2").row["name"] = "Alpha"
        sheet.by_id("P3").row["name"] = "gamma"
        review.sort_rows(sheet.rows, "name", descending=False)
        self.assertEqual([r.name for r in sheet.rows], ["Alpha", "beta", "gamma"])


class TestCompatibility(ReviewTestCase):
    """What this writes must stay readable by the tools already there."""

    def test_cull_py_can_read_what_review_saved(self):
        sheet = self.reload()
        sheet.by_id("P2").decision = review.CULL
        sheet.by_id("P2").reason = "site_fine"
        review.save(self.batch, sheet)

        from pipeline.cull import gut_share, reason_counts

        rejections = self.batch.read_rejections()
        self.assertEqual(reason_counts(rejections), {"site_fine": 1})
        self.assertEqual(gut_share(rejections), 0.0)

    def test_combine_sees_the_approved_rows(self):
        sheet = self.reload()
        sheet.by_id("P3").decision = review.CULL
        sheet.by_id("P3").reason = "too_small"
        review.save(self.batch, sheet)

        from pipeline.combine import approved_ids, rejected_ids

        self.assertEqual(approved_ids(Batch(self.batch.path)), {"P1", "P2"})
        self.assertEqual(rejected_ids(Batch(self.batch.path)), {"P3"})


if __name__ == "__main__":
    unittest.main()
