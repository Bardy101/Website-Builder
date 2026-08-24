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
    from pipeline.cull import REASON_CODES, gut_share, reason_counts, split_shortlist
    from pipeline.storage import Batch, utcnow
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
        "--from-csv",
        help="read decisions from a 'reason' column in this CSV instead of prompting",
    )
    p.add_argument("--keep", type=int, default=15, help="target number to keep (guide only)")
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
        print(
            f"    address to: {row.get('address_to') or 'FAO the Owner'}"
            f"  [{check or 'n/a'}]{marker}"
        )
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
    rows = batch.read_shortlist()
    if not rows:
        raise SystemExit(f"No shortlist.csv in {args.batch}. Run find.py first.")

    if args.from_csv:
        csv_path = Path(args.from_csv)
        if not csv_path.is_absolute():
            candidate = batch.path / args.from_csv
            csv_path = candidate if candidate.is_file() else csv_path
        decisions = decisions_from_csv(csv_path)
    else:
        decisions = prompt_decisions(rows, args.keep)

    approved, rejected = split_shortlist(rows, decisions)
    batch.write_shortlist(approved, path=batch.approved_path)

    for row in rejected:
        business = batch.read_business(row["place_id"]) or {}
        batch.append_rejection(
            {
                "place_id": row["place_id"],
                "name": row.get("name"),
                "reason": row["reason"],
                "rejected_at": utcnow(),
                "row": {k: v for k, v in row.items() if k != "reason"},
                "business": business,
            }
        )

    counts = reason_counts(rejected)
    meta = batch.read_meta()
    meta.setdefault("rejection_reasons", {})
    for code, n in counts.items():
        meta["rejection_reasons"][code] = meta["rejection_reasons"].get(code, 0) + n
    meta["status_counts"] = {
        **meta.get("status_counts", {}),
        "approved": len(approved),
        "rejected": len(batch.read_rejections()),
    }
    meta["culled"] = utcnow()
    batch.write_meta(meta)

    print(f"\nApproved: {len(approved)}  ->  {batch.approved_path}")
    print(f"Rejected: {len(rejected)}  ->  {batch.rejections_path}")
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
