#!/usr/bin/env python3
"""run.py — the friendly front door.

Double-click run.bat (Windows) or run.command (macOS), or type `python run.py`.
You get a menu; it asks for the niche and town in plain English and runs the
right script for you. Nothing here does anything the individual CLIs can't —
it just means you don't have to remember flags.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

try:
    from pipeline.config import Config
    from pipeline.storage import Batch
except ImportError as exc:
    if "pipeline" not in str(exc):
        raise
    sys.exit(
        "\nrun.py could not find the 'pipeline' package.\n"
        "Run this from inside the project folder, with pipeline/ beside it.\n"
    )

FIXTURE = HERE / "examples" / "sample_fixture.json"

# Niches worth starting with: static content, mostly ltd companies,
# review-conscious. See the build spec's open decisions.
SUGGESTED = [
    "physiotherapist", "accountant", "solicitor", "dentist", "osteopath",
    "plumber", "electrician", "roofer", "chiropractor", "vet",
]


def clear() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        answer = input(f"{prompt}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        raise SystemExit(0)
    return answer or default


def ask_yes_no(prompt: str, default: bool = False) -> bool:
    hint = "Y/n" if default else "y/N"
    answer = ask(f"{prompt} ({hint})").strip().lower()
    if not answer:
        return default
    return answer.startswith("y")


def pause() -> None:
    try:
        input("\nPress Enter to return to the menu…")
    except (EOFError, KeyboardInterrupt):
        raise SystemExit(0)


def open_in_default_app(path: Path) -> None:
    """Open a file in whatever the OS uses for it (Excel, Numbers, …)."""
    try:
        if os.name == "nt":
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.run(["open", str(path)], check=False)
        else:
            subprocess.run(["xdg-open", str(path)], check=False)
    except Exception as exc:
        print(f"Couldn't open it automatically ({exc}).")
        print(f"It's here: {path}")


def batches_dir() -> Path:
    return Path(Config.from_env().batches_dir)


def list_batches() -> list[Path]:
    root = batches_dir()
    if not root.is_dir():
        return []
    folders = [p for p in root.iterdir() if p.is_dir() and (p / "shortlist.csv").is_file()]
    return sorted(folders, key=lambda p: p.stat().st_mtime, reverse=True)


def choose_batch(action: str) -> Path | None:
    folders = list_batches()
    if not folders:
        print("\nNo batches yet — run 'Find prospects' first.")
        return None
    print(f"\nWhich batch would you like to {action}?\n")
    for i, folder in enumerate(folders[:15], 1):
        rows = len(Batch(folder).read_shortlist())
        approved = Batch(folder).approved_path
        done = " (already culled)" if approved.is_file() else ""
        print(f"  {i}  {folder.name}  — {rows} rows{done}")
    print("  b  Back")
    answer = ask("\nNumber", "1")
    if answer.lower() == "b":
        return None
    try:
        return folders[int(answer) - 1]
    except (ValueError, IndexError):
        print("That wasn't one of the options.")
        return None


# -- actions ---------------------------------------------------------------



def write_pairs_config(pairs: list[tuple[str, str]]):
    """Write a temporary batch-config YAML for a multi-pair find run."""
    import tempfile

    from pipeline.storage import slugify

    niches = list(dict.fromkeys(n for n, _ in pairs))
    areas = list(dict.fromkeys(a for _, a in pairs))
    lines = [
        f"label: {slugify('-'.join(niches))[:60]}",
        f"area_label: {slugify('-'.join(areas))[:60]}",
        "pairs:",
    ]
    for niche, area in pairs:
        lines.append(f'  - {{ niche: "{niche}", area: "{area}" }}')
    handle = tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", prefix="menu-find-", delete=False,
        encoding="utf-8",
    )
    with handle:
        handle.write("\n".join(lines) + "\n")
    return Path(handle.name)


def action_find() -> None:
    config = Config.from_env()
    has_key = bool(config.google_places_api_key)

    print("\nFind prospects")
    print("-" * 60)
    if not has_key:
        print("No GOOGLE_PLACES_API_KEY found, so this will use the demo data.")
        print("Add a key to your .env file to search for real businesses.\n")
    print("Suggested niches: " + ", ".join(SUGGESTED[:6]))
    print("(use the word a customer would search — 'plumber', not 'plumbing services')\n")

    demo = not has_key or ask_yes_no("Use the demo data instead of the real API?", False)

    if demo:
        from pipeline.fixtures import FixturePlacesClient

        covered = FixturePlacesClient.from_file(FIXTURE).available()
        print("\nThe demo data only covers:")
        for niche, area in covered:
            print(f"    {niche} in {area}")
        niche = covered[0][0] if covered else "physiotherapist"
        area = covered[0][1] if covered else "Hitchin"
        print(f"\nUsing {niche} in {area}.")
        pairs = [(niche, area)]
    else:
        pairs = []
        while True:
            niche = ask("Niche", "physiotherapist")
            area = ask("Town or area", "Hitchin, Hertfordshire")
            pairs.append((niche, area))
            if not ask_yes_no(
                "Add another niche/town to this run? (merged into one sheet)", False
            ):
                break

    radius = ask("Radius in metres", "8000")
    top = ask("How many rows in the shortlist", "25")
    refresh = (not demo) and ask_yes_no(
        "Ignore the 30-day cache and re-fetch fresh data? Costs API calls", False
    )

    config_path = None
    if len(pairs) == 1:
        niche, area = pairs[0]
        argv = ["--niche", niche, "--area", area, "--radius", radius,
                "--top", top, "--from-menu"]
    else:
        # Several pairs run through find.py's batch-config path, merged and
        # de-duplicated into one sheet. The YAML is written for it here so
        # nobody has to author a config file by hand.
        config_path = write_pairs_config(pairs)
        argv = ["--batch-config", str(config_path), "--radius", radius,
                "--top", top, "--from-menu"]
    if demo:
        argv += ["--fixture", str(FIXTURE)]
    if refresh:
        argv += ["--refresh"]

    print("\n" + "-" * 60)
    import find

    try:
        code = find.main(argv)
    except SystemExit as exc:
        # find.py exits with a message for bad input; show it, don't crash out.
        print(exc.code if isinstance(exc.code, str) else "")
        return
    finally:
        if config_path is not None:
            config_path.unlink(missing_ok=True)
    if code == 0:
        latest = list_batches()
        if latest and ask_yes_no("\nOpen the shortlist in your spreadsheet app?", True):
            open_in_default_app(latest[0] / "shortlist.csv")


def action_cull() -> None:
    folder = choose_batch("review")
    if folder is None:
        return
    print("\nHow would you like to review it?\n")
    print("  1  One at a time here (shows the links, you type keep or a reason)")
    print("  2  In a spreadsheet (add a 'reason' column, save, then import it)")
    choice = ask("\nNumber", "1")

    import cull

    if choice == "2":
        path = folder / "shortlist.csv"
        print(f"\nOpening {path.name}. Add a column headed 'reason' and fill it in")
        print("for the ones you're rejecting. Reason codes:\n")
        from pipeline.cull import REASON_CODES

        for code, meaning in REASON_CODES.items():
            print(f"    {code:<16} {meaning}")
        open_in_default_app(path)
        input("\nSave and close the file, then press Enter to import your decisions…")
        try:
            cull.main(["--batch", str(folder), "--from-csv", "shortlist.csv"])
        except SystemExit as exc:
            print(exc.code if isinstance(exc.code, str) else "")
    else:
        try:
            cull.main(["--batch", str(folder)])
        except SystemExit as exc:
            print(exc.code if isinstance(exc.code, str) else "")


def action_tune() -> None:
    folders = list_batches()
    if not folders:
        print("\nNo batches yet.")
        return
    print("\nPooling every batch that has rejections recorded.")
    import tune

    try:
        tune.main(["--batch"] + [str(f) for f in folders])
    except SystemExit as exc:
        print(exc.code if isinstance(exc.code, str) else "")


def action_open() -> None:
    folder = choose_batch("open")
    if folder is None:
        return
    approved = folder / "approved.csv"
    target = approved if approved.is_file() else folder / "shortlist.csv"
    print(f"\nOpening {target.name}…")
    open_in_default_app(target)


def action_check() -> None:
    print("\nSetup check")
    print("-" * 60)
    print(f"Python           {sys.version.split()[0]}")
    print(f"Project folder   {HERE}")

    for module, needed_for in [("requests", "API calls"), ("yaml", "batch configs")]:
        try:
            __import__(module)
            print(f"{module:<16} installed          ({needed_for})")
        except ImportError:
            print(f"{module:<16} MISSING            ({needed_for})")
            print(f"                 fix: python -m pip install -r requirements.txt")

    config = Config.from_env()
    env_file = HERE / ".env"
    print(f"\n.env file        {'found' if env_file.is_file() else 'not created yet'}")
    for label, value, needed in [
        ("Places key", config.google_places_api_key, "required for real searches"),
        ("PageSpeed key", config.pagespeed_api_key, "optional — mobile scores"),
        ("Companies House", config.companies_house_api_key, "optional — owner names"),
    ]:
        state = "set" if value else "not set"
        print(f"{label:<16} {state:<18} ({needed})")

    if not config.google_places_api_key:
        print("\nWithout a Places key you can still run the demo data from the menu.")
        print("To search real businesses, choose 'Set up API keys' on the menu —")
        print("it writes the .env file for you.")

    folders = list_batches()
    print(f"\nBatches so far   {len(folders)}")
    for folder in folders[:5]:
        print(f"    {folder.name}")

    # The cache is a dot-folder full of JSON — it looks like junk, and
    # deleting it silently converts every future re-run back to full price.
    cache_root = Path(config.cache_dir)
    if not cache_root.is_absolute():
        cache_root = HERE / cache_root
    if cache_root.is_dir():
        entries = list(cache_root.rglob("*.json"))
        details = len(list((cache_root / "places_details").glob("*.json"))) \
            if (cache_root / "places_details").is_dir() else 0
        print(f"\nCache            {len(entries)} saved API responses "
              f"in {cache_root}")
        if details:
            print(f"                 {details} of them are Place Details "
                  "— the billable ones")
        print("                 Keep this folder: it is what makes re-runs free.")
    else:
        print(f"\nCache            none yet ({cache_root})")



KEY_INFO = [
    (
        "GOOGLE_PLACES_API_KEY",
        "Google Places",
        "Required to search for real businesses.",
        "https://console.cloud.google.com/ -> create a project -> enable\n"
        "     'Places API (New)' -> Credentials -> Create API key.\n"
        "     Billing must be enabled, but there is a free monthly allowance\n"
        "     that comfortably covers a batch or two. Check current pricing at\n"
        "     https://mapsplatform.google.com/pricing/",
    ),
    (
        "PAGESPEED_API_KEY",
        "PageSpeed Insights",
        "Optional. Gives mobile scores, so 'poor' and 'dated' verdicts mean\n"
        "     something. Without it sites you have are marked 'unknown'.",
        "You almost certainly do not need a new key — your Places key\n"
        "     works for this too. Enabling an API in Google Cloud does not\n"
        "     issue a key; keys live under Credentials and are shared.\n"
        "     In the console: Credentials -> click your key -> under 'API\n"
        "     restrictions' tick 'PageSpeed Insights API' as well -> Save.\n"
        "     Free.",
    ),
    (
        "COMPANIES_HOUSE_API_KEY",
        "Companies House",
        "Optional. Fills in owner names so letters open 'Dear Sarah'\n"
        "     instead of 'FAO the Owner'.",
        "Nothing to do with Google — it is a UK government service.\n"
        "     https://developer.company-information.service.gov.uk/\n"
        "     Sign up -> Manage applications -> Create an application, and\n"
        "     choose LIVE, not test/sandbox (sandbox holds invented\n"
        "     companies). Open the application -> Create new key -> client\n"
        "     type REST, not Streaming or OAuth2. Free.",
    ),
]


def read_existing_env() -> dict[str, str]:
    """Parse the current .env, so setup edits rather than overwrites."""
    env_path = HERE / ".env"
    values: dict[str, str] = {}
    if not env_path.is_file():
        return values
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def write_env(values: dict[str, str]) -> Path:
    """Write .env with comments intact. Windows Explorer won't make this file."""
    env_path = HERE / ".env"
    lines = [
        "# Postal Outreach Pipeline — API keys.",
        "# Written by the setup menu. Safe to edit by hand.",
        "# This file is gitignored: your keys never reach GitHub.",
        "",
    ]
    for key, label, purpose, _ in KEY_INFO:
        lines.append(f"# {label} — {purpose.splitlines()[0].strip()}")
        lines.append(f"{key}={values.get(key, '')}")
        lines.append("")
    for key in ("PIPELINE_CACHE_DIR", "PIPELINE_BATCHES_DIR", "PIPELINE_CACHE_TTL_DAYS"):
        if key in values:
            lines.append(f"{key}={values[key]}")
    env_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return env_path


