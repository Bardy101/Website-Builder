"""Tests for the cull loop and the weight-tuning frequency table."""

import tempfile
import unittest
from pathlib import Path

from pipeline.cull import REASON_CODES, gut_share, reason_counts, split_shortlist, validate_reason
from pipeline.storage import Batch
from pipeline.tune import compare, format_report, propose, signal_summary


class TestCull(unittest.TestCase):
    def test_reason_codes_are_the_spec_list(self):
        self.assertEqual(
            set(REASON_CODES),
            {"chain", "winding_down", "site_fine", "too_small", "wrong_niche",
             "duplicate", "no_owner_signal", "gut"},
        )

    def test_validate_rejects_free_text(self):
        with self.assertRaises(ValueError):
            validate_reason("looked a bit shabby")

    def test_split_keeps_unmarked_rows(self):
        rows = [{"place_id": "a"}, {"place_id": "b"}, {"place_id": "c"}]
        approved, rejected = split_shortlist(rows, {"b": "chain"})
        self.assertEqual([r["place_id"] for r in approved], ["a", "c"])
        self.assertEqual(rejected[0]["reason"], "chain")

    def test_reason_counts_and_gut_share(self):
        rejections = [{"reason": "gut"}, {"reason": "gut"}, {"reason": "chain"}]
        self.assertEqual(reason_counts(rejections), {"gut": 2, "chain": 1})
        self.assertAlmostEqual(gut_share(rejections), 2 / 3)

    def test_gut_share_empty(self):
        self.assertEqual(gut_share([]), 0.0)


class TestBatchRejections(unittest.TestCase):
    def test_rejections_roundtrip_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch = Batch.create(tmp, niche="physio", area="hitchin")
            batch.append_rejection({"place_id": "a", "reason": "chain"})
            batch.append_rejection({"place_id": "b", "reason": "gut"})
            out = batch.read_rejections()
            self.assertEqual(len(out), 2)
            self.assertEqual(out[1]["reason"], "gut")


class TestTune(unittest.TestCase):
    def test_signal_summary(self):
        records = [
            {"lead_score": "40", "review_count": "10"},
            {"lead_score": "20", "review_count": "30"},
        ]
        summary = signal_summary(records)
        self.assertEqual(summary["lead_score"]["mean"], 30.0)
        self.assertEqual(summary["review_count"]["median"], 20.0)

    def test_too_small_cluster_proposes_higher_bar(self):
        approved = [{"lead_score": "50", "review_count": "40"}]
        rejected = [
            {"reason": "too_small", "review_count": "9", "lead_score": "20"},
            {"reason": "too_small", "review_count": "11", "lead_score": "20"},
            {"reason": "too_small", "review_count": "12", "lead_score": "20"},
        ]
        comparison = compare(approved, rejected)
        weights = {"thresholds": {"too_few_reviews_max": 5}}
        proposals = propose(comparison, weights)
        fields = [p["field"] for p in proposals]
        self.assertIn("thresholds.too_few_reviews_max", fields)
        prop = next(p for p in proposals if p["field"] == "thresholds.too_few_reviews_max")
        self.assertEqual(prop["current"], 5)
        self.assertEqual(prop["suggested"], 11)

    def test_mobile_score_setting_is_never_proposed(self):
        # The old tuner proposed thresholds.mobile_score_dated_max, which
        # nothing reads. Whatever the cull history, it must not come back.
        approved = [{"lead_score": "50", "mobile_score": "30"}]
        rejected = [{"reason": "site_fine", "mobile_score": str(m), "lead_score": "15"}
                    for m in (60, 62, 65)]
        proposals = propose(compare(approved, rejected), {"thresholds": {}})
        self.assertFalse(any("mobile" in p["field"] for p in proposals))

    def test_too_few_cases_proposes_nothing(self):
        comparison = compare(
            [{"lead_score": "50", "review_count": "40"}],
            [{"reason": "too_small", "review_count": "9", "lead_score": "20"}],
        )
        proposals = propose(comparison, {"thresholds": {"too_few_reviews_max": 5}})
        self.assertEqual(
            [p for p in proposals if p["field"] == "thresholds.too_few_reviews_max"], []
        )

    def test_rejections_outscoring_approvals_flags_the_signals(self):
        approved = [{"lead_score": "10"}]
        rejected = [{"reason": "gut", "lead_score": "80"}]
        proposals = propose(compare(approved, rejected), {"thresholds": {}})
        self.assertTrue(any("Narrow the niche" in p["why"] for p in proposals))

    def test_report_renders(self):
        comparison = compare(
            [{"lead_score": "50"}], [{"reason": "chain", "lead_score": "20"}]
        )
        text = format_report(comparison, [])
        self.assertIn("Rejections by reason code", text)
        self.assertIn("chain", text)
        self.assertIn("None. Not enough evidence yet", text)



