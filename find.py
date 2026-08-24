#!/usr/bin/env python3
"""find.py — the prospect finder (Phase 0).

One command -> a ranked spreadsheet of candidate businesses with everything
you need to decide.

    ./find.py --niche physiotherapist --area "Hitchin" --radius 8000 --top 25
    ./find.py --batch-config examples/herts.yaml

Writes shortlist.csv and one business.json per row into a batch folder.
Nothing else in the pipeline needs to exist for this to be useful.
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
    from pipeline.cache import Cache
    from pipeline.companies_house import CompaniesHouseClient
    from pipeline.config import Config
    from pipeline.discover import discover, merge_results, write_batch
    from pipeline.places import PlacesClient
    from pipeline.scoring import Weights
    from pipeline.site_checks import SiteChecker
    from pipeline.storage import Batch
except ImportError as exc:
    if "pipeline" not in str(exc):
        raise
    sys.exit(
        "\nfind.py could not find the 'pipeline' package.\n\n"
        "This is one file of a project, not a standalone script. It needs the\n"
        "pipeline/ folder, weights.json and examples/ sitting beside it.\n\n"
        "Get the whole project and run it from inside that folder:\n\n"
        "  git clone -b claude/tool-build-markdown-spec-vnr7ss \\\n"
        "      https://github.com/Bardy101/Website-Builder.git\n"
        "  cd Website-Builder\n"
        "  python -m pip install -r requirements.txt\n"
        "  python find.py --help\n"
    )


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        prog="find.py",
        description="Find and rank candidate businesses for a niche and area.",
    )
    p.add_argument("--niche", help="e.g. physiotherapist")
    p.add_argument("--area", help='e.g. "Hitchin"')
    p.add_argument("--radius", type=int, default=8000, help="metres (default 8000)")
    p.add_argument("--top", type=int, default=25, help="rows in the shortlist (default 25)")
    p.add_argument(
        "--batch-config",
        help="YAML file of niche/town pairs; results merge into one ranked sheet",
    )
    p.add_argument("--batch-name", help="folder name under batches/ (default: date_niche_area)")
    p.add_argument("--weights", default="weights.json", help="scoring weights file")
    p.add_argument(
        "--no-site-checks",
        action="store_true",
        help="skip PageSpeed/HTTPS checks (faster, less accurate verdicts)",
    )
    p.add_argument(
        "--no-owner-lookup",
        action="store_true",
        help="skip the Companies House owner lookup",
    )
    p.add_argument(
        "--fixture",
        help="JSON fixture of Places responses; runs fully offline, no API key needed",
    )
    p.add_argument("--quiet", action="store_true")
    return p.parse_args(argv)


def load_batch_config(path: str) -> dict:
    import yaml

    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if "pairs" not in data:
        raise SystemExit(f"{path}: expected a top-level 'pairs' list")
    return data


def build_clients(args, config: Config):
    """Wire up the clients, with a fixture path for offline runs."""
    cache = Cache(config.cache_dir, config.cache_ttl_days)

    if args.fixture:
        from pipeline.fixtures import FixturePlacesClient

        places = FixturePlacesClient.from_file(args.fixture)
        # Offline means no live site checks or Companies House calls; the
        # fixture may still carry site_score values of its own.
        return places, None, None

    places = PlacesClient(api_key=config.google_places_api_key, cache=cache)
    checker = (
        None
        if args.no_site_checks
        else SiteChecker(
            pagespeed_api_key=config.pagespeed_api_key,
            cache=cache,
            thresholds=Weights.load(args.weights).thresholds,
        )
    )
    ch = (
        None
        if args.no_owner_lookup or not config.companies_house_api_key
        else CompaniesHouseClient(api_key=config.companies_house_api_key, cache=cache)
    )
    return places, checker, ch


def main(argv=None) -> int:
    args = parse_args(argv)
    if not args.batch_config and not (args.niche and args.area):
        raise SystemExit("Give either --niche and --area, or --batch-config.")

    config = Config.from_env()
    weights = Weights.load(args.weights)
    places, checker, ch = build_clients(args, config)
    say = (lambda _m: None) if args.quiet else (lambda m: print(m, flush=True))

    if args.batch_config:
        cfg = load_batch_config(args.batch_config)
        pairs = cfg["pairs"]
        radius = cfg.get("radius", args.radius)
        top_each = cfg.get("top_per_pair", args.top)
        overall_top = cfg.get("top", args.top)
        batch_name = args.batch_name or cfg.get("batch_name")
        niche_label = cfg.get("label", "multi")
        area_label = cfg.get("area_label", "herts")

        seen: set[str] = set()
        results = []
        for pair in pairs:
            results.append(
                discover(
                    niche=pair["niche"],
                    area=pair["area"],
                    places_client=places,
                    site_checker=checker,
                    companies_house=ch,
                    weights=weights,
                    radius_m=pair.get("radius", radius),
                    top=top_each,
                    progress=say,
                    seen_place_ids=seen,
                )
            )
        result = merge_results(results)
        result.businesses = result.businesses[:overall_top]
        result.rows = result.rows[:overall_top]
        batch = Batch.create(
            config.batches_dir, niche=niche_label, area=area_label, name=batch_name
        )
    else:
        result = discover(
            niche=args.niche,
            area=args.area,
            places_client=places,
            site_checker=checker,
            companies_house=ch,
            weights=weights,
            radius_m=args.radius,
            top=args.top,
            progress=say,
        )
        batch = Batch.create(
            config.batches_dir, niche=args.niche, area=args.area, name=args.batch_name
        )

    write_batch(batch, result)

    say("")
    say(f"Shortlist:  {batch.shortlist_path}")
    say(f"Businesses: {len(result.businesses)} kept, {len(result.excluded)} excluded")
    if result.rows:
        say("")
        say("Top of the list:")
        for row in result.rows[:10]:
            say(
                f"  {str(row['lead_score']):>4}  {row['name'][:38]:<38} "
                f"{row['site_verdict']:<12} {row['town']}"
            )
    say("")
    say("Next: open the CSV, cull to 10-15, then run ./cull.py --batch "
        f"{batch.path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
