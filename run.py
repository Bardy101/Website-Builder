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
    else:
        niche = ask("Niche", "physiotherapist")
        area = ask("Town or area", "Hitchin, Hertfordshire")

    radius = ask("Radius in metres", "8000")
    top = ask("How many rows in the shortlist", "25")

    argv = ["--niche", niche, "--area", area, "--radius", radius,
            "--top", top, "--from-menu"]
    if demo:
        argv += ["--fixture", str(FIXTURE)]

    print("\n" + "-" * 60)
    import find

    try:
        code = find.main(argv)
    except SystemExit as exc:
        # find.py exits with a message for bad input; show it, don't crash out.
        print(exc.code if isinstance(exc.code, str) else "")
        return
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
        print("To search for real businesses: copy .env.example to .env and add a key.")

    folders = list_batches()
    print(f"\nBatches so far   {len(folders)}")
    for folder in folders[:5]:
        print(f"    {folder.name}")


MENU = [
    ("1", "Find prospects", action_find),
    ("2", "Review a shortlist (cull to your 10-15)", action_cull),
    ("3", "Tune the scoring from your decisions", action_tune),
    ("4", "Open a shortlist in your spreadsheet app", action_open),
    ("5", "Check setup (keys, dependencies)", action_check),
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
        pause()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print()
        sys.exit(0)
