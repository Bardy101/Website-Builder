#!/usr/bin/env python3
"""tune.py — propose scoring weight changes from your cull decisions.

    ./tune.py --batch batches/2026-09-01_physios_hitchin
    ./tune.py --batch batches/*/          # pool several batches

No machine learning: a frequency table. For each reason code it compares
what the machine saw on the businesses you rejected — the site problems
found, the points they add up to, platform, review recency — against the
ones you kept, and proposes changes to weights.json where a cluster is
clear. Nothing changes until you say so:

    ./tune.py --batch batches/*/ --apply     # write them, after a backup

Cull good-looking sites as 'site_fine': that is what teaches it to stop
bringing you sites that don't need work.

Only worth running once you have rejections to learn from (after batch two).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Running this from another folder, or copying it out of the project on its
# own, both break the imports below: the script needs the pipeline/ package
# beside it. Adding the script's own directory to the path fixes the first
# case; the guard below explains the second in English rather than a traceback.
sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from pipeline.scoring import Weights
    from pipeline.storage import Batch
    from pipeline.tune import apply, compare, format_report, propose
except ImportError as exc:
    if "pipeline" not in str(exc):
        raise
    sys.exit(
        "\ntune.py could not find the 'pipeline' package.\n\n"
        "This is one file of a project, not a standalone script. It needs the\n"
        "pipeline/ folder, weights.json and examples/ sitting beside it.\n\n"
        "Get the whole project and run it from inside that folder:\n\n"
        "  git clone -b claude/tool-build-markdown-spec-vnr7ss \\\n"
        "      https://github.com/Bardy101/Website-Builder.git\n"
        "  cd Website-Builder\n"
        "  python -m pip install -r requirements.txt\n"
        "  python tune.py --help\n"
    )


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="tune.py", description=__doc__.split("\n")[0])
    p.add_argument("--batch", nargs="+", required=True, help="one or more batch folders")
    p.add_argument("--weights", default="weights.json")
    p.add_argument("--min-cases", type=int, default=3,
                   help="minimum rejections per reason code before proposing (default 3)")
    p.add_argument("--json", action="store_true", help="emit the comparison as JSON")
    p.add_argument("--apply", action="store_true",
                   help="write the proposals into weights.json (backed up first)")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    approved: list[dict] = []
    rejected: list[dict] = []
    for folder in args.batch:
        batch = Batch(folder)
        if not batch.path.is_dir():
            print(f"skipping {folder}: not a folder", file=sys.stderr)
            continue
        for row in batch.read_shortlist(path=batch.approved_path):
            # The business record carries the full staleness analysis;
            # the CSV row only its summary columns.
            business = batch.read_business(row.get("place_id") or "") or {}
            approved.append(_with_record(row, business))
        for rej in batch.read_rejections():
            row = dict(rej.get("row") or {})
            row["reason"] = rej.get("reason")
            rejected.append(_with_record(row, rej.get("business") or {}))

    if not rejected:
        print("No rejections recorded yet. Run cull.py first — tune.py has "
              "nothing to learn from until your judgement is on the record.")
        return 0

    weights_path = Path(args.weights)
    if not weights_path.is_file():
        beside = Path(__file__).resolve().parent / weights_path.name
        weights_path = beside if beside.is_file() else weights_path
    if weights_path.is_file():
        weights = json.loads(weights_path.read_text(encoding="utf-8"))
    else:
        default = Weights.default()
        weights = {"signals": default.signals, "thresholds": default.thresholds}
    comparison = compare(approved, rejected, weights=weights)
    proposals = propose(comparison, weights, min_cases=args.min_cases)

    if args.apply:
        return _apply(weights_path, weights, proposals)

    if args.json:
        print(json.dumps({"comparison": comparison, "proposals": proposals}, indent=2))
    else:
        print(f"Pooled {len(approved)} approved, {len(rejected)} rejected "
              f"across {len(args.batch)} batch folder(s).\n")
        print(format_report(comparison, proposals))
    return 0


def _with_record(row: dict, business: dict) -> dict:
    """A CSV row plus the parts of its business record tune learns from."""
    out = dict(row)
    if business.get("site_score"):
        out["site_score"] = business["site_score"]
    if isinstance(business.get("staleness"), dict):
        out["staleness"] = business["staleness"]
    return out


def _apply(weights_path: Path, weights: dict, proposals: list[dict]) -> int:
    """Write the applicable proposals, after keeping a dated copy."""
    applicable = [p for p in proposals if p.get("path")]
    if not applicable:
        print("Nothing to apply — no proposal names a setting to change.")
        return 0
    if not weights_path.is_file():
        raise SystemExit(f"No {weights_path} to apply to.")
    from datetime import datetime

    backup = weights_path.with_name(
        f"{weights_path.name}.bak-{datetime.now().strftime('%Y%m%d-%H%M%S')}")
    backup.write_text(weights_path.read_text(encoding="utf-8"), encoding="utf-8")
    changed = apply(weights, applicable)
    weights_path.write_text(json.dumps(weights, indent=2, ensure_ascii=False) + "\n",
                            encoding="utf-8")
    print(f"Applied to {weights_path.name} (previous version kept as {backup.name}):")
    for line in changed:
        print(f"  {line}")
    print("\nNew finds use these at once. Re-score existing batches by combining "
          "them — combine re-scores under the current weights.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
