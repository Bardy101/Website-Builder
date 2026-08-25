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
    from pipeline.site_contacts import SiteContactFinder
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
        "--no-site-contacts",
        action="store_true",
        help="skip reading the business's own About page for a named contact",
    )
    p.add_argument(
        "--fixture",
        help="JSON fixture of Places responses; runs fully offline, no API key needed",
    )
    p.add_argument(
        "--workers", type=int, default=8,
        help="concurrent site/owner lookups (default 8; 1 = one at a time)",
    )
    p.add_argument(
        "--refresh",
        action="store_true",
        help="ignore the cache and re-fetch everything (costs real API calls)",
    )
    p.add_argument("--quiet", action="store_true")
    # Set by run.py so the closing hint points at the menu, not a command line.
    p.add_argument("--from-menu", action="store_true", help=argparse.SUPPRESS)
    return p.parse_args(argv)


def load_batch_config(path: str) -> dict:
    import yaml

    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if "pairs" not in data:
        raise SystemExit(f"{path}: expected a top-level 'pairs' list")
    return data


def build_clients(args, config: Config):
    """Wire up the clients, with a fixture path for offline runs."""
    cache = Cache(
        config.cache_dir, config.cache_ttl_days, bypass=getattr(args, "refresh", False)
    )

    if args.fixture:
        from pipeline.fixtures import FixturePlacesClient

        places = FixturePlacesClient.from_file(args.fixture)
        # Offline means no live site checks or Companies House calls; the
        # fixture may still carry site_score values of its own.
        return places, None, None, None

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
    contacts = None if args.no_site_contacts else SiteContactFinder(cache=cache)
    return places, checker, ch, contacts


def main(argv=None) -> int:
    args = parse_args(argv)
    if not args.batch_config and not (args.niche and args.area):
        raise SystemExit("Give either --niche and --area, or --batch-config.")

    config = Config.from_env()
    weights = Weights.load(args.weights)
    places, checker, ch, contacts = build_clients(args, config)
    say = (lambda _m: None) if args.quiet else (lambda m: print(m, flush=True))

    # The demo fixture holds a handful of invented businesses, not the whole
    # internet. Asking it for a niche it doesn't cover returns nothing, which
    # reads as a broken tool unless we say plainly what it does cover.
    if args.fixture and args.niche and args.area and not places.covers(args.niche, args.area):
        covered = places.available()
        raise SystemExit(
            f"\nThe demo fixture has no '{args.niche}' in '{args.area}'.\n\n"
            "It contains only invented sample businesses, so it can only answer for:\n"
            + "".join(f"    --niche {n} --area {a}\n" for n, a in covered)
            + "\nTo search for real businesses in any niche or town, drop --fixture\n"
            "and set GOOGLE_PLACES_API_KEY in your .env file:\n\n"
            f"    python find.py --niche {args.niche} --area \"{args.area}\"\n"
        )

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
                    site_contacts=contacts,
                    weights=weights,
                    radius_m=pair.get("radius", radius),
                    top=top_each,
                    progress=say,
                    seen_place_ids=seen,
                    workers=args.workers,
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
            site_contacts=contacts,
            weights=weights,
            radius_m=args.radius,
            top=args.top,
            progress=say,
            workers=args.workers,
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
    if not result.rows:
        say("")
        say("No businesses matched. Things worth checking:")
        say("  - Is the niche a term Google Places would recognise? Try the")
        say("    words a customer would search: 'plumber', not 'plumbing services'.")
        say("  - Is the town spelled as Google knows it? '\"Hitchin, Hertfordshire\"'")
        say("    is safer than 'Hitchin'.")
        if result.excluded:
            say(f"  - {len(result.excluded)} were found but excluded "
                "(chain or not operational).")
        return 1

    needs_human = [
        b for b in result.businesses
        if (b.get("addressee") or {}).get("needs_human")
    ]
    if needs_human:
        say("")
        say(f"{len(needs_human)} row(s) need you to pick who to address:")
        for b in needs_human[:10]:
            say(f"  {b['name']}")
            say(f"    {b['addressee']['note']}")

    stats = getattr(places, "stats", None)
    if stats and not args.fixture:
        searches = stats["search_fetched"] + stats.get("area_fetched", 0)
        details = stats["details_fetched"]
        cached = (stats["search_requested"] - searches) + (
            stats["details_requested"] - details
        )
        say("")
        say("Google API usage this run:")
        say(f"  {searches:>4}  Text Search    (Pro tier, 5,000 free/month)")
        say(f"  {details:>4}  Place Details  (Enterprise tier, 1,000 free/month)")
        if cached:
            say(f"  {cached:>4}  served from the 30-day cache, costing nothing")
        if searches + details == 0:
            say("  Nothing was billed — every Places lookup came from the cache.")
        elif details:
            say(f"  At this rate, roughly {1000 // max(details, 1)} more runs "
                "this month within the free Enterprise allowance.")

        # Reading a business's own website is not a Google call. Saying so
        # explicitly matters: a run can show real activity here while billing
        # nothing, which is exactly what a re-run for contact data looks like.
        contact_stats = getattr(contacts, "stats", None) if contacts else None
        if contact_stats and contact_stats.get("fetched"):
            say(f"  {contact_stats['fetched']:>4}  pages read from the businesses' "
                "own websites (not billed by anyone)")

    say("")
    if args.from_menu:
        say("Next: cull this to 10-15 with 'Review a shortlist' on the menu.")
    else:
        say("Next: open the CSV, cull to 10-15, then run ./cull.py --batch "
            f"{batch.path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