class TestRecull(unittest.TestCase):
    """Re-reviewing a culled batch must continue from the survivors.

    The bug: option 2 always read shortlist.csv, so a second review showed
    the original uncalled list — and completing it would have resurrected
    every previously rejected row into approved.csv.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.batch = Batch.create(self.tmp.name, niche="physio", area="hitchin")
        self.batch.write_shortlist(
            [{"place_id": f"id{i}", "name": f"Biz {i}"} for i in range(4)]
        )

    def tearDown(self):
        self.tmp.cleanup()

    def _cull(self, reasons, *, source="shortlist.csv", full=False):
        import csv

        import cull

        rows = self.batch.read_shortlist(
            path=None if source == "shortlist.csv" else self.batch.approved_path
        )
        marked = self.batch.path / "marked.csv"
        cols = [c for c in rows[0].keys() if c != "reason"] + ["reason"]
        with marked.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=cols)
            writer.writeheader()
            for row in rows:
                row = dict(row)
                row["reason"] = reasons.get(row["place_id"], "")
                writer.writerow(row)
        argv = ["--batch", str(self.batch.path), "--from-csv", "marked.csv"]
        if full:
            argv.append("--full")
        cull.main(argv)

    def test_second_cull_reviews_survivors_not_the_original(self):
        self._cull({"id0": "chain"})
        self.assertEqual(len(self.batch.read_shortlist(path=self.batch.approved_path)), 3)

        self._cull({"id1": "gut"}, source="approved")
        survivors = self.batch.read_shortlist(path=self.batch.approved_path)
        ids = {r["place_id"] for r in survivors}
        # id1 culled this pass — and id0 must NOT have been resurrected.
        self.assertEqual(ids, {"id2", "id3"})

    def test_rejections_accumulate_across_passes(self):
        self._cull({"id0": "chain"})
        self._cull({"id1": "gut"}, source="approved")
        reasons = [r["reason"] for r in self.batch.read_rejections()]
        self.assertEqual(sorted(reasons), ["chain", "gut"])

    def test_full_flag_starts_over_from_the_original(self):
        self._cull({"id0": "chain"})
        self._cull({}, full=True)  # keep everything on the fresh pass
        survivors = self.batch.read_shortlist(path=self.batch.approved_path)
        self.assertEqual(len(survivors), 4)

    def test_empty_approved_points_at_full(self):
        import cull

        self.batch.write_shortlist([], path=self.batch.approved_path)
        with self.assertRaises(SystemExit) as ctx:
            cull.main(["--batch", str(self.batch.path)])
        self.assertIn("--full", str(ctx.exception))

    def test_uncalled_batch_still_reviews_the_shortlist(self):
        self._cull({"id3": "too_small"})
        survivors = self.batch.read_shortlist(path=self.batch.approved_path)
        self.assertEqual(len(survivors), 3)



def site_row(reason=None, *, viewport="yes", media="yes", year=2026, table="no",
             https="yes", platform="", verdict="poor", last_review="", score="40"):
    """A shortlist row as the CSVs carry it."""
    row = {"lead_score": score, "site_verdict": verdict, "has_viewport": viewport,
           "has_media_queries": media, "copyright_year": str(year),
           "uses_table_layout": table, "https": https, "has_flash": "no",
           "platform_hint": platform, "recent_review_date": last_review}
    if reason:
        row["reason"] = reason
    return row


WEIGHTS = {
    "signals": {"no_viewport": 25, "no_media_queries": 15, "copyright_stale": 15,
                "table_layout": 15, "no_https": 15, "flash": 10},
    "thresholds": {"copyright_stale_years": 3, "min_site_points": 25,
                   "dormant_after_days": 365, "too_few_reviews_max": 5},
}


class TestTuneLearnsFromSiteFine(unittest.TestCase):
    """Culling good-looking sites as 'site_fine' is what teaches the finder
    to stop bringing them."""

    def run_tune(self, approved, rejected):
        return propose(compare(approved, rejected, weights=WEIGHTS), WEIGHTS)

    def test_flags_read_from_csv_columns(self):
        from pipeline.tune import record_flags

        flags = record_flags(site_row(viewport="no", year=2019, https="no"))
        self.assertEqual(sorted(flags), ["copyright_stale", "no_https", "no_viewport"])
        self.assertIsNone(record_flags({"lead_score": "40"}))   # never measured

    def test_one_signal_behind_most_fine_culls_is_weakened(self):
        # Good sites that only had an old footer year plus HTTPS missing.
        fine = [site_row("site_fine", year=2019, https="no") for _ in range(4)]
        kept = [site_row(viewport="no", media="no") for _ in range(6)]
        proposals = self.run_tune(kept, fine)
        fields = {p["field"]: p for p in proposals}
        self.assertIn("signals.copyright_stale", fields)
        prop = fields["signals.copyright_stale"]
        # 15 + 15 = 30 passes the 25 bar; only a cut to 5 (15 + 5 = 20) doesn't.
        self.assertEqual(prop["suggested"], 5)
        self.assertIn("4 of those 4 would fall below the 25-point bar", prop["why"])
        self.assertIn("0 of your keeps would", prop["why"])
        # One fix, not two: the bar is not raised as well.
        self.assertNotIn("thresholds.min_site_points", fields)

    def test_signal_common_in_keeps_too_is_left_alone(self):
        fine = [site_row("site_fine", year=2019, https="no") for _ in range(4)]
        kept = [site_row(year=2019, viewport="no") for _ in range(6)]
        fields = {p["field"] for p in self.run_tune(kept, fine)}
        self.assertNotIn("signals.copyright_stale", fields)

    def test_mixed_signals_raise_the_bar(self):
        # No single culprit: each fine site had two different weak signs.
        fine = [site_row("site_fine", year=2019, https="no"),
                site_row("site_fine", media="no", table="yes"),
                site_row("site_fine", year=2019, table="yes"),
                site_row("site_fine", https="no", media="no")]
        kept = [site_row(viewport="no", media="no", year=2015) for _ in range(6)]
        fields = {p["field"]: p for p in self.run_tune(kept, fine)}
        bar = fields["thresholds.min_site_points"]
        self.assertEqual((bar["current"], bar["suggested"]), (25, 35))
        self.assertIn("0 of the 6 sites you kept", bar["why"])

    def test_bar_that_would_cost_too_many_keeps_is_not_proposed(self):
        fine = [site_row("site_fine", year=2019, https="no") for _ in range(2)] + \
               [site_row("site_fine", media="no", table="yes") for _ in range(2)]
        kept = [site_row(year=2019, media="no") for _ in range(6)]   # also 30 points
        fields = {p["field"] for p in self.run_tune(kept, fine)}
        self.assertNotIn("thresholds.min_site_points", fields)

    def test_platform_shared_by_fine_culls_is_proposed_as_modern(self):
        fine = [site_row("site_fine", year=2019, table="yes", platform="wix")
                for _ in range(3)] + \
               [site_row("site_fine", media="no", https="no", platform="wix")]
        kept = [site_row(viewport="no", platform="wordpress") for _ in range(5)]
        proposals = self.run_tune(kept, fine)
        platform = [p for p in proposals if p["field"] == "modern_platforms"]
        self.assertTrue(platform)
        self.assertIn("wix", platform[0]["suggested"])

    def test_too_few_fine_culls_proposes_nothing(self):
        fine = [site_row("site_fine", year=2019) for _ in range(2)]
        self.assertEqual(self.run_tune([site_row(viewport="no")], fine), [])

    def test_every_proposal_actually_removes_the_culls(self):
        """The rule that caught the first draft out: a proposal is simulated,
        not just plausible. Apply it and re-score — the culls must drop."""
        from pipeline.tune import apply, features, _points

        fine = [site_row("site_fine", year=2019, https="no") for _ in range(4)]
        kept = [site_row(viewport="no", media="no", year=2014) for _ in range(6)]
        weights = {"signals": dict(WEIGHTS["signals"]),
                   "thresholds": dict(WEIGHTS["thresholds"])}
        proposals = propose(compare(kept, fine, weights=weights), weights)
        apply(weights, proposals)
        bar = weights["thresholds"]["min_site_points"]
        for row in fine:
            self.assertLess(_points(features(row), weights["signals"]), bar)
        for row in kept:
            self.assertGreaterEqual(_points(features(row), weights["signals"]), bar)

    def test_no_change_that_works_proposes_nothing_misleading(self):
        # The culls look exactly like the keeps: nothing can separate them.
        fine = [site_row("site_fine", viewport="no", media="no") for _ in range(4)]
        kept = [site_row(viewport="no", media="no") for _ in range(6)]
        fields = {p["field"] for p in self.run_tune(kept, fine)}
        self.assertFalse({"signals.no_viewport", "signals.no_media_queries",
                          "thresholds.min_site_points"} & fields)


class TestTuneLearnsFromWindingDown(unittest.TestCase):
    def test_dormancy_window_shortened(self):
        from datetime import date, timedelta

        today = date.today()
        old = lambda days: (today - timedelta(days=days)).isoformat()
        rejected = [site_row("winding_down", last_review=old(d)) for d in (200, 240, 260)]
        kept = [site_row(last_review=old(d)) for d in (10, 30, 45, 60)]
        proposals = propose(compare(kept, rejected, weights=WEIGHTS), WEIGHTS)
        prop = next(p for p in proposals if p["field"] == "thresholds.dormant_after_days")
        self.assertEqual((prop["current"], prop["suggested"]), (365, 240))


class TestApply(unittest.TestCase):
    def test_apply_writes_paths_and_bumps_the_version(self):
        from pipeline.tune import apply

        weights = {"_version": 3, "signals": {"copyright_stale": 15},
                   "thresholds": {"min_site_points": 25}}
        changed = apply(weights, [
            {"field": "signals.copyright_stale", "path": ["signals", "copyright_stale"],
             "current": 15, "suggested": 10, "why": ""},
            {"field": "(no single weight)", "current": "a", "suggested": "b", "why": ""},
        ])
        self.assertEqual(weights["signals"]["copyright_stale"], 10)
        self.assertEqual(weights["_version"], 4)
        self.assertEqual(changed, ["signals.copyright_stale: 15 -> 10"])

    def test_nothing_applicable_changes_nothing(self):
        from pipeline.tune import apply

        weights = {"_version": 3}
        self.assertEqual(apply(weights, [{"field": "x", "current": 1, "suggested": 2,
                                          "why": ""}]), [])
        self.assertEqual(weights, {"_version": 3})

    def test_applied_platform_list_is_what_scoring_uses(self):
        from pipeline.scoring import Weights, modern_platforms

        w = Weights.default()
        w.raw = {"modern_platforms": ["squarespace", "wix"]}
        self.assertIn("wix", modern_platforms(w))
        self.assertIn("squarespace", modern_platforms(Weights.default()))


if __name__ == "__main__":
    unittest.main()


class TestFullClearsRejections(unittest.TestCase):
    """Bug 3 through cull.py itself: --full must actually start over."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.batch = Batch.create(self.tmp.name, niche="physio", area="hitchin")
        self.batch.write_shortlist(
            [{"place_id": f"id{i}", "name": f"Biz {i}"} for i in range(3)])

    def tearDown(self):
        self.tmp.cleanup()

    def import_decisions(self, decisions, *, full=False):
        import contextlib
        import io
        import json

        import cull

        path = self.batch.path / "d.json"
        path.write_text(json.dumps(decisions), encoding="utf-8")
        argv = ["--batch", str(self.batch.path), "--import", str(path)]
        if full:
            argv.append("--full")
        with contextlib.redirect_stdout(io.StringIO()):
            cull.main(argv)

    def test_kept_on_a_fresh_pass_is_no_longer_rejected(self):
        from pipeline.combine import approved_ids, rejected_ids

        self.import_decisions([{"place_id": "id1", "decision": "cull", "reason": "gut"}])
        self.assertEqual(rejected_ids(Batch(self.batch.path)), {"id1"})

        self.import_decisions([{"place_id": f"id{i}", "decision": "keep"}
                               for i in range(3)], full=True)
        b = Batch(self.batch.path)
        self.assertEqual(approved_ids(b), {"id0", "id1", "id2"})
        self.assertEqual(rejected_ids(b), set())


