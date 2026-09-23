#!/usr/bin/env python3
"""mockup.py — a Webflow-ready brief for the prospects you picked.

    ./mockup.py --batch batches/2026-09-01_physios_hitchin --place ChIJ...
    ./mockup.py --batch <dir> --approved          # every business you kept
    ./mockup.py --batch <dir> --place ChIJ... --open

For each business, writes into <batch>/<place_id>/mockup/:

    prompt.txt   paste into Webflow's AI Site Builder as-is
    brief.html   the build sheet: Copy buttons for every block, the accent
                 colour, what to fill in by hand, their current site
    mockup.json  the same content as data (the tier-2 variable schema)

Everything in the prompt comes from what the prospect phase verified —
Places, Companies House, their own current website. Nothing is invented;
anything missing becomes a visible [placeholder].

The concept banner names your business: set YOUR_BUSINESS_NAME in .env, or
pass --your-business.
"""

from __future__ import annotations

import argparse
import os
import sys
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

try:
    from pipeline import mockup
    from pipeline.config import Config
    from pipeline.screenshots import ScreenshotCapturer, read_page
    from pipeline.storage import Batch
except ImportError as exc:
    if "pipeline" not in str(exc):
        raise
    sys.exit(
        "\nmockup.py could not find the 'pipeline' package.\n"
        "Run it from inside the project folder, with pipeline/ beside it.\n"
    )


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="mockup.py", description=__doc__.split("\n")[0])
    p.add_argument("--batch", required=True, help="path to the batch folder")
    which = p.add_mutually_exclusive_group(required=True)
    which.add_argument("--place", action="append", metavar="PLACE_ID",
                       help="a business to brief (repeatable)")
    which.add_argument("--approved", action="store_true",
                       help="every business kept in approved.csv")
    p.add_argument("--your-business", default=None,
                   help="your business name, for the concept banner "
                        "(default: YOUR_BUSINESS_NAME from .env)")
    p.add_argument("--open", action="store_true",
                   help="open the build sheet in your browser when done")
    return p.parse_args(argv)


def cache_dir() -> Path:
    path = Path(Config.from_env().cache_dir)
    return path if path.is_absolute() else HERE / path


def rows_by_id(batch: Batch) -> dict:
    """Shortlist rows, overlaid by approved.csv — which holds your edits."""
    rows = {r.get("place_id"): r for r in batch.read_shortlist()}
    if batch.approved_path.is_file():
        for r in batch.read_shortlist(path=batch.approved_path):
            rows[r.get("place_id")] = {**rows.get(r.get("place_id"), {}), **r}
    return rows


def approved_ids(batch: Batch) -> list[str]:
    if not batch.approved_path.is_file():
        return []
    return [r["place_id"] for r in batch.read_shortlist(path=batch.approved_path)
            if r.get("place_id")]


def main(argv=None) -> int:
    args = parse_args(argv)
    batch = Batch(args.batch)
    if not batch.path.is_dir():
        raise SystemExit(f"Not a folder: {args.batch}")

    Config.from_env()  # loads .env, for YOUR_BUSINESS_NAME
    your_business = (args.your_business if args.your_business is not None
                     else os.environ.get("YOUR_BUSINESS_NAME", "")).strip()

    ids = approved_ids(batch) if args.approved else list(dict.fromkeys(args.place))
    if not ids:
        raise SystemExit("No businesses to brief — approved.csv is empty or missing. "
                         "Keep some in the Shortlist tab (or cull.py) first.")

    rows = rows_by_id(batch)
    shots = ScreenshotCapturer(cache_dir=cache_dir())
    written = []
    for place_id in ids:
        business = batch.read_business(place_id)
        row = rows.get(place_id)
        if row is None and business is None:
            print(f"  {place_id}: not in this batch — skipped")
            continue
        desktop, _mobile = shots.paths_for(place_id)
        page = read_page(shots.page_path_for(place_id))
        brief = mockup.build_brief(
            row or {}, business,
            site_html=(page or {}).get("html"),
            screenshot=desktop if desktop.is_file() else None,
            your_business=your_business,
        )
        paths = mockup.write(batch.business_dir(place_id) / "mockup", brief,
                             screenshot=desktop if desktop.is_file() else None)
        written.append(paths)
        todo = f", {len(brief.placeholders)} to fill by hand" if brief.placeholders else ""
        print(f"  {brief.name}: {brief.shape} layout, accent {brief.accent}{todo}")
        print(f"    {paths['html']}")

    if not written:
        return 1
    if not your_business:
        print("\nThe banner says [your business name] — set YOUR_BUSINESS_NAME in .env "
              "or pass --your-business.")
    print("\nOpen brief.html, copy the prompt into Webflow's AI Site Builder, "
          "then work down the sheet.")
    if args.open:
        for paths in written:
            webbrowser.open(paths["html"].resolve().as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())
