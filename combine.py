#!/usr/bin/env python3
"""combine.py — merge existing batches into one re-ranked sheet.

Zero API calls: it reads batch folders already on disk. Records are
de-duplicated by Place ID and re-scored with the current weights.json, so
old batches pick up new scoring rules for free.

    # all physios, every town you've searched
    ./combine.py --all --niche physio --name physios-all-towns

    # every niche you've searched, Hitchin only
    ./combine.py --all --town Hitchin --name hitchin-everything

    # explicit folders
    ./combine.py batches/2026-08-25_physiotherapist_hitchin \\
                 batches/2026-08-26_physiotherapist_stevenage

The output is an ordinary batch folder — cull it, tune from it, open its
CSV — and it stands alone: copied records, not links.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from pipeline.combine import combine_batches, write_combined
    from pipeline.config import Config
    from pipeline.scoring import Weights
    from pipeline.storage import Batch
except ImportError as exc:
    if "pipeline" not in str(exc):
        raise
    sys.exit(
        "\ncombine.py could not find the 'pipeline' package.\n"
        "Run it from inside the project folder, with pipeline/ beside it.\n"
    )


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="combine.py", description=__doc__.split("\n")[0])
    p.add_argument("batches", nargs="*", help="batch folders to combine")
    p.add_argument("--all", action="store_true",
                   help="combine every batch folder under batches/")
    p.add_argument("--niche", help="keep only this niche (substring match: "
                                   "'physio' matches 'physiotherapist')")
    p.add_argument("--town", help="keep only this town (as in the CSV's town column)")
    p.add_argument("--name", help="output folder name (default: combined_<date>)")
    p.add_argument("--top", type=int, help="cap the combined sheet at N rows")
    p.add_argument("--weights", default="weights.json")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    config = Config.from_env()
    root = Path(config.batches_dir)

    if args.all:
        folders = sorted(
            p for p in root.iterdir()
            if p.is_dir() and (p / "shortlist.csv").is_file()
        ) if root.is_dir() else []
    else:
        folders = [Path(b) for b in args.batches]
    if not folders:
        raise SystemExit("No batch folders given. Pass paths, or --all.")

    # Never fold an earlier combined sheet back in — its records are copies
    # of other batches' records and would silently double the pool.
    sources = []
    for folder in folders:
        batch = Batch(folder)
        if not batch.path.is_dir():
            raise SystemExit(f"Not a folder: {folder}")
        if batch.read_meta().get("combined_from") and args.all:
            print(f"skipping {folder.name}: already a combined sheet")
            continue
        sources.append(batch)

    merged = combine_batches(
        sources,
        niche=args.niche,
        town=args.town,
        weights=Weights.load(args.weights),
        top=args.top,
    )
    if not merged:
        raise SystemExit(
            "Nothing matched. Check the --niche/--town spelling against the "
            "niche and town columns of the source CSVs."
        )

    label_parts = [p for p in (args.niche, args.town) if p]
    label = "-".join(label_parts) or "combined"
    name = args.name or f"combined_{date.today().isoformat()}_{label}"
    batch = write_combined(
        config.batches_dir,
        merged,
        name=name,
        source_names=[b.path.name for b in sources],
        niche_label=args.niche or "combined",
        area_label=args.town or "combined",
    )

    print(f"\nCombined {len(sources)} batch(es) -> {batch.shortlist_path}")
    print(f"{len(merged)} businesses after de-duplication"
          + (f" and filtering ({label})" if label_parts else ""))
    print("\nTop of the combined sheet:")
    for b in merged[:10]:
        town = (b.get("address") or {}).get("town") or ""
        print(f"  {b['lead_score']:>4}  {(b.get('name') or '')[:38]:<38} "
              f"{(b.get('niche') or '')[:16]:<16} {town}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