def action_keys() -> None:
    print("\nAPI keys")
    print("-" * 60)
    print("These live in a file called .env in the project folder. You only")
    print("need them to search for real businesses — the demo data works")
    print("without any. Press Enter to skip any key and keep what's there.\n")

    existing = read_existing_env()
    values = dict(existing)

    for key, label, purpose, where in KEY_INFO:
        current = existing.get(key, "")
        shown = f"set, ending …{current[-4:]}" if current else "not set"
        print(f"\n{label}  ({shown})")
        print(f"     {purpose}")
        print(f"     Get one: {where}")

        # PageSpeed runs on the same Google key as Places. Offering the reuse
        # saves a trip to the console for a key that was never needed.
        places_key = values.get("GOOGLE_PLACES_API_KEY", "")
        if key == "PAGESPEED_API_KEY" and places_key and places_key != current:
            if ask_yes_no(
                f"     Reuse your Places key (…{places_key[-4:]}) for this?", True
            ):
                values[key] = places_key
                print("     Using the Places key. Make sure 'PageSpeed Insights API'")
                print("     is ticked under that key's API restrictions.")
                continue

        answer = ask("     Paste the key (Enter to skip)").strip()
        if answer:
            values[key] = answer

    if values == existing and (HERE / ".env").is_file():
        print("\nNothing changed.")
        return
    if not any(values.get(k) for k, _, _, _ in KEY_INFO):
        print("\nNo keys entered, so nothing was written.")
        print("You can still use the demo data from the menu.")
        return

    path = write_env(values)
    # Make the new keys live for the rest of this session, not just the next.
    for key, value in values.items():
        if value:
            os.environ[key] = value

    print(f"\nWritten to {path}")
    have = [label for key, label, _, _ in KEY_INFO if values.get(key)]
    print("Keys set: " + ", ".join(have))
    if values.get("GOOGLE_PLACES_API_KEY"):
        print("\nYou can now search real businesses: 'Find prospects' on the menu,")
        print("and answer 'n' when it offers the demo data.")



