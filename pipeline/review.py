"""The shortlist as an editable sheet that remembers what you did.

The cull already had three front ends — the console prompt, the contact
sheet, and a marked-up CSV — but nothing that let you sit with the list,
change your mind, fix who the letter is addressed to, jot down why, and come
back to it tomorrow. This is that: load a batch into rows carrying their
current decision, edit, save, reopen and find it as you left it.

**Where the truth lives.** Everything is read back from the files the rest
of the pipeline already writes: ``approved.csv`` holds the keeps, and each
cull is a record in ``rejections.jsonl``. Both carry the full row, so your
edits — a corrected ``address_to``, a note — live in the same row as the
decision. There is no second copy to disagree with, which means an edit made
by opening ``approved.csv`` in a spreadsheet is simply what the next load
reads.

Row *measurements* (score, verdict, review count…) always come from
``shortlist.csv``, never from ``approved.csv``: a spreadsheet re-save can turn
"01462" into 1462 or a date into 20/08/2026, and only the columns that are
yours to edit are taken from the file you edited.

This module is the single writer. ``cull.py`` applies its decisions through
``apply_decisions`` and ``save`` rather than writing the files itself, so a
cull made at the console, from a contact sheet or on the Shortlist tab
follows one set of rules.

``review.json`` was an earlier side-car for notes. It is still read, as a
fallback for any row whose file predates the notes column, and removed on
the next save — by then everything in it is in the rows themselves.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .cull import REASON_CODES
from .storage import Batch, utcnow

# Columns the sheet lets you change. Everything else is measured, and editing
# a measurement by hand would make the score a lie.
EDITABLE = ("address_to", "notes")

KEEP = "keep"
CULL = "cull"
UNDECIDED = ""


@dataclass
class ReviewRow:
    """One shortlist row plus what you decided about it."""

    row: dict
    decision: str = UNDECIDED
    reason: str = ""
    notes: str = ""
    rejected_at: Optional[str] = None

    @property
    def place_id(self) -> str:
        return self.row.get("place_id") or ""

    @property
    def name(self) -> str:
        return self.row.get("name") or ""

    def get(self, column: str) -> str:
        if column == "notes":
            return self.notes
        value = self.row.get(column)
        return "" if value is None else str(value)

    def set(self, column: str, value: str) -> None:
        if column not in EDITABLE:
            raise ValueError(f"{column} is measured, not editable")
        if column == "notes":
            self.notes = value
        else:
            self.row[column] = value

    def score(self) -> int:
        try:
            return int(float(self.row.get("lead_score") or 0))
        except (TypeError, ValueError):
            return 0


@dataclass
class ReviewSheet:
    rows: list[ReviewRow] = field(default_factory=list)
    # True when the batch has been culled before, so the caller can say so
    # rather than silently presenting a half-finished review as a fresh one.
    culled_before: bool = False
    # What the decision files looked like when this sheet was read, so a
    # save can tell that a spreadsheet (or cull.py) has written them since.
    stamp: tuple = ()

    def __len__(self) -> int:
        return len(self.rows)

    def counts(self) -> dict[str, int]:
        out = {KEEP: 0, CULL: 0, UNDECIDED: 0}
        for row in self.rows:
            out[row.decision if row.decision in (KEEP, CULL) else UNDECIDED] += 1
        return out

    def by_id(self, place_id: str) -> Optional[ReviewRow]:
        return next((r for r in self.rows if r.place_id == place_id), None)


def sidecar_path(batch: Batch) -> Path:
    return batch.path / "review.json"


def read_sidecar(batch: Batch) -> dict[str, dict]:
    """Notes and hand-edits, keyed by Place ID. Corrupt or absent means none.

    A side-car that fails to parse must not take the batch down with it: the
    decisions are in the CSVs and are what matter, so a bad file costs notes
    and nothing else.
    """
    path = sidecar_path(batch)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    entries = data.get("rows") if isinstance(data, dict) else None
    if not isinstance(entries, dict):
        return {}
    return {k: v for k, v in entries.items() if isinstance(v, dict)}


def disk_stamp(batch: Batch) -> tuple:
    """Modification time and size of each file a sheet is read from.

    Size as well as mtime, because a save and a re-save inside the same
    filesystem tick would otherwise look unchanged.
    """
    out = []
    for path in (batch.shortlist_path, batch.approved_path, batch.rejections_path):
        try:
            st = path.stat()
            out.append((path.name, st.st_mtime_ns, st.st_size))
        except FileNotFoundError:
            out.append((path.name, None, None))
    return tuple(out)


def changed_on_disk(batch: Batch, sheet: ReviewSheet) -> bool:
    """Has anything written the decision files since this sheet was read?"""
    return bool(sheet.stamp) and disk_stamp(batch) != sheet.stamp


def _editable_from(source: Optional[dict], column: str) -> Optional[str]:
    """The edited value if the source row has that column at all.

    Present-but-blank counts: clearing a note in the spreadsheet must clear
    it here. Absent means the file predates the column, so look elsewhere.
    """
    if source is None or column not in source:
        return None
    value = source.get(column)
    return "" if value is None else str(value)


def load(batch: Batch) -> ReviewSheet:
    """Build the sheet: shortlist rows, current decisions, your edits.

    Rows come from ``shortlist.csv`` — the full list, always, so a cull can be
    reversed. A row named in ``rejections.jsonl`` is a cull carrying its
    reason; a row in ``approved.csv`` is a keep. A batch never culled has
    every row undecided. ``address_to`` and ``notes`` come from whichever of
    those two records the row, so whatever last wrote them wins.
    """
    stamp = disk_stamp(batch)
    rows = batch.read_shortlist()
    culled_before = batch.approved_path.is_file()
    approved_rows: dict[str, dict] = {}
    if culled_before:
        for r in batch.read_shortlist(path=batch.approved_path):
            if r.get("place_id"):
                approved_rows[r["place_id"]] = r
    rejections: dict[str, dict] = {}
    for rej in batch.read_rejections():
        place_id = rej.get("place_id")
        if place_id:
            # Later entries win: older cull.py runs appended, so the last
            # word about a business is the current one.
            rejections[place_id] = rej

    # A row can be in approved.csv but missing from shortlist.csv when an
    # older batch was culled and the shortlist later regenerated. Keeping it
    # means an approved business never silently vanishes from the review.
    known = {r.get("place_id") for r in rows}
    for place_id, extra in approved_rows.items():
        if place_id not in known:
            rows.append(dict(extra))

    legacy = read_sidecar(batch)
    sheet = ReviewSheet(culled_before=culled_before, stamp=stamp)
    for row in rows:
        place_id = row.get("place_id") or ""
        rejection = rejections.get(place_id)
        if rejection is not None and place_id not in approved_rows:
            decision, reason = CULL, rejection.get("reason") or ""
            rejected_at = rejection.get("rejected_at")
            source = rejection.get("row")
        elif rejection is not None:
            # In both files. Only an old cull.py --full produced this: it
            # rewrote approved.csv for a fresh pass but left the earlier
            # rejection behind. approved.csv is the newer statement.
            decision, reason, rejected_at = KEEP, "", None
            source = approved_rows[place_id]
        elif place_id in approved_rows:
            decision, reason, rejected_at = KEEP, "", None
            source = approved_rows[place_id]
        else:
            decision, reason, rejected_at, source = UNDECIDED, "", None, None

        saved = legacy.get(place_id, {})
        address_to = _editable_from(source, "address_to")
        if address_to is None:
            address_to = saved.get("address_to")
        if address_to is not None:
            row["address_to"] = address_to
        notes = _editable_from(source, "notes")
        if notes is None:
            notes = saved.get("notes") or row.get("notes") or ""

        sheet.rows.append(ReviewRow(
            row=row, decision=decision, reason=reason, notes=notes,
            rejected_at=rejected_at,
        ))
    return sheet


def apply_decisions(
    sheet: ReviewSheet, decisions: dict[str, str], *, start_over: bool = False
) -> list[ReviewRow]:
    """Apply a place_id -> reason map, the way cull.py produces them.

    Named rows become culls with that reason. ``start_over`` first resets
    every row to undecided, so a business culled on an earlier pass and not
    culled on this one is kept — and its old rejection disappears on save,
    rather than lingering where combine and tune would still act on it.
    Returns the rows culled by this call, for the caller to report.
    """
    if start_over:
        for row in sheet.rows:
            row.decision, row.reason, row.rejected_at = UNDECIDED, "", None
    culled = []
    for row in sheet.rows:
        reason = decisions.get(row.place_id)
        if reason:
            row.decision, row.reason = CULL, reason
            culled.append(row)
    return culled


def validate(sheet: ReviewSheet) -> list[str]:
    """Problems that must be fixed before saving, in plain English.

    A cull without a reason code is the one that matters: free text and
    blanks are what make a rejection useless to tune.py later.
    """
    problems = []
    for row in sheet.rows:
        if row.decision != CULL:
            continue
        if not row.reason:
            problems.append(f"{row.name or row.place_id}: culled with no reason code")
        elif row.reason not in REASON_CODES:
            problems.append(
                f"{row.name or row.place_id}: '{row.reason}' is not a reason code")
    return problems


def save(batch: Batch, sheet: ReviewSheet) -> dict:
    """Write the decisions back out. Returns counts for the caller to report.

    ``approved.csv`` and ``rejections.jsonl`` are rewritten wholesale rather
    than appended to, because this sheet is editable: un-culling a business
    has to remove its rejection, and an append-only log cannot express that.
    The original ``rejected_at`` is carried over for rows still culled, so
    rewriting does not restate history it did not change.

    Undecided rows count as keeps. The sheet is a working list, and a row you
    have not got to yet is not one you have rejected.
    """
    problems = validate(sheet)
    if problems:
        raise ValueError("; ".join(problems))

    approved = [r for r in sheet.rows if r.decision != CULL]
    culled = [r for r in sheet.rows if r.decision == CULL]

    for row in sheet.rows:
        # Unconditionally: a cleared note has to overwrite the old one.
        row.row["notes"] = row.notes

    batch.write_shortlist((r.row for r in approved), path=batch.approved_path)

    lines = []
    for row in culled:
        business = batch.read_business(row.place_id) or {}
        lines.append(json.dumps({
            "place_id": row.place_id,
            "name": row.name,
            "reason": row.reason,
            "rejected_at": row.rejected_at or utcnow(),
            "row": {k: v for k, v in row.row.items() if k != "reason"},
            "business": business,
        }))
    text = "\n".join(lines) + ("\n" if lines else "")
    tmp = batch.rejections_path.with_suffix(".jsonl.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(batch.rejections_path)

    # Everything the side-car held is in the rows now; one copy, not two.
    sidecar_path(batch).unlink(missing_ok=True)
    sheet.stamp = disk_stamp(batch)

    counts: dict[str, int] = {}
    for row in culled:
        counts[row.reason] = counts.get(row.reason, 0) + 1
    meta = batch.read_meta()
    meta["rejection_reasons"] = counts
    meta["status_counts"] = {
        **meta.get("status_counts", {}),
        "approved": len(approved),
        "rejected": len(culled),
    }
    meta["culled"] = utcnow()
    batch.write_meta(meta)
    return {"approved": len(approved), "rejected": len(culled), "reasons": counts}


# Columns that hold numbers. Sorting them as text puts 120 before 22 before 9.
NUMERIC_COLUMNS = {"lead_score", "staleness_points", "review_count", "rating",
                   "mobile_score", "photo_count"}
# Undecided first when ascending: the rows still needing you come to the top.
DECISION_ORDER = {UNDECIDED: 0, KEEP: 1, CULL: 2}


def sort_rows(rows: list[ReviewRow], column: str, *, descending: bool) -> None:
    """Sort in place by a sheet column. Blanks always go last."""
    def number(row):
        raw = row.get(column)
        try:
            return (0, float(raw))
        except (TypeError, ValueError):
            return (1, 0.0)

    if column == "decision":
        rows.sort(key=lambda r: DECISION_ORDER.get(r.decision, 0),
                  reverse=descending)
        return
    if column in NUMERIC_COLUMNS:
        # Sort the present values, then append the blanks, so descending
        # doesn't float the blanks to the top.
        present = [r for r in rows if number(r)[0] == 0]
        blank = [r for r in rows if number(r)[0] == 1]
        present.sort(key=lambda r: number(r)[1], reverse=descending)
        rows[:] = present + blank
        return

    def text(row):
        return (row.reason if column == "reason" else row.get(column)).lower()

    present = [r for r in rows if text(r)]
    blank = [r for r in rows if not text(r)]
    present.sort(key=text, reverse=descending)
    rows[:] = present + blank
