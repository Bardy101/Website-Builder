#!/usr/bin/env python3
"""tune.py — propose scoring weight changes from your cull decisions.

    ./tune.py --batch batches/2026-09-01_physios_hitchin
    ./tune.py --batch batches/*/          # pool several batches

No machine learning: a frequency table. For each reason code it reports the
average value of each machine-visible signal among rejected businesses
versus approved ones, and proposes threshold adjustments where a cluster is
clear. It prints a diff against weights.json — you apply it by hand.

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
    from pipeline.tune import compare, format_report, propose
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
        approved.extend(batch.read_shortlist(path=batch.approved_path))
        for rej in batch.read_rejections():
            row = dict(rej.get("row") or {})
            row["reason"] = rej.get("reason")
            business = rej.get("business") or {}
            if business.get("site_score"):
                row["site_score"] = business["site_score"]
            rejected.append(row)

    if not rejected:
        print("No rejections recorded yet. Run cull.py first — tune.py has "
              "nothing to learn from until your judgement is on the record.")
        return 0

    comparison = compare(approved, rejected)
    weights_path = Path(args.weights)
    weights = json.loads(weights_path.read_text(encoding="utf-8")) if weights_path.is_file() \
        else Weights.default().__dict__
    proposals = propose(comparison, weights, min_cases=args.min_cases)

    if args.json:
        print(json.dumps({"comparison": comparison, "proposals": proposals}, indent=2))
    else:
        print(f"Pooled {len(approved)} approved, {len(rejected)} rejected "
              f"across {len(args.batch)} batch folder(s).\n")
        print(format_report(comparison, proposals))
    return 0


if __name__ == "__main__":
    sys.exit(main())
