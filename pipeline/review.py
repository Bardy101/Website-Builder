"""The shortlist as an editable sheet that remembers what you did.

The cull already had three front ends — the console prompt, the contact
sheet, and a marked-up CSV — but nothing that let you sit with the list,
change your mind, fix who the letter is addressed to, jot down why, and come
back to it tomorrow. This is that: load a batch into rows carrying their
current decision, edit, save, reopen and find it as you left it.

**Where the truth lives.** Decisions are read back from the files the rest of
the pipeline already writes — ``approved.csv`` for the keeps, a rejection in
``rejections.jsonl`` for the culls. Nothing here invents a private store for
them, so a cull done in the contact sheet or by ``cull.py`` shows up here,
and a cull done here feeds ``tune.py`` exactly as before.

The one thing those two files cannot hold is a note you typed, or an
``address_to`` you corrected on a row you then kept — a keep is a row in
``approved.csv``, and a regenerated shortlist would overwrite it. Those edits
go in a side-car, ``review.json``, keyed by Place ID, and are re-applied on
load. It is deliberately not the source of truth for decisions: if it is
deleted the decisions survive, and only the notes are lost.
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


def write_sidecar(batch: Batch, sheet: ReviewSheet) -> Optional[Path]:
    """Persist notes and hand-edits. Writes nothing when there are none."""
    entries: dict[str, dict] = {}
    for row in sheet.rows:
        entry = {}
        if row.notes:
            entry["notes"] = row.notes
        if row.row.get("address_to"):
            entry["address_to"] = row.row["address_to"]
        if entry:
            entries[row.place_id] = entry
    path = sidecar_path(batch)
    if not entries:
        path.unlink(missing_ok=True)
        return None
    payload = {"saved": utcnow(), "rows": entries}
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def load(batch: Batch) -> ReviewSheet:
    """Build the sheet: shortlist rows, current decisions, saved edits.

    Rows come from ``shortlist.csv`` — the full list, always, so a cull can be
    reversed. A row present in ``approved.csv`` is a keep; a row named in
    ``rejections.jsonl`` is a cull carrying its reason. A batch never culled
    has every row undecided.
    """
    rows = batch.read_shortlist()
    culled_before = batch.approved_path.is_file()
    approved_ids = set()
    if culled_before:
        approved_ids = {
            r.get("place_id") for r in batch.read_shortlist(path=batch.approved_path)
        }
    rejections = {}
    for rej in batch.read_rejections():
        place_id = rej.get("place_id")
        if place_id:
            # Later entries win: rejections.jsonl is appended to, so the last
            # word about a business is the current one.
            rejections[place_id] = rej

    # A row can be missing from shortlist.csv but present in approved.csv when
    # an older batch was culled and the shortlist later regenerated. Keeping
    # it means an approved business never silently vanishes from the review.
    known = {r.get("place_id") for r in rows}
    if culled_before:
        for extra in batch.read_shortlist(path=batch.approved_path):
            if extra.get("place_id") not in known:
                rows.append(extra)

    sidecar = read_sidecar(batch)
    sheet = ReviewSheet(culled_before=culled_before)
    for row in rows:
        place_id = row.get("place_id") or ""
        saved = sidecar.get(place_id, {})
        if saved.get("address_to"):
            row["address_to"] = saved["address_to"]
        rejection = rejections.get(place_id)
        if rejection is not None:
            decision, reason = CULL, rejection.get("reason") or ""
            rejected_at = rejection.get("rejected_at")
        elif culled_before and place_id in approved_ids:
            decision, reason, rejected_at = KEEP, "", None
        else:
            decision, reason, rejected_at = UNDECIDED, "", None
        sheet.rows.append(ReviewRow(
            row=row, decision=decision, reason=reason,
            notes=saved.get("notes") or row.get("notes") or "",
            rejected_at=rejected_at,
        ))
    return sheet


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
        if row.notes:
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

    write_sidecar(batch, sheet)

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