def action_test_keys() -> None:
    from pipeline.keycheck import test_companies_house, test_pagespeed, test_places

    config = Config.from_env()
    print("\nTest API keys")
    print("-" * 60)
    print("Makes one small call per key to check it actually works —")
    print("this is what catches a key restricted the wrong way.\n")

    print("Google Places…", flush=True)
    print(test_places(config.google_places_api_key))

    if config.pagespeed_api_key:
        print("\nPageSpeed Insights… (this one takes 20-30 seconds)", flush=True)
    print(test_pagespeed(config.pagespeed_api_key))

    print()
    print(test_companies_house(config.companies_house_api_key))

    print("\n" + "-" * 60)
    print("A restriction change can take a few minutes to take effect,")
    print("so if you have just edited the key, wait and test again.")



def action_combine() -> None:
    folders = list_batches()
    if len(folders) < 1:
        print("\nNo batches yet — run 'Find prospects' first.")
        return
    print("\nCombine batches into one re-ranked sheet")
    print("-" * 60)
    print("Costs nothing — it re-reads what's already on disk, de-duplicates")
    print("by business, and re-scores with the current weights.\n")
    for i, folder in enumerate(folders[:20], 1):
        combined = " (combined sheet)" if Batch(folder).read_meta().get("combined_from") else ""
        print(f"  {i}  {folder.name}{combined}")
    answer = ask("\nNumbers to combine (e.g. 1,3,4) or 'a' for all", "a")

    if answer.lower() == "a":
        chosen = [f for f in folders
                  if not Batch(f).read_meta().get("combined_from")]
    else:
        try:
            chosen = [folders[int(i) - 1] for i in answer.replace(" ", "").split(",")]
        except (ValueError, IndexError):
            print("That wasn't a list of numbers from the list.")
            return
    if not chosen:
        print("Nothing to combine.")
        return

    niche = ask("Keep only one niche? (blank = all; 'physio' matches "
                "'physiotherapist')").strip() or None
    town = ask("Keep only one town? (blank = all)").strip() or None

    from datetime import date

    from pipeline.combine import combine_batches, write_combined
    from pipeline.scoring import Weights

    merged = combine_batches(
        [Batch(f) for f in chosen], niche=niche, town=town,
        weights=Weights.load(HERE / "weights.json"),
    )
    if not merged:
        print("\nNothing matched that filter — check the spelling against the")
        print("niche and town columns of the source CSVs.")
        return

    label = "-".join(x for x in (niche, town) if x) or "combined"
    default_name = f"combined_{date.today().isoformat()}_{label}"
    name = ask("Name for the combined sheet", default_name)
    batch = write_combined(
        batches_dir(), merged, name=name,
        source_names=[Path(f).name for f in chosen],
        niche_label=niche or "combined", area_label=town or "combined",
    )
    print(f"\n{len(merged)} businesses -> {batch.shortlist_path}")
    if ask_yes_no("Open it in your spreadsheet app?", True):
        open_in_default_app(batch.shortlist_path)