class TestTuneCountsEachBusinessOnce(unittest.TestCase):
    """A combined sheet copies its sources' businesses. Tuning across all
    folders used to count one business in each, or as both kept and culled."""

    def setUp(self):
        import os

        self.tmp = tempfile.TemporaryDirectory()
        rows = [{"place_id": f"P{i}", "name": f"Biz {i}"} for i in range(4)]
        self.town = Batch.create(self.tmp.name, niche="physio", area="hitchin")
        self.town.write_shortlist(rows)
        self.town.write_shortlist(rows[:3], path=self.town.approved_path)
        self.town.append_rejection({"place_id": "P3", "reason": "chain", "row": rows[3]})
        old = self.town.approved_path.stat().st_mtime - 3600
        for path in (self.town.approved_path, self.town.rejections_path):
            os.utime(path, (old, old))
        # Later: the three keeps combined, and one of them culled on a second look.
        self.combined = Batch.create(self.tmp.name, niche="combined", area="combined",
                                     name="combined_x")
        self.combined.write_shortlist(rows[:3])
        self.combined.write_shortlist(rows[:2], path=self.combined.approved_path)
        self.combined.append_rejection({"place_id": "P2", "reason": "site_fine",
                                        "row": rows[2]})

    def tearDown(self):
        self.tmp.cleanup()

    def test_latest_decision_wins_and_nothing_counts_twice(self):
        import tune

        approved, rejected, overlap = tune.gather([self.town.path, self.combined.path])
        self.assertEqual(sorted(r["place_id"] for r in approved), ["P0", "P1"])
        self.assertEqual(sorted((r["place_id"], r["reason"]) for r in rejected),
                         [("P2", "site_fine"), ("P3", "chain")])
        self.assertEqual(overlap, 3)

    def test_folder_order_does_not_matter(self):
        import tune

        a = tune.gather([self.town.path, self.combined.path])
        b = tune.gather([self.combined.path, self.town.path])
        key = lambda rows: sorted(r["place_id"] for r in rows)  # noqa: E731
        self.assertEqual((key(a[0]), key(a[1])), (key(b[0]), key(b[1])))
