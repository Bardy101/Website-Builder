#!/usr/bin/env python3
"""contactsheet.py — render a batch as a visual cull sheet.

    ./contactsheet.py --batch batches/2026-09-01_physios_hitchin
    ./contactsheet.py --batch <dir> --sort name --min-score 20

Writes a single self-contained contactsheet.html into the batch directory.
Thumbnails are inlined, so it opens with no network and can be sent anywhere.

Keep/cull each tile with a reason code, then Export decisions to download
decisions.json and apply it with:

    ./cull.py --batch <dir> --import decisions.json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from pipeline.contactsheet import build_cells, render
    from pipeline.storage import Batch
except ImportError as exc:
    if "pipeline" not in str(exc):
        raise
    sys.exit(
        "\ncontactsheet.py could not find the 'pipeline' package.\n"
        "Run it from inside the project folder, with pipeline/ beside it.\n"
    )


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="contactsheet.py",
                                description=__doc__.split("\n")[0])
    p.add_argument("--batch", required=True, help="path to the batch folder")
    p.add_argument("--sort", choices=["score", "name"], default="score")
    p.add_argument("--min-score", type=int, default=None,
                   help="omit candidates scoring below N")
    p.add_argument("--approved", action="store_true",
                   help="render approved.csv rather than the full shortlist")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    batch = Batch(args.batch)
    if not batch.path.is_dir():
        raise SystemExit(f"Not a folder: {args.batch}")

    source = batch.approved_path if args.approved else batch.shortlist_path
    rows = batch.read_shortlist(path=source)
    if not rows:
        raise SystemExit(
            f"No rows in {source.name}. Run find.py for this batch first."
        )

    # business.json carries the desktop shot path, which the CSV does not.
    businesses = {b.get("place_id"): b for b in batch.iter_businesses()}
    cells = build_cells(rows, businesses, sort=args.sort, min_score=args.min_score)
    if not cells:
        raise SystemExit("Nothing to show after filtering — lower --min-score.")

    out = batch.path / "contactsheet.html"
    out.write_text(render(cells, batch_name=batch.path.name), encoding="utf-8")

    with_shots = sum(1 for c in cells if c["mobile_thumb"])
    size_kb = out.stat().st_size / 1024
    print(f"\n{out}")
    print(f"{len(cells)} candidates, {with_shots} with screenshots, {size_kb:.0f} KB")
    print("\nOpen it, keep/cull each tile, then Export decisions and run:")
    print(f"  ./cull.py --batch {batch.path} --import decisions.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