CONFIG_MENU = [
    ("1", "Check setup (keys, dependencies)", action_check),
    ("2", "Set up API keys (writes your .env file)", action_keys),
    ("3", "Test API keys (one small call each)", action_test_keys),
]


def action_config() -> None:
    """The configuration submenu — setup lives here, out of the daily flow."""
    while True:
        clear()
        print("=" * 60)
        print("  Setup & configuration")
        print("=" * 60)
        print()
        for key, label, _ in CONFIG_MENU:
            print(f"  {key}  {label}")
        print("  b  Back to the main menu")
        print()
        choice = ask("Choose", "b").lower()
        if choice in ("b", "back", "q"):
            return
        action = next((fn for key, _, fn in CONFIG_MENU if key == choice), None)
        if action is None:
            print("\nThat wasn't one of the options.")
            pause()
            continue
        try:
            action()
        except KeyboardInterrupt:
            print("\n\nStopped.")
        pause()


action_config.handles_own_pause = True

# Ordered as the work actually flows: find, cull, combine, open, tune.
MENU = [
    ("1", "Find prospects (one or several niches/towns)", action_find),
    ("2", "Review a shortlist (cull to your 10-15)", action_cull),
    ("3", "Combine batches into one sheet (by niche or town)", action_combine),
    ("4", "Open a shortlist in your spreadsheet app", action_open),
    ("5", "Tune the scoring from your decisions", action_tune),
    ("6", "Setup & configuration (keys, checks, tests)", action_config),
]


def main() -> int:
    while True:
        clear()
        print("=" * 60)
        print("  Postal Outreach Pipeline — prospect finder")
        print("=" * 60)
        print()
        for key, label, _ in MENU:
            print(f"  {key}  {label}")
        print("  q  Quit")
        print()
        choice = ask("Choose", "1").lower()
        if choice in ("q", "quit", "exit"):
            print("\nRight you are.\n")
            return 0
        action = next((fn for key, _, fn in MENU if key == choice), None)
        if action is None:
            print("\nThat wasn't one of the options.")
            pause()
            continue
        try:
            action()
        except KeyboardInterrupt:
            print("\n\nStopped.")
        if not getattr(action, "handles_own_pause", False):
            pause()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print()
        sys.exit(0)
