#!/usr/bin/env python3
"""cull.py — record your cull decisions and write approved.csv.

Two ways to use it.

Interactive (the normal way, after you've eyeballed shortlist.csv):

    ./cull.py --batch batches/2026-09-01_physios_hitchin

    It walks the shortlist top-down showing the columns that matter and the
    two clickable links. Press Enter to keep; type a reason code to reject.

From a marked-up spreadsheet (if you'd rather work in Excel/Numbers):

    Add a 'reason' column to shortlist.csv, fill it in for rejections only,
    save, then:

    ./cull.py --batch <folder> --from-csv shortlist.csv

Either way it writes approved.csv and appends every rejection to
rejections.jsonl with the full business record attached, so tune.py has
something to learn from.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Running this from another folder, or copying it out of the project on its
# own, both break the imports below: the script needs the pipeline/ package
# beside it. Adding the script's own directory to the path fixes the first
# case; the guard below explains the second in English rather than a traceback.
sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from pipeline.cull import (
        REASON_CODES,
        decisions_from_export,
        gut_share,
        reason_counts,
    )
    from pipeline import review
    from pipeline.storage import Batch
except ImportError as exc:
    if "pipeline" not in str(exc):
        raise
    sys.exit(
        "\ncull.py could not find the 'pipeline' package.\n\n"
        "This is one file of a project, not a standalone script. It needs the\n"
        "pipeline/ folder, weights.json and examples/ sitting beside it.\n\n"
        "Get the whole project and run it from inside that folder:\n\n"
        "  git clone -b claude/tool-build-markdown-spec-vnr7ss \\\n"
        "      https://github.com/Bardy101/Website-Builder.git\n"
        "  cd Website-Builder\n"
        "  python -m pip install -r requirements.txt\n"
        "  python cull.py --help\n"
    )


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="cull.py", description=__doc__.split("\n")[0])
    p.add_argument("--batch", required=True, help="path to the batch folder")
    p.add_argument(
        "--import", dest="import_path", metavar="decisions.json",
        help="apply decisions exported from a contact sheet",
    )
    p.add_argument(
        "--from-csv",
        help="read decisions from a 'reason' column in this CSV instead of prompting",
    )
    p.add_argument("--keep", type=int, default=15, help="target number to keep (guide only)")
    p.add_argument(
        "--full",
        action="store_true",
        help="review the original shortlist even if this batch was already "
             "culled (starts the cull over)",
    )
    return p.parse_args(argv)


def print_reason_help() -> None:
    print("\nReason codes:")
    for code, meaning in REASON_CODES.items():
        print(f"  {code:<16} {meaning}")
    print()


def prompt_decisions(rows: list[dict], keep_target: int) -> dict[str, str]:
    """Walk the shortlist and collect a decision per row."""
    print_reason_help()
    print(f"{len(rows)} rows. Enter = keep, reason code = reject, 'q' = stop here.\n")
    decisions: dict[str, str] = {}
    kept = 0
    for i, row in enumerate(rows, 1):
        print(f"[{i}/{len(rows)}] {row['name']}  ({row['town']})")
        print(
            f"    score {row['lead_score']}  |  site: {row['site_verdict'] or 'n/a'}"
            f"  mobile: {row['mobile_score'] or '-'}  https: {row['https'] or '-'}"
        )
        print(
            f"    {row['rating'] or '-'}* from {row['review_count'] or 0} reviews"
            f"  |  last review: {row['recent_review_date'] or 'unknown'}"
        )
        check = row.get("contact_check", "")
        marker = "  <-- check this" if check in ("differ", "likely_same", "site_only") else ""
        # Shortlists written before the contact columns existed have no
        # address_to; the owner column still names someone, so use it rather
        # than falling all the way back to "FAO the Owner".
        address_to = (
            row.get("address_to") or row.get("owner_name") or "FAO the Owner"
        )
        print(f"    address to: {address_to}  [{check or 'n/a'}]{marker}")
        if row.get("owner_name") or row.get("site_contact"):
            print(
                f"      register: {row.get('owner_name') or '-'}"
                f"  ({row['company_type']})"
                f"   |   website: {row.get('site_contact') or '-'}"
            )
        print(f"    maps: {row['maps_url']}")
        if row["website_url"]:
            print(f"    site: {row['website_url']}")
        while True:
            answer = input("    keep / reason code > ").strip().lower()
            if answer in ("", "k", "keep"):
                kept += 1
                break
            if answer == "q":
                return decisions
            if answer in ("?", "help"):
                print_reason_help()
                continue
            if answer in REASON_CODES:
                decisions[row["place_id"]] = answer
                break
            print(f"    ! '{answer}' is not a reason code — '?' to list them.")
        print()
        if kept >= keep_target:
            print(f"— that's {kept} kept, your target. Carry on or 'q' to stop. —\n")
    return decisions


def decisions_from_csv(path: Path) -> dict[str, str]:
    import csv

    with path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    decisions = {}
    for row in rows:
        reason = (row.get("reason") or "").strip().lower()
        if reason:
            if reason not in REASON_CODES:
                raise SystemExit(
                    f"Row '{row.get('name')}': unknown reason code '{reason}'. "
                    f"Use one of: {', '.join(sorted(REASON_CODES))}"
                )
            decisions[row["place_id"]] = reason
    return decisions


def main(argv=None) -> int:
    args = parse_args(argv)
    batch = Batch(args.batch)

    # A culled batch is re-reviewed from its survivors, not the original
    # shortlist — otherwise a second pass silently resurrects every row
    # already rejected, and its outcome overwrites approved.csv.
    already_culled = batch.approved_path.is_file()
    if already_culled and not args.full:
        rows = batch.read_shortlist(path=batch.approved_path)
        if not rows:
            raise SystemExit(
                "approved.csv is empty — everything was rejected last time.\n"
                "To start over from the original shortlist, rerun with --full."
            )
        original = len(batch.read_shortlist())
        print(f"\nThis batch was already culled: reviewing its {len(rows)} "
              f"approved row(s), of {original} originally found.")
        print("Rejections here cull further. To start over from the original "
              "shortlist instead, rerun with --full.")
    else:
        rows = batch.read_shortlist()
        if not rows:
            raise SystemExit(f"No shortlist.csv in {args.batch}. Run find.py first.")

    if args.import_path:
        import json

        path = Path(args.import_path)
        if not path.is_absolute() and not path.is_file():
            candidate = batch.path / args.import_path
            if candidate.is_file():
                path = candidate
        if not path.is_file():
            raise SystemExit(f"No such file: {args.import_path}")
        try:
            decisions = decisions_from_export(
                json.loads(path.read_text(encoding="utf-8"))
            )
        except ValueError as exc:
            raise SystemExit(f"{path.name}: {exc}") from None

        known = {row["place_id"] for row in rows}
        unknown = sorted(set(decisions) - known)
        if unknown:
            # Usually a sheet exported from a different batch.
            raise SystemExit(
                f"{path.name} names {len(unknown)} business(es) not in this "
                f"list, e.g. {unknown[0]}. Is it the right batch?"
            )
        print(f"\nImported {len(decisions)} cull decision(s) from {path.name}.")
    elif args.from_csv:
        csv_path = Path(args.from_csv)
        if not csv_path.is_absolute():
            candidate = batch.path / args.from_csv
            csv_path = candidate if candidate.is_file() else csv_path
        decisions = decisions_from_csv(csv_path)
    else:
        decisions = prompt_decisions(rows, args.keep)

    # Written through the review module, which is the one writer of these
    # files. It rewrites the rejection log rather than appending to it, so
    # --full really does start over: a business culled on an earlier pass and
    # kept on this one loses its old rejection, instead of combine and tune
    # still treating it as culled while approved.csv says keep.
    sheet = review.load(batch)
    rejected = review.apply_decisions(sheet, decisions, start_over=args.full)
    try:
        result = review.save(batch, sheet)
    except PermissionError:
        raise SystemExit(
            f"Could not write {batch.approved_path.name} — it is open in another "
            "program (probably your spreadsheet). Close it there and run this again."
        ) from None

    print(f"\nApproved: {result['approved']}  ->  {batch.approved_path}")
    print(f"Rejected this pass: {len(rejected)}  ->  {batch.rejections_path}"
          f"   ({result['rejected']} in total)")
    counts = reason_counts([{"reason": r.reason} for r in rejected])
    if counts:
        print("\nReasons this cull:")
        for code, n in sorted(counts.items(), key=lambda kv: -kv[1]):
            print(f"  {code:<16} {n}")
    all_rejections = batch.read_rejections()
    share = gut_share(all_rejections)
    if share > 0.2 and len(all_rejections) >= 10:
        print(
            f"\nNote: 'gut' is {share:.0%} of rejections. Above ~20% there's a real "
            "signal you haven't named yet — worth sitting down to articulate it."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
