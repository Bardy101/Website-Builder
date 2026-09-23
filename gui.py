#!/usr/bin/env python3
"""gui.py — the prospect finder as a window.

Double-click gui.bat (Windows) or gui.command (macOS), or run `python gui.py`.
Three tabs — Find prospects, Batches, Setup — and a log pane underneath that
shows exactly what the command-line tools print. Nothing here does anything
the CLIs can't: every button builds a command line and runs it, so the
scripts stay scriptable and this stays a thin skin over them.

The command builders at the top are plain functions with no window in them,
so they are unit-tested; the widgets only collect values and hand them over.
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

try:
    import run as menu  # the console menu: shared helpers and key metadata
    from pipeline.config import Config
    from pipeline.history import past_searches
    from pipeline import review as review_mod
    from pipeline.storage import Batch
except ImportError as exc:
    if "pipeline" not in str(exc):
        raise
    sys.exit(
        "\ngui.py could not find the 'pipeline' package.\n"
        "Run this from inside the project folder, with pipeline/ beside it.\n"
    )

APP_TITLE = "Postal Outreach — prospect finder"

# The design, in one place. Every colour the window uses comes from here, so
# a change of mind is one edit rather than a hunt. Soft tints carry state
# (kept, culled, warning) and the one accent marks what you can act on.
PALETTE = {
    "bg": "#f3f4f6",          # window
    "surface": "#ffffff",     # cards, tables, fields
    "subtle": "#eef1f5",      # table headings, hover
    "border": "#d6dbe1",
    "text": "#1d2127",
    "muted": "#5d6673",
    "accent": "#2b5b84",      # primary action, selection
    "accent_hover": "#1f4768",
    "accent_soft": "#e4edf6",
    "good": "#1a7f37",
    "good_soft": "#e6f4ea",
    "bad": "#b42318",
    "bad_soft": "#fbe9e8",
    "warn": "#8a5300",
    "warn_soft": "#fff3dc",
    "header": "#1f2d3d",      # the title strip
    "log_bg": "#1b1f24",
    "log_text": "#d6dae0",
    "log_err": "#ff8a80",
    "log_ok": "#7ee2a8",
}

TONES = {  # verdict tone -> (background, text) for the chip
    "good": ("good_soft", "good"),
    "ok": ("warn_soft", "warn"),
    "bad": ("bad_soft", "bad"),
    "muted": ("subtle", "muted"),
}


# -- command builders (pure: no window, no side effects) ---------------------

def find_command(
    pairs: list[tuple[str, str]],
    *,
    radius: int,
    top: int,
    screenshots: bool = True,
    site_checks: bool = True,
    owner_lookup: bool = True,
    site_contacts: bool = True,
    keep_dormant: bool = False,
    keep_fine: bool = False,
    dormant_days: Optional[int] = None,
    min_site_points: Optional[int] = None,
    refresh: bool = False,
    fixture: Optional[Path] = None,
    config_path: Optional[Path] = None,
) -> list[str]:
    """Arguments for find.py. Several pairs go through a batch-config file,
    which the caller writes (it owns the temp file's lifetime)."""
    if not pairs:
        raise ValueError("at least one niche/area pair is needed")
    if len(pairs) == 1:
        niche, area = pairs[0]
        argv = ["find.py", "--niche", niche, "--area", area]
    else:
        if config_path is None:
            raise ValueError("several pairs need a config_path")
        argv = ["find.py", "--batch-config", str(config_path)]
    argv += ["--radius", str(radius), "--top", str(top), "--from-menu"]
    if not screenshots:
        argv.append("--no-screenshots")
    if not site_checks:
        argv.append("--no-site-checks")
    if not owner_lookup:
        argv.append("--no-owner-lookup")
    if not site_contacts:
        argv.append("--no-site-contacts")
    if keep_dormant:
        argv.append("--keep-dormant")
    if keep_fine:
        argv.append("--keep-fine")
    if dormant_days:
        argv += ["--dormant-days", str(dormant_days)]
    if min_site_points is not None:
        argv += ["--min-site-points", str(min_site_points)]
    if refresh:
        argv.append("--refresh")
    if fixture is not None:
        argv += ["--fixture", str(fixture)]
    return argv


def contactsheet_command(batch: Path, *, approved_only: bool = False) -> list[str]:
    argv = ["contactsheet.py", "--batch", str(batch)]
    if approved_only:
        argv.append("--approved")
    return argv


def cull_import_command(batch: Path, decisions: Path, *, start_over: bool = False) -> list[str]:
    argv = ["cull.py", "--batch", str(batch), "--import", str(decisions)]
    if start_over:
        argv.append("--full")
    return argv


def cull_csv_command(batch: Path, csv_name: str, *, start_over: bool = False) -> list[str]:
    argv = ["cull.py", "--batch", str(batch), "--from-csv", csv_name]
    if start_over:
        argv.append("--full")
    return argv


def combine_command(
    batches: list[Path],
    *,
    niche: str = "",
    town: str = "",
    name: str = "",
    use_culls: bool = True,
) -> list[str]:
    if not batches:
        raise ValueError("nothing to combine")
    argv = ["combine.py"] + [str(b) for b in batches]
    if niche.strip():
        argv += ["--niche", niche.strip()]
    if town.strip():
        argv += ["--town", town.strip()]
    if name.strip():
        argv += ["--name", name.strip()]
    if not use_culls:
        argv.append("--full")
    return argv


def tune_command(batches: list[Path], *, apply: bool = False) -> list[str]:
    if not batches:
        raise ValueError("nothing to tune from")
    return ["tune.py", "--batch"] + [str(b) for b in batches] + (["--apply"] if apply else [])


def mockup_command(batch: Path, place_ids: Optional[list[str]] = None, *,
                   approved: bool = False) -> list[str]:
    """Webflow briefs for the given businesses, or every kept one."""
    argv = ["mockup.py", "--batch", str(batch)]
    if approved:
        return argv + ["--approved"]
    if not place_ids:
        raise ValueError("no business to brief")
    for place_id in place_ids:
        argv += ["--place", place_id]
    return argv


def check_setup_command() -> list[str]:
    return ["-c", "import run; run.action_check()"]


def test_keys_command() -> list[str]:
    return ["-c", "import run; run.action_test_keys()"]


def cost_note(
    pairs: list[tuple[str, str]], radius: int, history, *, refresh: bool
) -> tuple[bool, str]:
    """(free, message): will this run bill Google, as far as the cache knows?

    A pair is free when the exact niche, area and radius are cached — the
    wording matters, "Hitchin" and "Hitchin, Hertfordshire" are different
    searches. Anything else is a new billable search.
    """
    if refresh:
        return False, "Re-fetching everything: this run bills Google Places."
    cached = {(h.niche.lower(), h.area.lower(), h.radius_m) for h in history}
    new = [(n, a) for n, a in pairs if (n.lower(), a.lower(), radius) not in cached]
    if not pairs:
        return True, ""
    if not new:
        return True, "Places data served from the cache — nothing billed."
    if len(new) == len(pairs):
        return False, "New search: bills Google Places (searches + details)."
    return False, (f"{len(new)} of {len(pairs)} pairs are new searches and "
                   "will bill Google Places.")


def pairs_for_run(
    listed: list[tuple[str, str]], typed: tuple[str, str]
) -> tuple[list[tuple[str, str]], bool]:
    """(pairs to search, whether the typed pair is being left out).

    Once the run list has entries the typed fields only add to it — so a
    niche typed and never added would silently not be searched. The second
    value says that is about to happen, so the window can ask instead.
    """
    niche, area = (typed[0] or "").strip(), (typed[1] or "").strip()
    if not listed:
        return ([(niche, area)] if niche and area else []), False
    wanted = {(n.lower(), a.lower()) for n, a in listed}
    left_out = bool(niche and area) and (niche.lower(), area.lower()) not in wanted
    return list(listed), left_out


# Short forms, so a status fits its column: the full code is in the pane.
SHORT_REASONS = {"chain_or_franchise": "chain", "non_operational": "closed",
                 "no_owner_signal": "no owner", "winding_down": "winding down",
                 "wrong_niche": "wrong niche"}


def status_text(decision: str, reason: str, *, excluded: bool = False) -> str:
    """One column for the decision and its reason: '✓ kept', '✗ chain'."""
    raw = (reason or "").replace(" ", "_")
    reason = SHORT_REASONS.get(raw, raw).replace("_", " ")
    if excluded:
        return f"⊘ {reason}" if reason else "⊘ excluded"
    if decision == "keep":
        return "✓ kept"
    if decision == "cull":
        return f"✗ {reason}" if reason else "✗ culled"
    return ""


def log_tag(line: str) -> tuple:
    """Colour for a line of tool output: failures red, completion green."""
    text = line.strip()
    lowered = text.lower()
    if (text.startswith("Traceback") or "error:" in lowered or " failed" in lowered
            or text.startswith("[stopped]") or "exit code 1" in lowered
            or text.startswith("! ") or "could not" in lowered):
        return ("err",)
    if text == "[finished with exit code 0]" or lowered.startswith("saved ") \
            or ": saved " in lowered:
        return ("ok",)
    return ()


def batch_summary(folder: Path) -> dict:
    """Counts a human wants next to a batch name. Cheap: three small files."""
    batch = Batch(folder)
    meta = batch.read_meta()
    found = len(batch.read_shortlist())
    approved = (
        len(batch.read_shortlist(path=batch.approved_path))
        if batch.approved_path.is_file() else None
    )
    excluded = 0
    if batch.excluded_path.is_file():
        with batch.excluded_path.open(encoding="utf-8") as fh:
            excluded = max(sum(1 for _ in fh) - 1, 0)
    if meta.get("combined_from"):
        status = "combined"
    elif approved is not None:
        status = "culled"
    else:
        status = "found"
    return {
        "name": folder.name, "found": found, "approved": approved,
        "excluded": excluded, "status": status, "path": folder,
    }


# -- stopping a run, all of it -----------------------------------------------

def descendants(pid: int, table: list[tuple[int, int]]) -> list[int]:
    """Every process below ``pid`` in a (pid, parent_pid) table, deepest last.

    Walked from a snapshot taken before anything is killed: once a parent
    dies its children are re-parented and the lineage is gone.
    """
    children: dict[int, list[int]] = {}
    for child, parent in table:
        children.setdefault(parent, []).append(child)
    out, queue_ = [], [pid]
    while queue_:
        current = queue_.pop(0)
        for child in children.get(current, []):
            if child not in out and child != pid:
                out.append(child)
                queue_.append(child)
    return out


def _process_table() -> list[tuple[int, int]]:
    """(pid, ppid) for every process, via ps — present on macOS and Linux."""
    try:
        text = subprocess.run(["ps", "-A", "-o", "pid=", "-o", "ppid="],
                              capture_output=True, text=True, timeout=5).stdout
    except Exception:  # noqa: BLE001
        return []
    table = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            table.append((int(parts[0]), int(parts[1])))
    return table


def kill_tree(pid: int, *, grace: float = 3.0) -> None:
    """Stop a process and everything it started — including the browser.

    A plain terminate() missed Chromium on every platform, for different
    reasons. On Windows, ending a process never touches its children.
    Elsewhere, Playwright launches the browser detached (its own process
    group leader), so even a process-group kill misses it. So: Windows gets
    taskkill /T, which follows the parent lineage; macOS and Linux get the
    tree from ps, a polite SIGTERM, and SIGKILL for whatever is left.
    """
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)],
                       capture_output=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return

    import signal
    import time

    targets = [pid] + descendants(pid, _process_table())
    for target in targets:
        try:
            os.kill(target, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline and any(_alive(t) for t in targets):
        time.sleep(0.1)
    for target in targets:
        if _alive(target):
            try:
                os.kill(target, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass


def _alive(pid: int) -> bool:
    """Running, not merely a zombie awaiting its (possibly absent) reaper."""
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    try:
        with open(f"/proc/{pid}/stat", encoding="ascii") as fh:
            return fh.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return True   # no /proc (macOS): trust kill(0)


# -- subprocess runner --------------------------------------------------------

class Runner:
    """Runs one CLI at a time, streaming its output line by line.

    The subprocess is the same interpreter as the window, so whatever
    packages the window found, the tool finds too — "but I installed it"
    cannot happen between the two.
    """

    def __init__(self, on_line: Callable[[str], None],
                 on_done: Callable[[int], None]) -> None:
        self.on_line = on_line
        self.on_done = on_done
        self.lines: queue.Queue = queue.Queue()
        self.proc: Optional[subprocess.Popen] = None
        self.exit_code: Optional[int] = None

    @property
    def busy(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self, argv: list[str]) -> None:
        if self.busy:
            raise RuntimeError("something is already running")
        env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
        kwargs = {}
        if os.name == "nt":
            # No console window flashing up behind the GUI on Windows.
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.exit_code = None
        self.proc = subprocess.Popen(
            [sys.executable] + argv, cwd=str(HERE), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, text=True, encoding="utf-8",
            errors="replace", bufsize=1, **kwargs,
        )
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        assert self.proc is not None and self.proc.stdout is not None
        for line in self.proc.stdout:
            self.lines.put(line.rstrip("\n"))
        self.proc.wait()
        self.lines.put(("__done__", self.proc.returncode))

    def stop(self) -> None:
        """Stop the run and everything under it (see kill_tree)."""
        if self.busy and self.proc is not None:
            kill_tree(self.proc.pid)

    def drain(self) -> None:
        """Called from the Tk loop: move queued lines into the widget."""
        while True:
            try:
                item = self.lines.get_nowait()
            except queue.Empty:
                return
            if isinstance(item, tuple):
                self.exit_code = item[1]
                self.on_done(item[1])
            else:
                self.on_line(item)


# -- the window ---------------------------------------------------------------

def build_app():
    """Construct the Tk application. Imports tkinter here, not at module
    level, so the command builders above import cleanly anywhere."""
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    class App(tk.Tk):
        def __init__(self) -> None:
            super().__init__()
            self.title(APP_TITLE)
            self.geometry("1180x860")
            self.minsize(900, 600)
            self._apply_theme()
            self.config_obj = Config.from_env()
            self.runner = Runner(self._log_line, self._run_finished)
            self._after_run: Optional[Callable[[int], None]] = None
            self._pairs_config: Optional[Path] = None
            self._build()
            self.protocol("WM_DELETE_WINDOW", self.on_close)
            self.after(100, self._tick)
            self.refresh_history()
            self.refresh_batches()
            self.refresh_review_picker()
            self.load_review()
            self._log_line(f"Project folder: {HERE}")
            self._log_line(f"Python: {sys.executable}")
            if not self.config_obj.google_places_api_key:
                self._log_line("No Places key set — searches will use the demo data. "
                               "Add keys on the Setup tab.")

        # -- design ---------------------------------------------------------

        def _apply_theme(self) -> None:
            """One look on every platform.

            The native Windows theme ("vista") ignores most colour settings,
            so a designed look means a styleable base — "clam" — with the
            palette laid over it. The same code renders the same window on
            Windows, macOS and Linux; only the font family differs.
            """
            from tkinter import font as tkfont

            c = PALETTE
            style = ttk.Style(self)
            try:
                style.theme_use("clam")
            except tk.TclError:
                return

            base = tkfont.nametofont("TkDefaultFont")
            family = "Segoe UI" if os.name == "nt" else base.actual("family")
            base.configure(family=family, size=10)
            for name in ("TkTextFont", "TkMenuFont", "TkHeadingFont"):
                try:
                    tkfont.nametofont(name).configure(family=family, size=10)
                except tk.TclError:
                    pass
            self.fonts = {
                "base": (family, 10),
                "small": (family, 9),
                "bold": (family, 10, "bold"),
                "h2": (family, 11, "bold"),
                "title": (family, 16, "bold"),
                "brand": (family, 13, "bold"),
                "mono": ("Consolas" if os.name == "nt" else "DejaVu Sans Mono", 10),
            }
            self.configure(background=c["bg"])

            style.configure(".", background=c["bg"], foreground=c["text"],
                            bordercolor=c["border"], darkcolor=c["bg"],
                            lightcolor=c["bg"], troughcolor=c["subtle"],
                            focuscolor=c["accent"], font=self.fonts["base"])
            style.configure("TFrame", background=c["bg"])
            style.configure("Card.TFrame", background=c["surface"])
            style.configure("TLabel", background=c["bg"], foreground=c["text"])
            style.configure("Muted.TLabel", foreground=c["muted"])
            style.configure("Small.TLabel", foreground=c["muted"], font=self.fonts["small"])
            style.configure("H2.TLabel", font=self.fonts["h2"])
            for variant, extra in (("Card", {}),
                                   ("CardMuted", {"foreground": c["muted"]}),
                                   ("CardSmall", {"foreground": c["muted"],
                                                  "font": self.fonts["small"]}),
                                   ("CardBold", {"font": self.fonts["bold"]}),
                                   ("CardH2", {"font": self.fonts["h2"]}),
                                   ("CardTitle", {"font": self.fonts["title"]})):
                style.configure(f"{variant}.TLabel", background=c["surface"], **extra)

            # Buttons: quiet by default, one filled accent for the main action.
            style.configure("TButton", background=c["surface"], foreground=c["text"],
                            bordercolor=c["border"], lightcolor=c["surface"],
                            darkcolor=c["surface"], padding=(12, 5), relief="solid",
                            borderwidth=1)
            style.map("TButton",
                      background=[("disabled", c["bg"]), ("pressed", c["subtle"]),
                                  ("active", c["subtle"])],
                      foreground=[("disabled", c["muted"])],
                      bordercolor=[("focus", c["accent"])])
            for name, bg, fg, hover in (
                ("Accent", c["accent"], "#ffffff", c["accent_hover"]),
                ("Keep", c["good_soft"], c["good"], "#d3ecd9"),
                ("Cull", c["bad_soft"], c["bad"], "#f6d5d2"),
            ):
                style.configure(f"{name}.TButton", background=bg, foreground=fg,
                                bordercolor=bg, lightcolor=bg, darkcolor=bg,
                                font=self.fonts["bold"], padding=(14, 6))
                style.map(f"{name}.TButton",
                          background=[("disabled", "#b7c3cf"), ("pressed", hover),
                                      ("active", hover)],
                          foreground=[("disabled", "#ffffff")],
                          bordercolor=[("active", hover), ("focus", c["accent"])])

            style.configure("TEntry", fieldbackground=c["surface"], padding=5,
                            bordercolor=c["border"], lightcolor=c["surface"])
            style.map("TEntry", bordercolor=[("focus", c["accent"])],
                      lightcolor=[("focus", c["accent"])])
            style.configure("TCombobox", fieldbackground=c["surface"], padding=4,
                            background=c["surface"], arrowcolor=c["muted"],
                            bordercolor=c["border"])
            style.map("TCombobox", fieldbackground=[("readonly", c["surface"])],
                      bordercolor=[("focus", c["accent"])],
                      selectbackground=[("readonly", c["surface"])],
                      selectforeground=[("readonly", c["text"])])
            style.configure("TSpinbox", fieldbackground=c["surface"], padding=4,
                            arrowcolor=c["muted"], bordercolor=c["border"])
            style.configure("TCheckbutton", background=c["bg"], padding=2)
            style.map("TCheckbutton", background=[("active", c["bg"])],
                      indicatorcolor=[("selected", c["accent"]), ("!selected", c["surface"])])
            style.configure("Card.TCheckbutton", background=c["surface"])
            style.map("Card.TCheckbutton", background=[("active", c["surface"])])
            style.configure("TLabelframe", background=c["bg"], bordercolor=c["border"],
                            relief="solid", borderwidth=1, padding=10)
            style.configure("TLabelframe.Label", background=c["bg"],
                            foreground=c["text"], font=self.fonts["h2"])
            style.configure("TSeparator", background=c["border"])
            style.configure("TPanedwindow", background=c["bg"])
            style.configure("Sash", sashthickness=6, gripcount=0)

            style.configure("TNotebook", background=c["bg"], borderwidth=0,
                            tabmargins=(10, 8, 10, 0))
            style.configure("TNotebook.Tab", background=c["bg"], foreground=c["muted"],
                            padding=(18, 8), borderwidth=0, font=self.fonts["bold"])
            style.map("TNotebook.Tab",
                      expand=[("selected", (0, 0, 0, 0))],
                      background=[("selected", c["surface"]), ("active", c["subtle"])],
                      foreground=[("selected", c["accent"])],
                      lightcolor=[("selected", c["surface"])])

            # Tables: taller rows, flat headings, a selection you can't miss.
            style.configure("Treeview", background=c["surface"], foreground=c["text"],
                            fieldbackground=c["surface"], rowheight=28, borderwidth=0,
                            relief="flat")
            style.map("Treeview", background=[("selected", c["accent"])],
                      foreground=[("selected", "#ffffff")])
            # Normal weight on purpose: every table's column widths were set
            # for this font, and bold headings clipped "Found" to "Foun".
            style.configure("Treeview.Heading", background=c["subtle"],
                            foreground=c["muted"], relief="flat",
                            font=self.fonts["base"], padding=(4, 5))
            style.map("Treeview.Heading", background=[("active", c["border"])])
            style.configure("Vertical.TScrollbar", background=c["subtle"],
                            troughcolor=c["bg"], bordercolor=c["bg"],
                            arrowcolor=c["muted"], gripcount=0)
            style.configure("Horizontal.TScrollbar", background=c["subtle"],
                            troughcolor=c["bg"], bordercolor=c["bg"],
                            arrowcolor=c["muted"], gripcount=0)

        def chip(self, parent, text: str, tone: str = "muted") -> "tk.Label":
            """A small rounded-looking tag. Plain tk.Label: ttk won't take an
            arbitrary background per widget without a style per colour."""
            bg, fg = TONES.get(tone, TONES["muted"])
            return tk.Label(parent, text=text, bg=PALETTE[bg], fg=PALETTE[fg],
                            font=self.fonts["bold"], padx=8, pady=2)

        # -- layout ---------------------------------------------------------

        def _build(self) -> None:
            c = PALETTE
            # Title strip: says what this is and gives the eye an anchor.
            head = tk.Frame(self, bg=c["header"], height=46)
            head.pack(fill=tk.X)
            head.pack_propagate(False)
            tk.Label(head, text="Postal Outreach", bg=c["header"], fg="#ffffff",
                     font=self.fonts["brand"]).pack(side=tk.LEFT, padx=(18, 8))
            tk.Label(head, text="prospect finder", bg=c["header"], fg="#9fb0c3",
                     font=self.fonts["base"]).pack(side=tk.LEFT)
            self.head_status = tk.Label(head, text="", bg=c["header"], fg="#9fb0c3",
                                        font=self.fonts["small"])
            self.head_status.pack(side=tk.RIGHT, padx=18)

            outer = ttk.PanedWindow(self, orient=tk.VERTICAL)
            outer.pack(fill=tk.BOTH, expand=True)

            self.tabs = ttk.Notebook(outer)
            self.tab_find = ttk.Frame(self.tabs, padding=14)
            self.tab_review = ttk.Frame(self.tabs, padding=(14, 12, 14, 10))
            self.tab_batches = ttk.Frame(self.tabs, padding=14)
            self.tab_setup = ttk.Frame(self.tabs, padding=14)
            self.tabs.add(self.tab_find, text="Find prospects")
            self.tabs.add(self.tab_review, text="Shortlist")
            self.tabs.add(self.tab_batches, text="Batches")
            self.tabs.add(self.tab_setup, text="Setup")
            outer.add(self.tabs, weight=4)

            log_frame = ttk.Frame(outer, padding=(14, 6, 14, 10))
            outer.add(log_frame, weight=1)
            bar = ttk.Frame(log_frame)
            bar.pack(fill=tk.X)
            ttk.Label(bar, text="Output", style="H2.TLabel").pack(side=tk.LEFT)
            self.status = ttk.Label(bar, text="Ready", style="Muted.TLabel")
            self.status.pack(side=tk.LEFT, padx=12)
            self.btn_stop = ttk.Button(bar, text="Stop", command=self.stop_run,
                                       state=tk.DISABLED)
            self.btn_stop.pack(side=tk.RIGHT)
            ttk.Button(bar, text="Clear", command=self.clear_log).pack(
                side=tk.RIGHT, padx=(0, 6))
            self.log = tk.Text(log_frame, height=8, wrap=tk.NONE,
                               font=self.fonts["mono"], relief="flat",
                               borderwidth=0, padx=10, pady=8,
                               state=tk.DISABLED, background=c["log_bg"],
                               foreground=c["log_text"], insertbackground=c["log_text"])
            # Failures and successes in colour, so they don't scroll past as
            # just more grey text.
            self.log.tag_configure("err", foreground=c["log_err"])
            self.log.tag_configure("ok", foreground=c["log_ok"])
            scroll = ttk.Scrollbar(log_frame, command=self.log.yview)
            self.log.configure(yscrollcommand=scroll.set)
            self.log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, pady=(4, 0))
            scroll.pack(side=tk.RIGHT, fill=tk.Y, pady=(4, 0))

            self._build_find()
            self._build_review()
            self._build_batches()
            self._build_setup()

            # Weights only govern resizing, so the first layout still splits
            # evenly and the log eats space the controls need. Place the sash
            # once the window knows its real height.
            def place_sash() -> None:
                height = outer.winfo_height()
                if height > 1:
                    outer.sashpos(0, int(height * 0.79))

            self.after_idle(place_sash)

        def _build_find(self) -> None:
            f = self.tab_find
            # The left column holds the two lists and takes the slack; the
            # right is checkboxes at their natural width, which must not be
            # squeezed — a half-read option is worse than no option.
            f.columnconfigure(0, weight=1, minsize=430)
            f.columnconfigure(1, weight=0, minsize=430)
            f.rowconfigure(1, weight=1)

            # Left: what to search ------------------------------------------
            left = ttk.LabelFrame(f, text="What to search", padding=10)
            left.grid(row=0, column=0, rowspan=2, sticky="nsew", padx=(0, 8))
            left.columnconfigure(1, weight=1)

            ttk.Label(left, text="Niche").grid(row=0, column=0, sticky="w", pady=3)
            self.niche = ttk.Combobox(left, values=menu.SUGGESTED)
            self.niche.set("physiotherapist")
            self.niche.grid(row=0, column=1, sticky="ew", pady=3)
            ttk.Label(left, text="the word a customer would search:\n"
                      "'plumber', not 'plumbing services'",
                      foreground="#666", justify="left").grid(
                row=1, column=1, sticky="w")

            ttk.Label(left, text="Town or area").grid(row=2, column=0, sticky="w", pady=3)
            self.area = ttk.Entry(left)
            self.area.insert(0, "Hitchin, Hertfordshire")
            self.area.grid(row=2, column=1, sticky="ew", pady=3)

            ttk.Label(left, text="Radius (m)").grid(row=3, column=0, sticky="w", pady=3)
            self.radius = ttk.Spinbox(left, from_=1000, to=40000, increment=1000, width=8)
            self.radius.set(8000)
            self.radius.grid(row=3, column=1, sticky="w", pady=3)

            ttk.Label(left, text="Rows in shortlist").grid(row=4, column=0, sticky="w", pady=3)
            self.top = ttk.Spinbox(left, from_=5, to=200, increment=5, width=8)
            self.top.set(25)
            self.top.grid(row=4, column=1, sticky="w", pady=3)

            ttk.Label(left, text="several niches/towns merge into one sheet",
                      foreground="#666").grid(row=5, column=0, columnspan=2,
                                              sticky="w", pady=(12, 2))
            pair_bar = ttk.Frame(left)
            pair_bar.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(0, 2))
            ttk.Button(pair_bar, text="Add to this run", command=self.add_pair).pack(side=tk.LEFT)
            ttk.Button(pair_bar, text="Remove", command=self.remove_pair).pack(side=tk.LEFT, padx=6)
            self.pairs = tk.Listbox(left, height=3, exportselection=False,
                                    relief="flat", borderwidth=0, highlightthickness=1,
                                    highlightbackground=PALETTE["border"],
                                    highlightcolor=PALETTE["accent"],
                                    background=PALETTE["surface"],
                                    selectbackground=PALETTE["accent"])
            self.pairs.grid(row=7, column=0, columnspan=2, sticky="ew")

            ttk.Label(left, text="Searches you have already paid for — click one to reuse it "
                      "(free to repeat)").grid(row=8, column=0, columnspan=2, sticky="w", pady=(12, 2))
            cols = ("niche", "area", "radius", "age", "results")
            self.history_tree = ttk.Treeview(left, columns=cols, show="headings", height=7)
            for col, title, width in (("niche", "Niche", 120), ("area", "Town / area", 170),
                                      ("radius", "Radius", 64), ("age", "Fetched", 78),
                                      ("results", "Found", 58)):
                self.history_tree.heading(col, text=title)
                self.history_tree.column(col, width=width, minwidth=width,
                                         anchor="w", stretch=col == "area")
            self.history_tree.column("area", minwidth=110)
            self.history_tree.grid(row=9, column=0, columnspan=2, sticky="nsew")
            left.rowconfigure(9, weight=1)
            self.history_tree.bind("<<TreeviewSelect>>", self.use_history)
            ttk.Button(left, text="Refresh list", command=self.refresh_history).grid(
                row=10, column=0, sticky="w", pady=(4, 0))

            # Right: how to search --------------------------------------------
            right = ttk.LabelFrame(f, text="Options", padding=10)
            right.grid(row=0, column=1, sticky="nsew")

            self.opt_screens = tk.BooleanVar(value=True)
            self.opt_site = tk.BooleanVar(value=True)
            self.opt_owner = tk.BooleanVar(value=True)
            self.opt_contacts = tk.BooleanVar(value=True)
            for r, (var, text, note) in enumerate((
                (self.opt_screens, "Capture screenshots",
                 "needs Chromium; adds a couple of minutes"),
                (self.opt_site, "Check each website",
                 "HTTPS, mobile score, staleness signals"),
                (self.opt_owner, "Look up owners at Companies House", ""),
                (self.opt_contacts, "Read each site's About page", "for a named contact"),
            )):
                row = ttk.Frame(right)
                row.grid(row=r, column=0, sticky="w", pady=1)
                ttk.Checkbutton(row, text=text, variable=var).pack(side=tk.LEFT)
                if note:
                    ttk.Label(row, text=note, foreground="#666").pack(side=tk.LEFT, padx=(4, 0))

            ttk.Separator(right).grid(row=4, column=0, sticky="new", pady=(8, 0))
            ttk.Label(right, text="Selection rules", font=("", 9, "bold")).grid(
                row=4, column=0, sticky="w", pady=(8, 0))
            self.opt_keep_dormant = tk.BooleanVar(value=False)
            self.opt_keep_fine = tk.BooleanVar(value=False)
            ttk.Label(right, text="Both are excluded by default — tick to put them back.",
                      foreground="#666").grid(row=5, column=0, sticky="w", pady=(2, 4))
            ttk.Checkbutton(right, text="Keep dormant listings",
                            variable=self.opt_keep_dormant).grid(row=6, column=0, sticky="w", pady=1)
            ttk.Checkbutton(right, text="Keep sites with little wrong ('fine')",
                            variable=self.opt_keep_fine).grid(row=7, column=0, sticky="w", pady=1)
            dorm = ttk.Frame(right)
            dorm.grid(row=8, column=0, sticky="w", pady=(2, 0))
            ttk.Label(dorm, text="Dormant after").pack(side=tk.LEFT)
            self.dormant_days = ttk.Spinbox(dorm, from_=30, to=1500, increment=30, width=6)
            self.dormant_days.set(int(self._weights_threshold("dormant_after_days", 365)))
            self.dormant_days.pack(side=tk.LEFT, padx=4)
            ttk.Label(dorm, text="days without a review").pack(side=tk.LEFT)
            bar = ttk.Frame(right)
            bar.grid(row=9, column=0, sticky="w", pady=(4, 0))
            ttk.Label(bar, text="A site needs").pack(side=tk.LEFT)
            self.min_points = ttk.Spinbox(bar, from_=0, to=100, increment=5, width=5)
            self.min_points.set(int(self._weights_threshold("min_site_points", 25)))
            self.min_points.pack(side=tk.LEFT, padx=4)
            ttk.Label(bar, text="points of problems").pack(side=tk.LEFT)
            ttk.Label(right, text="25 = one strong sign (not built for phones) or two weak "
                      "ones.\nA stale footer year alone is 15, so it isn't enough.",
                      style="Small.TLabel", justify="left").grid(
                row=10, column=0, sticky="w", padx=(2, 0))

            ttk.Separator(right).grid(row=11, column=0, sticky="ew", pady=8)
            self.opt_refresh = tk.BooleanVar(value=False)
            ttk.Checkbutton(right, text="Ignore the 30-day cache and re-fetch",
                            variable=self.opt_refresh, command=self.update_cost).grid(
                row=12, column=0, sticky="w")
            ttk.Label(right, text="costs real API calls for data you already have",
                      foreground="#666").grid(row=13, column=0, sticky="w", padx=(20, 0))

            # Run --------------------------------------------------------------
            go = ttk.Frame(f, padding=(0, 10, 0, 0))
            go.grid(row=1, column=1, sticky="sew")
            self.cost = ttk.Label(go, text="", foreground="#1a7f37", wraplength=380)
            self.cost.pack(anchor="w", pady=(0, 6))
            self.btn_find = ttk.Button(go, text="Find prospects", style="Accent.TButton",
                                       command=self.run_find)
            self.btn_find.pack(anchor="e", ipadx=12, ipady=4)
            for w in (self.niche, self.area, self.radius):
                w.bind("<KeyRelease>", lambda _e: self.update_cost())
                w.bind("<<ComboboxSelected>>", lambda _e: self.update_cost())

        # -- shortlist tab ----------------------------------------------------
        #
        # The list on the left, the business on the right. Everything the
        # spreadsheet used to be opened for — contact details, who to write
        # to, the site evidence, why it scored what it did — is in the pane,
        # with the screenshots, so reviewing a batch never leaves the window.

        # What the table shows, and how wide. The pane holds the rest, so the
        # table stays narrow enough never to need scrolling sideways.
        REVIEW_COLUMNS = [
            ("decision", "Status", 118, "w"),
            ("lead_score", "Score", 62, "e"),
            ("name", "Business", 170, "w"),
            ("town", "Town", 92, "w"),
            ("site_verdict", "Website", 132, "w"),
            ("review_count", "Reviews", 70, "e"),
        ]
        MARKS = {"keep": "✓", "cull": "✗", "": "·"}
        FILTERS = ["Everything", "Not yet decided", "Keeps", "Culls",
                   "Excluded by the rules"]

        def _build_review(self) -> None:
            from pipeline.cull import REASON_CODES

            c = PALETTE
            self.reason_codes = list(REASON_CODES)
            self.sheet = None
            self.review_batch: Optional[Path] = None
            self.review_dirty = False
            self.excluded_rows: list = []
            self._records: dict[str, dict] = {}
            self._thumbs: dict[str, tuple] = {}
            self._detail_row = None
            self._filling_detail = False

            f = self.tab_review
            f.columnconfigure(0, weight=1)
            f.rowconfigure(2, weight=1)

            # Row 0: which batch, where it stands, and the one button that
            # matters most.
            top = ttk.Frame(f)
            top.grid(row=0, column=0, sticky="ew")
            ttk.Label(top, text="Batch", style="H2.TLabel").pack(side=tk.LEFT)
            self.review_pick = ttk.Combobox(top, state="readonly", width=36)
            self.review_pick.pack(side=tk.LEFT, padx=(8, 8))
            self.review_pick.bind("<<ComboboxSelected>>", lambda _e: self.load_review())
            ttk.Button(top, text="Reload", command=self.reload_review).pack(side=tk.LEFT)
            self.review_counts = tk.Frame(top, bg=c["bg"])
            self.review_counts.pack(side=tk.LEFT, padx=14)
            self.review_save = ttk.Button(top, text="Save decisions",
                                          style="Accent.TButton",
                                          command=self.save_review)
            self.review_save.pack(side=tk.RIGHT)

            # Row 1: narrowing the list down.
            bar = ttk.Frame(f)
            bar.grid(row=1, column=0, sticky="ew", pady=(10, 8))
            ttk.Label(bar, text="Show").pack(side=tk.LEFT)
            self.review_filter = ttk.Combobox(bar, state="readonly", width=20,
                                              values=self.FILTERS)
            self.review_filter.set("Everything")
            self.review_filter.pack(side=tk.LEFT, padx=(6, 14))
            self.review_filter.bind("<<ComboboxSelected>>", lambda _e: self.fill_review())
            ttk.Label(bar, text="Find").pack(side=tk.LEFT)
            self.review_search = ttk.Entry(bar, width=24)
            self.review_search.pack(side=tk.LEFT, padx=6)
            self.review_search.bind("<KeyRelease>", lambda _e: self.fill_review())
            ttk.Label(bar, text="name, town or notes", style="Small.TLabel").pack(side=tk.LEFT)
            # The spreadsheet is optional now; its button sits out of the way.
            ttk.Button(bar, text="Open as CSV", command=self.open_review_csv).pack(
                side=tk.RIGHT)
            self.review_dirty_label = ttk.Label(bar, text="", foreground=c["warn"],
                                                font=self.fonts["bold"])
            self.review_dirty_label.pack(side=tk.RIGHT, padx=12)

            # Row 2: list | business, side by side and resizable.
            split = ttk.PanedWindow(f, orient=tk.HORIZONTAL)
            split.grid(row=2, column=0, sticky="nsew")

            left = ttk.Frame(split)
            left.columnconfigure(0, weight=1)
            left.rowconfigure(0, weight=1)
            cols = [col[0] for col in self.REVIEW_COLUMNS]
            self.review_tree = ttk.Treeview(left, columns=cols, show="headings",
                                            selectmode="extended")
            for key, title, width, anchor in self.REVIEW_COLUMNS:
                self.review_tree.heading(
                    key, text=title, command=lambda k=key: self.sort_review(k))
                self.review_tree.column(key, width=width, minwidth=width,
                                        anchor=anchor, stretch=key == "name")
            self.review_tree.column("name", minwidth=130)
            self.review_tree.grid(row=0, column=0, sticky="nsew")
            vsb = ttk.Scrollbar(left, command=self.review_tree.yview)
            self.review_tree.configure(yscrollcommand=vsb.set)
            vsb.grid(row=0, column=1, sticky="ns")

            # Kept rows read as settled, culled as struck out, at a glance.
            self.review_tree.tag_configure("keep", background=c["good_soft"])
            self.review_tree.tag_configure("cull", background="#f4f4f5",
                                           foreground="#9aa1ab")
            self.review_tree.tag_configure("todo", background=c["surface"])
            self.review_tree.tag_configure("excluded", background="#fafafa",
                                           foreground=c["muted"])

            self.review_tree.bind("<<TreeviewSelect>>", lambda _e: self.show_detail())
            self.review_tree.bind("<Double-1>", lambda _e: self._focus_letter_to())
            self.review_tree.bind("<Return>", lambda _e: self.mark(review_mod.KEEP))
            for key, decision in (("k", review_mod.KEEP), ("c", review_mod.CULL),
                                  ("u", review_mod.UNDECIDED)):
                self.review_tree.bind(key, lambda _e, d=decision: self.mark(d))

            # Under the list: act on the selection (works on several rows).
            act = ttk.Frame(left)
            act.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 0))
            self.act_bar = act
            ttk.Button(act, text="✓  Keep", style="Keep.TButton",
                       command=lambda: self.mark(review_mod.KEEP)).pack(side=tk.LEFT)
            ttk.Button(act, text="✗  Cull", style="Cull.TButton",
                       command=lambda: self.mark(review_mod.CULL)).pack(side=tk.LEFT, padx=6)
            ttk.Label(act, text="as").pack(side=tk.LEFT, padx=(2, 4))
            self.review_reason = ttk.Combobox(act, state="readonly", width=16,
                                              values=self.reason_codes)
            self.review_reason.set(self.reason_codes[0])
            self.review_reason.pack(side=tk.LEFT)
            self.review_reason.bind("<<ComboboxSelected>>", lambda _e: self.apply_reason())
            ttk.Button(act, text="Undecide", command=lambda: self.mark(
                review_mod.UNDECIDED)).pack(side=tk.LEFT, padx=6)
            self.review_hint = ttk.Label(left, text="", style="Small.TLabel",
                                         wraplength=560, justify="left")
            self.review_hint.grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))
            split.add(left, weight=3)

            # The business pane.
            holder, self.detail = self._scrollable(split, width=440, bg=c["surface"])
            self.detail_holder = holder
            split.add(holder, weight=2)
            self._pane_last_width = 0

            def pane_resized(event) -> None:
                if abs(event.width - self._pane_last_width) < 24:
                    return
                self._pane_last_width = event.width
                if getattr(self, "_pane_redraw", None):
                    self.after_cancel(self._pane_redraw)
                self._pane_redraw = self.after(180, self.show_detail)

            holder.bind("<Configure>", pane_resized, add="+")
            self._build_detail_placeholder()

            def place_split() -> None:
                width = split.winfo_width()
                if width > 1:
                    split.sashpos(0, int(width * 0.56))

            self.after_idle(place_split)
            self.bind_all("<Control-s>", lambda _e: self.save_review())

        # -- shortlist: the business pane --------------------------------------

        def _clear_detail(self) -> None:
            for child in self.detail.winfo_children():
                child.destroy()

        def _build_detail_placeholder(self, text: str = "") -> None:
            self._clear_detail()
            self._detail_row = None
            msg = text or ("Pick a business on the left to see it here — its "
                           "website, who to write to, and why it scored what it did.")
            ttk.Label(self.detail, text=msg, style="CardMuted.TLabel",
                      wraplength=360, justify="left").pack(anchor="w", padx=20, pady=24)

        def _record(self, place_id: str) -> dict:
            """business.json for a row, read once per load."""
            if place_id not in self._records and self.review_batch is not None:
                try:
                    self._records[place_id] = Batch(self.review_batch).read_business(place_id) or {}
                except Exception:  # noqa: BLE001 — a bad record must not blank the pane
                    self._records[place_id] = {}
            return self._records.get(place_id, {})

        def _current_row(self):
            focus = self.review_tree.focus() or next(iter(self.review_tree.selection()), "")
            if not focus:
                return None
            if self.sheet is not None:
                row = self.sheet.by_id(focus)
                if row is not None:
                    return row
            return next((r for r in self.excluded_rows if r.place_id == focus), None)

        def show_detail(self) -> None:
            """Draw the business pane for the focused row."""
            self._commit_detail()
            row = self._current_row()
            if row is None:
                self._build_detail_placeholder()
                return
            from pipeline import details as details_mod

            excluded = getattr(row, "excluded", None)
            record = {} if excluded else self._record(row.place_id)
            info = details_mod.build(row.row, record, excluded=excluded)
            self._draw_detail(row, info, editable=excluded is None)

        def _draw_detail(self, row, info, *, editable: bool) -> None:
            c = PALETTE
            self._clear_detail()
            self.detail_holder.scroll_top()
            self._detail_row = row
            pad = {"padx": 20}
            d = self.detail

            # Header: who, where, and the three things you judge first.
            ttk.Label(d, text=info.name, style="CardTitle.TLabel",
                      wraplength=self._pane_width(), justify="left").pack(anchor="w", pady=(18, 0), **pad)
            if info.subtitle:
                ttk.Label(d, text=info.subtitle, style="CardMuted.TLabel").pack(
                    anchor="w", **pad)
            chips = tk.Frame(d, bg=c["surface"])
            chips.pack(anchor="w", pady=(10, 0), **pad)
            if info.score:
                self.chip(chips, f"Score {info.score}", "muted").pack(side=tk.LEFT)
            self.chip(chips, info.verdict_label, info.verdict_tone).pack(side=tk.LEFT, padx=6)
            if editable:
                label = {"keep": ("Kept", "good"), "cull": (
                    f"Culled — {row.reason.replace('_', ' ')}" if row.reason else "Culled", "bad")
                         }.get(row.decision, ("Not decided", "ok"))
                self.chip(chips, label[0], label[1]).pack(side=tk.LEFT)

            for warning in info.warnings:
                box = tk.Label(d, text=warning, bg=c["warn_soft"], fg=c["warn"],
                               font=self.fonts["small"], wraplength=self._pane_width() - 24,
                               justify="left",
                               anchor="w", padx=10, pady=6)
                box.pack(fill=tk.X, pady=(10, 0), **pad)

            # The website, as a visitor sees it.
            self._draw_screens(d, row, info)

            links = tk.Frame(d, bg=c["surface"])
            links.pack(anchor="w", pady=(8, 0), **pad)
            if info.website:
                ttk.Button(links, text="Open website",
                           command=lambda: self._open_url(info.website)).pack(side=tk.LEFT)
            if info.maps_url:
                ttk.Button(links, text="Google Maps",
                           command=lambda: self._open_url(info.maps_url)).pack(side=tk.LEFT, padx=6)
            if editable:
                ttk.Button(links, text="Webflow brief", style="Accent.TButton",
                           command=lambda: self.webflow_brief(row)).pack(side=tk.LEFT)

            if editable:
                # Decide on this one, right here.
                decide = tk.Frame(d, bg=c["surface"])
                decide.pack(anchor="w", pady=(16, 0), **pad)
                ttk.Button(decide, text="✓  Keep", style="Keep.TButton",
                           command=lambda: self.mark(review_mod.KEEP, rows=[row])).pack(side=tk.LEFT)
                ttk.Button(decide, text="✗  Cull", style="Cull.TButton",
                           command=lambda: self.mark(review_mod.CULL, rows=[row])).pack(
                    side=tk.LEFT, padx=6)
                ttk.Label(decide, text=f"as “{self.review_reason.get().replace('_', ' ')}”",
                          style="CardSmall.TLabel").pack(side=tk.LEFT, padx=4)

                # Yours to edit: the two fields that aren't measurements.
                ttk.Label(d, text="Letter to", style="CardBold.TLabel").pack(
                    anchor="w", pady=(16, 2), **pad)
                self.detail_addr = tk.StringVar(value=row.get("address_to"))
                self.detail_addr_entry = ttk.Entry(d, textvariable=self.detail_addr)
                self.detail_addr_entry.pack(fill=tk.X, **pad)
                self.detail_addr.trace_add("write", lambda *_: self._detail_changed())
                ttk.Label(d, text="Notes", style="CardBold.TLabel").pack(
                    anchor="w", pady=(10, 2), **pad)
                self.detail_notes = tk.Text(
                    d, height=4, wrap=tk.WORD, relief="flat", borderwidth=0,
                    highlightthickness=1, highlightcolor=c["accent"],
                    highlightbackground=c["border"], font=self.fonts["base"],
                    background=c["surface"], padx=8, pady=6)
                self.detail_notes.insert("1.0", row.notes)
                self.detail_notes.pack(fill=tk.X, **pad)
                self.detail_notes.bind("<KeyRelease>", lambda _e: self._detail_changed())
                self.detail_notes.bind("<FocusOut>", lambda _e: self._detail_changed())

            # Why this score, itemised.
            if info.breakdown:
                ttk.Label(d, text="Why it scored " + (info.score or ""),
                          style="CardH2.TLabel").pack(anchor="w", pady=(20, 4), **pad)
                grid = tk.Frame(d, bg=c["surface"])
                grid.pack(fill=tk.X, **pad)
                grid.columnconfigure(0, weight=1)
                for i, (label, points) in enumerate(info.breakdown):
                    ttk.Label(grid, text=label, style="Card.TLabel").grid(
                        row=i, column=0, sticky="w", pady=1)
                    tk.Label(grid, text=f"{points:+d}", bg=c["surface"],
                             fg=c["good"] if points > 0 else c["bad"],
                             font=self.fonts["bold"]).grid(row=i, column=1, sticky="e")

            # The rest, as labelled sections.
            for heading, rows in info.sections:
                ttk.Label(d, text=heading, style="CardH2.TLabel").pack(
                    anchor="w", pady=(18, 4), **pad)
                grid = tk.Frame(d, bg=c["surface"])
                grid.pack(fill=tk.X, **pad)
                grid.columnconfigure(1, weight=1)
                for i, (label, value) in enumerate(rows):
                    if label:
                        ttk.Label(grid, text=label, style="CardMuted.TLabel").grid(
                            row=i, column=0, sticky="nw", padx=(0, 14), pady=1)
                    ttk.Label(grid, text=value, style="Card.TLabel",
                              wraplength=max(160, self._pane_width() - 140),
                              justify="left").grid(row=i, column=1 if label else 0,
                                                   columnspan=1 if label else 2,
                                                   sticky="w", pady=1)
                if heading == "Reviews" and info.quotes:
                    for quote in info.quotes:
                        ttk.Label(d, text=f"“{quote}”", style="CardMuted.TLabel",
                                  wraplength=self._pane_width(), justify="left",
                                  font=(self.fonts["base"][0], 10, "italic")).pack(
                            anchor="w", pady=(6, 0), **pad)
            tk.Frame(d, bg=c["surface"], height=24).pack()

        def _draw_screens(self, parent, row, info) -> None:
            """Desktop and phone screenshots side by side, click to enlarge."""
            c = PALETTE
            frame = tk.Frame(parent, bg=c["surface"])
            frame.pack(anchor="w", padx=20, pady=(14, 0))
            desktop, mobile = self._screenshot_paths(row)
            if not (desktop or mobile):
                why = ("No website to show." if info.verdict in ("none",) else
                       "Only a social or directory page — nothing of theirs to show."
                       if info.verdict == "social_only" else
                       "No screenshot yet — capture was skipped or failed for this site.")
                tk.Label(frame, text=why, bg=c["subtle"], fg=c["muted"], height=6,
                         font=self.fonts["small"], wraplength=self._pane_width() - 40,
                         width=max(20, self._pane_width() // 8)).pack()
                return
            thumbs = self._thumbnails(row.place_id, desktop, mobile, self._pane_width())
            for path, image in thumbs:
                if image is None:
                    continue
                label = tk.Label(frame, image=image, bg=c["border"], bd=0, padx=1, pady=1,
                                 cursor="hand2")
                label.pack(side=tk.LEFT, padx=(0, 8), anchor="n")
                label.bind("<Button-1>", lambda _e, p=path: menu.open_in_default_app(p))
            ttk.Label(parent, text="Desktop · phone — click to enlarge",
                      style="CardSmall.TLabel").pack(anchor="w", padx=20, pady=(4, 0))

        def _screenshot_paths(self, row) -> tuple:
            """The cached pair for a business, from the cache by Place ID.

            Looked up in the cache rather than trusted from the row's
            screenshot_path, which is an absolute path from whichever
            machine and folder ran the find.
            """
            from pipeline.screenshots import ScreenshotCapturer

            desktop, mobile = ScreenshotCapturer(cache_dir=menu.cache_dir()).paths_for(
                row.place_id)
            if not mobile.is_file():
                recorded = Path(row.get("screenshot_path") or "")
                mobile = recorded if recorded.name and recorded.is_file() else mobile
            return (desktop if desktop.is_file() else None,
                    mobile if mobile.is_file() else None)

        def _pane_width(self) -> int:
            """Usable width inside the business pane, for wrapping and images."""
            width = self.detail.winfo_width()
            return max(280, (width if width > 50 else 440) - 44)

        def _thumbnails(self, place_id: str, desktop, mobile, width: int = 396) -> list:
            """(path, PhotoImage) for each shot, kept referenced, cached per row.

            Pillow when present (it ships with the requirements and scales
            smoothly); Tk's own PNG loader otherwise, which can only shrink by
            whole factors but needs nothing installed.
            """
            # One height for both, chosen so desktop (16:10) + phone (~9:19.5)
            # + the gap fill the pane's width exactly.
            height = int(min(210, (width - 8) / (1440 / 900 + 780 / 1688)))
            key = (place_id, height)
            if key in self._thumbs:
                return self._thumbs[key]
            out = []
            for path, box in ((desktop, (int(height * 1440 / 900), height)),
                              (mobile, (int(height * 780 / 1688) + 1, height))):
                if path is None:
                    continue
                image = None
                try:
                    from PIL import Image, ImageTk

                    with Image.open(path) as im:
                        im.thumbnail(box)
                        image = ImageTk.PhotoImage(im.copy())
                except ImportError:
                    try:
                        full = tk.PhotoImage(file=str(path))
                        factor = max(1, -(-full.width() // box[0]),
                                     -(-full.height() // box[1]))
                        image = full.subsample(factor, factor)
                    except tk.TclError:
                        image = None
                except Exception:  # noqa: BLE001 — a broken file shows as missing
                    image = None
                out.append((path, image))
            if len(self._thumbs) > 60:          # bounded: a batch, not a gallery
                self._thumbs.clear()
            self._thumbs[key] = out
            return out

        def _open_url(self, url: str) -> None:
            import webbrowser

            webbrowser.open(url if "://" in url else f"https://{url}")

        def _focus_letter_to(self) -> None:
            entry = getattr(self, "detail_addr_entry", None)
            if entry is not None and entry.winfo_exists():
                entry.focus_set()
                entry.select_range(0, tk.END)

        def _detail_changed(self) -> None:
            """Typing in the pane writes straight to the row."""
            if self._filling_detail or self._detail_row is None:
                return
            row = self._detail_row
            if getattr(row, "excluded", None) is not None:
                return
            changed = False
            addr_entry = getattr(self, "detail_addr_entry", None)
            if addr_entry is not None and addr_entry.winfo_exists():
                value = self.detail_addr.get().strip()
                if value != row.get("address_to"):
                    row.set("address_to", value)
                    changed = True
            notes = getattr(self, "detail_notes", None)
            if notes is not None and notes.winfo_exists():
                value = notes.get("1.0", "end-1c").rstrip()
                if value != row.notes:
                    row.set("notes", value)
                    changed = True
            if changed:
                self.set_review_dirty(True)
                self._paint_row(row)
                self.update_review_counts()

        def _commit_detail(self) -> None:
            """Take whatever is in the pane's fields before it is redrawn."""
            if self._detail_row is not None:
                self._detail_changed()

        # -- shortlist: data --------------------------------------------------

        def refresh_review_picker(self) -> None:
            self.review_folders = menu.list_batches()
            names = [f.name for f in self.review_folders]
            self.review_pick.configure(values=names)
            if names and not self.review_pick.get():
                self.review_pick.set(names[0])

        def _selected_review_folder(self) -> Optional[Path]:
            name = self.review_pick.get()
            return next((f for f in getattr(self, "review_folders", [])
                         if f.name == name), None)

        def _confirm_discard(self, doing: str) -> bool:
            """True if there is nothing unsaved, or the operator says discard.

            Every path that replaces the sheet comes through here. Before this
            existed, three of them (Reload, a Find finishing, an import) threw
            unsaved decisions away without a word.
            """
            if not self.review_dirty:
                return True
            name = self.review_batch.name if self.review_batch else "this batch"
            return messagebox.askokcancel(
                APP_TITLE,
                f"You have unsaved decisions on {name}.\n\n{doing} will discard "
                "them. Continue?\n\n(Cancel, then Save decisions, to keep them.)",
                icon="warning")

        def load_review(self, force: bool = False) -> None:
            """Load the batch selected in the picker.

            ``force`` means the caller has already dealt with unsaved changes
            (confirmed, saved, or there were none) — never "discard silently".
            """
            folder = self._selected_review_folder()
            if folder is None:
                return
            if not force and not self._confirm_discard("Opening another batch"):
                # Put the picker back where it was.
                if self.review_batch is not None:
                    self.review_pick.set(self.review_batch.name)
                return
            self._detail_row = None
            try:
                self.sheet = review_mod.load(Batch(folder))
            except Exception as exc:  # noqa: BLE001
                messagebox.showerror(APP_TITLE, f"Could not read that batch:\n{exc}")
                return
            self.review_batch = folder
            self._records.clear()
            self._thumbs.clear()
            self.excluded_rows = self._load_excluded(folder)
            self.set_review_dirty(False)
            self.fill_review()
            if not len(self.sheet):
                self._log_line(f"{folder.name}: shortlist.csv is empty.")

        def _load_excluded(self, folder: Path) -> list:
            """excluded.csv as read-only rows, so the rules' drops can be seen
            in the window instead of in a spreadsheet."""
            from pipeline.storage import read_csv_rows

            path = Batch(folder).excluded_path
            if not path.is_file():
                return []
            out = []
            try:
                for raw in read_csv_rows(path):
                    row = review_mod.ReviewRow(row={
                        "place_id": raw.get("place_id") or f"excluded-{len(out)}",
                        "name": raw.get("name") or "",
                        "town": raw.get("town") or "",
                        "site_verdict": raw.get("site_verdict") or "",
                        "review_count": raw.get("review_count") or "",
                        "website_url": raw.get("website") or "",
                    }, reason=raw.get("reason") or "")
                    row.excluded = raw
                    out.append(row)
            except Exception:  # noqa: BLE001 — a bad file hides the view, not the batch
                return []
            return out

        def reload_review(self) -> None:
            if self._confirm_discard("Reloading from disk"):
                self.load_review(force=True)

        def _check_disk(self) -> None:
            """Notice when the files under the sheet change from outside.

            Polled rather than tied to window focus, which Tk reports to
            whichever child had focus rather than to the window, so it is not
            dependable. The poll is three stat() calls. It also catches
            cull.py run from a console while the window is open.
            """
            if self.sheet is None or self.review_batch is None:
                return
            batch = Batch(self.review_batch)
            if not review_mod.changed_on_disk(batch, self.sheet):
                self._disk_notice_shown = False
                return
            if not self.review_dirty:
                # Nothing of yours to lose: show what's on disk now.
                self.load_review(force=True)
                self._log_line(f"{self.review_batch.name}: reloaded — it was changed "
                               "outside the window (spreadsheet or cull.py).")
                return
            if not getattr(self, "_disk_notice_shown", False):
                self._disk_notice_shown = True
                self.review_dirty_label.configure(
                    text="Unsaved changes — and the file changed on disk")
                self._log_line(f"{self.review_batch.name}: changed outside the window while "
                               "you have unsaved changes here. Saving will ask which to keep.")

        def _showing_excluded(self) -> bool:
            return self.review_filter.get() == "Excluded by the rules"

        def _visible_rows(self) -> list:
            if self.sheet is None:
                return []
            wanted = self.review_filter.get()
            needle = self.review_search.get().strip().lower()
            source = self.excluded_rows if self._showing_excluded() else self.sheet.rows
            out = []
            for row in source:
                if wanted == "Not yet decided" and row.decision != review_mod.UNDECIDED:
                    continue
                if wanted == "Keeps" and row.decision != review_mod.KEEP:
                    continue
                if wanted == "Culls" and row.decision != review_mod.CULL:
                    continue
                if needle and needle not in " ".join((
                        row.name, row.get("town"), row.notes)).lower():
                    continue
                out.append(row)
            return out

        def _row_values(self, row) -> list:
            from pipeline.details import VERDICT_LABELS

            out = []
            for key, *_ in self.REVIEW_COLUMNS:
                if key == "decision":
                    out.append(status_text(row.decision, row.reason,
                                           excluded=getattr(row, "excluded", None) is not None))
                elif key == "site_verdict":
                    out.append(VERDICT_LABELS.get(row.get(key), row.get(key)))
                else:
                    out.append(row.get(key))
            return out

        def _row_tag(self, row) -> str:
            if getattr(row, "excluded", None) is not None:
                return "excluded"
            return row.decision if row.decision else "todo"

        def _sync_actions(self) -> None:
            """Decisions make no sense on the excluded list: grey them out."""
            excluded = self._showing_excluded()
            for child in self.act_bar.winfo_children():
                try:
                    child.configure(state=tk.DISABLED if excluded else tk.NORMAL)
                except tk.TclError:
                    pass
            if not excluded:
                self.review_reason.configure(state="readonly")
            self.review_hint.configure(text=(
                "Read-only: dropped by the selection rules. To bring them back, run "
                "Find again with 'Keep dormant' or 'Keep fine' ticked."
                if excluded else
                "k keep · c cull · u undecide, each moving to the next row · Ctrl+S "
                "save. Untouched rows count as keeps."))

        def fill_review(self) -> None:
            self._sync_actions()
            keep_focus = self.review_tree.focus()
            self.review_tree.delete(*self.review_tree.get_children())
            for row in self._visible_rows():
                self.review_tree.insert("", tk.END, iid=row.place_id,
                                        values=self._row_values(row),
                                        tags=(self._row_tag(row),))
            kids = self.review_tree.get_children()
            target = keep_focus if keep_focus in kids else (kids[0] if kids else "")
            if target:
                self.review_tree.selection_set(target)
                self.review_tree.focus(target)
                self.review_tree.see(target)
            else:
                self._build_detail_placeholder(
                    "Nothing matches this view." if self.sheet is not None else "")
            self.update_review_counts()

        def update_review_counts(self) -> None:
            for child in self.review_counts.winfo_children():
                child.destroy()
            if self.sheet is None:
                return
            counts = self.sheet.counts()
            for text, tone in ((f"{counts[review_mod.KEEP]} kept", "good"),
                               (f"{counts[review_mod.CULL]} culled", "bad"),
                               (f"{counts[review_mod.UNDECIDED]} to decide", "ok"),
                               (f"{len(self.excluded_rows)} excluded", "muted")):
                self.chip(self.review_counts, text, tone).pack(side=tk.LEFT, padx=(0, 6))
            if self.review_batch is not None:
                self.head_status.configure(text=self.review_batch.name)

        def set_review_dirty(self, dirty: bool) -> None:
            self.review_dirty = dirty
            self.review_dirty_label.configure(
                text="● Unsaved changes" if dirty else "")

        def sort_review(self, key: str) -> None:
            """Click a header to sort; click again to reverse. Score starts
            highest-first, everything else A→Z / low→high."""
            if self.sheet is None or self._showing_excluded():
                return
            if getattr(self, "_review_sort", None) == key:
                self._review_sort_desc = not self._review_sort_desc
            else:
                self._review_sort = key
                self._review_sort_desc = key == "lead_score"
            review_mod.sort_rows(self.sheet.rows, key, descending=self._review_sort_desc)
            arrow = " ▼" if self._review_sort_desc else " ▲"
            for col, title, *_ in self.REVIEW_COLUMNS:
                self.review_tree.heading(col, text=title + (arrow if col == key else ""))
            self.fill_review()

        # -- shortlist: decisions ---------------------------------------------

        def _selected_rows(self) -> list:
            if self.sheet is None:
                return []
            return [r for r in (self.sheet.by_id(i)
                                for i in self.review_tree.selection()) if r]

        def mark(self, decision: str, rows=None):
            """Keep, cull or undecide; then move on to the next row.

            Moving on is what makes a cull quick: k, k, c, k… down the list
            with the pane following, instead of click-row, click-button.
            """
            if self._showing_excluded():
                return "break"
            rows = rows if rows is not None else self._selected_rows()
            if not rows:
                return "break"
            self._commit_detail()
            before = list(self.review_tree.get_children())
            anchor = rows[-1].place_id
            index = before.index(anchor) if anchor in before else -1
            reason = self.review_reason.get() if decision == review_mod.CULL else ""
            for row in rows:
                row.decision = decision
                row.reason = reason
            self.set_review_dirty(True)
            for row in rows:
                self._paint_row(row)
            self.update_review_counts()
            if len(rows) == 1 and index >= 0:
                after = list(self.review_tree.get_children())
                if after:
                    # Next row down: the one after this, or — if this one left
                    # the view (a filter) — whatever now sits in its place.
                    still_here = anchor in after
                    nxt = after[min(index + (1 if still_here else 0), len(after) - 1)]
                    self.review_tree.selection_set(nxt)
                    self.review_tree.focus(nxt)
                    self.review_tree.see(nxt)
            else:
                self.show_detail()
            self.review_tree.focus_set()
            return "break"

        def apply_reason(self) -> None:
            """Changing the dropdown re-codes any culled rows selected."""
            rows = [r for r in self._selected_rows() if r.decision == review_mod.CULL]
            if not rows:
                return
            for row in rows:
                row.reason = self.review_reason.get()
                self._paint_row(row)
            self.set_review_dirty(True)
            self.update_review_counts()
            self.show_detail()

        def _paint_row(self, row) -> None:
            """Repaint one row in place; drop it if it no longer fits the view."""
            if not self.review_tree.exists(row.place_id):
                return
            if row not in self._visible_rows():
                self.review_tree.delete(row.place_id)
                return
            self.review_tree.item(row.place_id, values=self._row_values(row),
                                  tags=(self._row_tag(row),))

        # -- shortlist: saving -------------------------------------------------

        def save_review(self) -> None:
            if self.sheet is None or self.review_batch is None:
                return
            self._commit_detail()
            problems = review_mod.validate(self.sheet)
            if problems:
                shown = "\n".join(problems[:8])
                if len(problems) > 8:
                    shown += f"\n… and {len(problems) - 8} more"
                messagebox.showwarning(
                    APP_TITLE,
                    "Every cull needs a reason code before this can be saved:\n\n"
                    + shown + "\n\nSelect those rows and pick a reason.")
                return
            batch = Batch(self.review_batch)
            if review_mod.changed_on_disk(batch, self.sheet):
                answer = messagebox.askyesnocancel(
                    APP_TITLE,
                    "approved.csv or the rejection list was changed outside the window "
                    "since you opened this sheet — probably in your spreadsheet.\n\n"
                    "Yes — save this sheet, replacing those changes\n"
                    "No — throw away this sheet's changes and load what's on disk\n"
                    "Cancel — do nothing yet",
                    icon="warning")
                if answer is None:
                    return
                if answer is False:
                    self.load_review(force=True)
                    return
            try:
                result = review_mod.save(batch, self.sheet)
            except PermissionError:
                # Excel holds a lock on an open CSV; Windows refuses the write.
                messagebox.showerror(
                    APP_TITLE,
                    "Couldn't save: approved.csv is open in another program — "
                    "probably your spreadsheet.\n\nClose it there, then press Save "
                    "decisions again. Nothing has been lost.")
                return
            except Exception as exc:  # noqa: BLE001
                messagebox.showerror(APP_TITLE, f"Could not save:\n{exc}")
                return
            self._disk_notice_shown = False
            self.set_review_dirty(False)
            self._log_line(
                f"{self.review_batch.name}: saved {result['approved']} approved, "
                f"{result['rejected']} rejected -> approved.csv, rejections.jsonl")
            if result["reasons"]:
                self._log_line("  " + ", ".join(
                    f"{n} {code}" for code, n in
                    sorted(result["reasons"].items(), key=lambda kv: -kv[1])))
            self.refresh_batches()

        def open_review_csv(self) -> None:
            """Optional now — everything is in the window — but still there
            for anyone who wants the list in a spreadsheet."""
            folder = self._selected_review_folder()
            if folder is None:
                return
            approved = folder / "approved.csv"
            self._open(approved if approved.is_file() else folder / "shortlist.csv")

        def _build_batches(self) -> None:
            f = self.tab_batches
            f.columnconfigure(0, weight=1)
            f.rowconfigure(0, weight=1)

            cols = ("name", "found", "approved", "excluded", "status")
            self.batch_tree = ttk.Treeview(f, columns=cols, show="headings", selectmode="extended")
            for col, title, width, anchor in (
                ("name", "Batch", 300, "w"), ("found", "Found", 58, "e"),
                ("approved", "Kept", 58, "e"), ("excluded", "Excluded", 84, "e"),
                ("status", "Status", 84, "w"),
            ):
                self.batch_tree.heading(col, text=title)
                # minwidth stops a narrow window truncating a heading into
                # "Approv" — the name column gives up space instead.
                self.batch_tree.column(col, width=width, minwidth=width,
                                       anchor=anchor, stretch=col == "name")
            self.batch_tree.column("name", minwidth=160)
            self.batch_tree.grid(row=0, column=0, sticky="nsew")
            sb = ttk.Scrollbar(f, command=self.batch_tree.yview)
            self.batch_tree.configure(yscrollcommand=sb.set)
            sb.grid(row=0, column=1, sticky="ns")
            self.batch_tree.bind("<Double-1>", lambda _e: self.review_selected_batch())

            holder, side = self._scrollable(f, width=300)
            holder.grid(row=0, column=2, sticky="ns", padx=(12, 0))

            def section(title):
                lf = ttk.LabelFrame(side, text=title, padding=8)
                lf.pack(fill=tk.X, pady=(0, 8), padx=(0, 4))
                return lf

            ttk.Label(side, text="Select a batch on the left, then:",
                      foreground="#666").pack(anchor="w", pady=(0, 6))

            s = section("Open")
            ttk.Button(s, text="Review in the Shortlist tab", style="Accent.TButton",
                       command=self.review_selected_batch).pack(fill=tk.X, pady=1)
            ttk.Button(s, text="See what the rules excluded", command=self.open_excluded).pack(fill=tk.X, pady=1)
            ttk.Button(s, text="Open CSV in a spreadsheet", command=self.open_best_csv).pack(fill=tk.X, pady=1)
            ttk.Button(s, text="Open batch folder", command=self.open_folder).pack(fill=tk.X, pady=1)
            ttk.Button(s, text="Refresh", command=self.refresh_batches).pack(fill=tk.X, pady=(6, 1))

            s = section("Cull")
            self.sheet_approved_only = tk.BooleanVar(value=True)
            ttk.Button(s, text="Build contact sheet", command=self.run_contactsheet).pack(fill=tk.X, pady=1)
            ttk.Checkbutton(s, text="approved rows only, if culled",
                            variable=self.sheet_approved_only).pack(anchor="w")
            ttk.Button(s, text="Import decisions.json…", command=self.import_decisions).pack(fill=tk.X, pady=(6, 1))
            ttk.Button(s, text="Import 'reason' column from CSV", command=self.import_csv_reasons).pack(fill=tk.X, pady=1)
            self.cull_start_over = tk.BooleanVar(value=False)
            ttk.Checkbutton(s, text="start over from the original shortlist",
                            variable=self.cull_start_over).pack(anchor="w")

            s = section("Mock up")
            ttk.Button(s, text="Webflow briefs for every kept one",
                       command=self.run_mockups).pack(fill=tk.X, pady=1)
            ttk.Label(s, text="Or one at a time: 'Webflow brief' in the\nShortlist tab copies its prompt for you.",
                      style="Small.TLabel", justify="left").pack(anchor="w", pady=(4, 0))

            s = section("Combine (Ctrl-click several)")
            grid = ttk.Frame(s)
            grid.pack(fill=tk.X)
            ttk.Label(grid, text="Only niche").grid(row=0, column=0, sticky="w")
            self.combine_niche = ttk.Entry(grid, width=16)
            self.combine_niche.grid(row=0, column=1, sticky="ew", pady=1)
            ttk.Label(grid, text="Only town").grid(row=1, column=0, sticky="w")
            self.combine_town = ttk.Entry(grid, width=16)
            self.combine_town.grid(row=1, column=1, sticky="ew", pady=1)
            ttk.Label(grid, text="Name").grid(row=2, column=0, sticky="w")
            self.combine_name = ttk.Entry(grid, width=16)
            self.combine_name.grid(row=2, column=1, sticky="ew", pady=1)
            grid.columnconfigure(1, weight=1)
            self.combine_use_culls = tk.BooleanVar(value=True)
            ttk.Checkbutton(s, text="use culled lists (approved rows only)",
                            variable=self.combine_use_culls).pack(anchor="w", pady=(4, 0))
            ttk.Button(s, text="Combine selected", command=self.run_combine).pack(fill=tk.X, pady=(4, 1))
            ttk.Button(s, text="Combine all", command=lambda: self.run_combine(all_batches=True)).pack(fill=tk.X, pady=1)

            s = section("Learn")
            ttk.Button(s, text="What have my culls taught it?", command=self.run_tune).pack(fill=tk.X, pady=1)
            ttk.Button(s, text="Apply tune's suggestions…", command=self.apply_tune).pack(fill=tk.X, pady=1)
            ttk.Label(s, text="Cull good-looking sites as 'site_fine' — that's\nwhat teaches it to stop bringing them.",
                      style="Small.TLabel", justify="left").pack(anchor="w", pady=(4, 0))

        def _build_setup(self) -> None:
            # Scrollable: on a laptop screen the folder paths fell off the end.
            holder, f = self._scrollable(self.tab_setup, width=900)
            holder.pack(fill=tk.BOTH, expand=True)
            f.columnconfigure(0, weight=1)
            keys = ttk.LabelFrame(f, text="API keys (saved to .env in the project folder — never committed)", padding=10)
            keys.grid(row=0, column=0, sticky="ew")
            keys.columnconfigure(1, weight=1)
            existing = menu.read_existing_env()
            self.key_vars: dict[str, tk.StringVar] = {}
            self.key_entries: list[ttk.Entry] = []
            self.show_keys = tk.BooleanVar(value=False)
            for r, (key, label, purpose, where) in enumerate(menu.KEY_INFO):
                base = r * 2
                ttk.Label(keys, text=label, font=("", 9, "bold")).grid(row=base, column=0, sticky="nw", pady=(6, 0))
                var = tk.StringVar(value=existing.get(key, ""))
                self.key_vars[key] = var
                entry = ttk.Entry(keys, textvariable=var, show="•")
                entry.grid(row=base, column=1, sticky="ew", pady=(6, 0))
                self.key_entries.append(entry)
                hint = " ".join(line.strip() for line in purpose.splitlines())
                ttk.Label(keys, text=hint, foreground="#555", wraplength=680, justify="left").grid(
                    row=base + 1, column=1, sticky="w")
            bar = ttk.Frame(keys)
            bar.grid(row=len(menu.KEY_INFO) * 2, column=0, columnspan=2, sticky="ew", pady=(10, 0))
            ttk.Checkbutton(bar, text="Show keys", variable=self.show_keys,
                            command=self._toggle_keys).pack(side=tk.LEFT)
            ttk.Button(bar, text="Save keys", command=self.save_keys).pack(side=tk.RIGHT)
            ttk.Button(bar, text="Test keys (one small call each)", command=self.run_test_keys).pack(side=tk.RIGHT, padx=6)
            ttk.Button(bar, text="Check setup", command=self.run_check).pack(side=tk.RIGHT, padx=6)

            where = ttk.LabelFrame(f, text="Where to get them", padding=10)
            where.grid(row=2, column=0, sticky="ew", pady=(10, 0))
            for key, label, _purpose, howto in menu.KEY_INFO:
                text = " ".join(line.strip() for line in howto.splitlines())
                ttk.Label(where, text=f"{label}: {text}", wraplength=880, justify="left",
                          foreground="#333").pack(anchor="w", pady=(0, 6))

            you = ttk.LabelFrame(f, text="You", padding=10)
            you.grid(row=1, column=0, sticky="ew", pady=(10, 0))
            you.columnconfigure(1, weight=1)
            ttk.Label(you, text="Your business name", font=("", 9, "bold")).grid(
                row=0, column=0, sticky="w", padx=(0, 10))
            self.your_business = tk.StringVar(value=existing.get(
                "YOUR_BUSINESS_NAME") or os.environ.get("YOUR_BUSINESS_NAME", ""))
            ttk.Entry(you, textvariable=self.your_business).grid(row=0, column=1, sticky="ew")
            ttk.Button(you, text="Save", command=self.save_your_business).grid(
                row=0, column=2, padx=(6, 0))
            ttk.Label(you, text="Shown on every mockup's banner: \"Design concept for …, "
                      "prepared by <this>. Not a live website.\"", foreground="#555",
                      wraplength=680, justify="left").grid(row=1, column=1, sticky="w")

            paths = ttk.LabelFrame(f, text="Folders", padding=10)
            paths.grid(row=3, column=0, sticky="ew", pady=(10, 0))
            cache = menu.cache_dir()
            ttk.Label(paths, text=f"Batches:  {menu.batches_dir().resolve()}").pack(anchor="w")
            ttk.Label(paths, text=f"Cache:    {cache}   — keep this folder: it is what makes "
                      "re-runs free").pack(anchor="w")

        # -- helpers ----------------------------------------------------------

        def _scrollable(self, parent, width, bg=None):
            """A column that grows a scrollbar when it has to, and scrolls
            with the mouse wheel while the pointer is over it.

            The action panel is taller than a 768px laptop screen once the
            log pane takes its share, and a button you cannot reach is the
            same as a button that isn't there.
            """
            bg = bg or PALETTE["bg"]
            holder = ttk.Frame(parent)
            canvas = tk.Canvas(holder, width=width, highlightthickness=0,
                               borderwidth=0, background=bg)
            bar = ttk.Scrollbar(holder, orient=tk.VERTICAL, command=canvas.yview)
            inner = tk.Frame(canvas, bg=bg)
            window = canvas.create_window((0, 0), window=inner, anchor="nw")
            canvas.configure(yscrollcommand=bar.set)
            canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

            def resized(_event=None):
                canvas.configure(scrollregion=canvas.bbox("all"))
                canvas.itemconfigure(window, width=canvas.winfo_width())
                # Only show the scrollbar when the content overflows, so it
                # doesn't steal width on a roomy screen.
                needed = inner.winfo_reqheight() > canvas.winfo_height()
                if needed and not bar.winfo_ismapped():
                    bar.pack(side=tk.RIGHT, fill=tk.Y)
                elif not needed and bar.winfo_ismapped():
                    bar.pack_forget()
                    canvas.yview_moveto(0)

            def wheel(event):
                if inner.winfo_reqheight() <= canvas.winfo_height():
                    return
                if getattr(event, "num", None) in (4, 5):        # Linux
                    step = -1 if event.num == 4 else 1
                else:                                            # Windows, macOS
                    step = -1 if event.delta > 0 else 1
                    if os.name == "nt":
                        step *= max(1, abs(event.delta) // 120)
                canvas.yview_scroll(step * 2, "units")

            def enter(_e):
                canvas.bind_all("<MouseWheel>", wheel)
                canvas.bind_all("<Button-4>", wheel)
                canvas.bind_all("<Button-5>", wheel)

            def leave(_e):
                canvas.unbind_all("<MouseWheel>")
                canvas.unbind_all("<Button-4>")
                canvas.unbind_all("<Button-5>")

            inner.bind("<Configure>", resized)
            canvas.bind("<Configure>", resized)
            holder.bind("<Enter>", enter)
            holder.bind("<Leave>", leave)
            holder.scroll_top = lambda: canvas.yview_moveto(0)
            return holder, inner

        def _weights_threshold(self, name, default):
            try:
                from pipeline.scoring import Weights

                return Weights.load(HERE / "weights.json").thresholds.get(name, default)
            except Exception:  # noqa: BLE001
                return default

        def _log_line(self, line: str) -> None:
            self.log.configure(state=tk.NORMAL)
            self.log.insert(tk.END, line + "\n", log_tag(line))
            self.log.see(tk.END)
            self.log.configure(state=tk.DISABLED)

        def clear_log(self) -> None:
            self.log.configure(state=tk.NORMAL)
            self.log.delete("1.0", tk.END)
            self.log.configure(state=tk.DISABLED)

        def _tick(self) -> None:
            self.runner.drain()
            self._ticks = getattr(self, "_ticks", 0) + 1
            if self._ticks % 20 == 0:          # every two seconds
                try:
                    self._check_disk()
                except Exception:  # noqa: BLE001 — a poll must never kill the loop
                    pass
            self.after(100, self._tick)

        def _set_busy(self, busy: bool, what: str = "") -> None:
            state = tk.DISABLED if busy else tk.NORMAL
            self.btn_find.configure(state=state)
            self.btn_stop.configure(state=tk.NORMAL if busy else tk.DISABLED)
            self.status.configure(text=(f"Running: {what}…" if busy else "Ready"),
                                  foreground="#b35c00" if busy else "#555")

        def start(self, argv: list[str], what: str,
                  after: Optional[Callable[[int], None]] = None) -> None:
            if self.runner.busy:
                messagebox.showinfo(APP_TITLE, "Something is already running — wait for it "
                                    "to finish, or press Stop.")
                return
            self._after_run = after
            self._log_line("")
            self._log_line("$ " + " ".join(self._quote(a) for a in argv))
            try:
                self.runner.start(argv)
            except Exception as exc:  # noqa: BLE001
                messagebox.showerror(APP_TITLE, f"Could not start:\n{exc}")
                return
            self._set_busy(True, what)

        @staticmethod
        def _quote(arg: str) -> str:
            return f'"{arg}"' if " " in arg else arg

        def _run_finished(self, code: int) -> None:
            self._set_busy(False)
            if self._pairs_config is not None:
                self._pairs_config.unlink(missing_ok=True)
                self._pairs_config = None
            self._log_line(f"[finished with exit code {code}]")
            self.status.configure(text="Done" if code == 0 else f"Finished with errors (code {code})",
                                  foreground="#1a7f37" if code == 0 else "#b00020")
            cb, self._after_run = self._after_run, None
            if cb is not None:
                cb(code)

        def on_close(self) -> None:
            if self.review_dirty and not messagebox.askokcancel(
                APP_TITLE, "There are unsaved decisions on the Shortlist tab. "
                "Close anyway?"):
                return
            self.runner.stop()
            self.destroy()

        def stop_run(self) -> None:
            self.runner.stop()
            self._log_line("[stopped]")

        def _selected_batches(self) -> list[Path]:
            root = menu.batches_dir()
            return [root / self.batch_tree.item(i, "values")[0]
                    for i in self.batch_tree.selection()]

        def _one_batch(self) -> Optional[Path]:
            chosen = self._selected_batches()
            if len(chosen) != 1:
                messagebox.showinfo(APP_TITLE, "Select one batch in the list first.")
                return None
            return chosen[0]

        def _open(self, path: Path) -> None:
            if not path.exists():
                messagebox.showinfo(APP_TITLE, f"Not there yet:\n{path}")
                return
            menu.open_in_default_app(path)

        # -- find tab ---------------------------------------------------------

        def refresh_history(self) -> None:
            # The configured TTL, not a hard-coded 30: a shorter cache would
            # otherwise be listed as "free to repeat" after it had expired.
            ttl = Config.from_env().cache_ttl_days
            self.history = (past_searches(menu.cache_dir(), ttl_days=ttl)
                            if not self._demo_mode() else [])
            self.history_tree.delete(*self.history_tree.get_children())
            for h in self.history:
                age = h.age_days
                when = "?" if age is None else ("today" if age == 0 else f"{age}d ago")
                self.history_tree.insert("", tk.END, values=(
                    h.niche, h.area, h.radius_m, when, h.results))
            self.update_cost()

        def use_history(self, _event=None) -> None:
            sel = self.history_tree.selection()
            if not sel:
                return
            niche, area, radius, *_ = self.history_tree.item(sel[0], "values")
            if self._listed_pairs():
                # A run list is being built: a click adds to it, rather than
                # filling fields the run would then ignore.
                entry = f"{niche}  in  {area}"
                if entry not in self.pairs.get(0, tk.END):
                    self.pairs.insert(tk.END, entry)
                self.radius.set(radius)
                self.update_cost()
                return
            self.niche.set(niche)
            self.area.delete(0, tk.END)
            self.area.insert(0, area)
            self.radius.set(radius)
            self.update_cost()

        def _demo_mode(self) -> bool:
            return not Config.from_env().google_places_api_key

        def _listed_pairs(self) -> list[tuple[str, str]]:
            return [tuple(item.split("  in  ", 1)) for item in self.pairs.get(0, tk.END)]

        def _current_pairs(self) -> list[tuple[str, str]]:
            pairs, _ = pairs_for_run(self._listed_pairs(),
                                     (self.niche.get(), self.area.get()))
            return pairs

        def add_pair(self) -> None:
            niche, area = self.niche.get().strip(), self.area.get().strip()
            if not (niche and area):
                return
            entry = f"{niche}  in  {area}"
            if entry not in self.pairs.get(0, tk.END):
                self.pairs.insert(tk.END, entry)
            self.update_cost()

        def remove_pair(self) -> None:
            for i in reversed(self.pairs.curselection()):
                self.pairs.delete(i)
            self.update_cost()

        def update_cost(self) -> None:
            if self._demo_mode():
                self.cost.configure(text="No Places key set — this will run on the demo data.",
                                    foreground="#666")
                return
            try:
                radius = int(float(self.radius.get()))
            except ValueError:
                radius = 0
            free, msg = cost_note(self._current_pairs(), radius, self.history,
                                  refresh=self.opt_refresh.get())
            listed = self._listed_pairs()
            if listed:
                _, left_out = pairs_for_run(listed, (self.niche.get(), self.area.get()))
                msg = (f"This run: {len(listed)} search{'es' if len(listed) != 1 else ''} "
                       "from the list. " + msg)
                if left_out:
                    msg += "\nWhat's typed above isn't in the list — you'll be asked."
            self.cost.configure(text=msg, foreground="#1a7f37" if free else "#b35c00")

        def run_find(self) -> None:
            typed = (self.niche.get().strip(), self.area.get().strip())
            pairs, left_out = pairs_for_run(self._listed_pairs(), typed)
            if left_out:
                answer = messagebox.askyesnocancel(
                    APP_TITLE,
                    f"You've typed '{typed[0]} in {typed[1]}', but it isn't in this "
                    f"run's list of {len(pairs)}.\n\nAdd it to the run?\n\n"
                    "Yes — search it too\nNo — run just the list\nCancel — go back")
                if answer is None:
                    return
                if answer:
                    self.add_pair()
                    pairs = self._current_pairs()
            if not pairs:
                messagebox.showinfo(APP_TITLE, "Give a niche and a town first.")
                return
            try:
                radius = int(float(self.radius.get()))
                top = int(float(self.top.get()))
                dormant = int(float(self.dormant_days.get()))
                min_points = int(float(self.min_points.get()))
            except ValueError:
                messagebox.showinfo(APP_TITLE, "Radius, rows and dormant days must be numbers.")
                return
            if self.opt_refresh.get() and not messagebox.askyesno(
                APP_TITLE, "Re-fetching ignores the cache and bills Google for every "
                "search and details call again. Continue?"):
                return
            demo = self._demo_mode()
            fixture = menu.FIXTURE if demo else None
            if demo:
                from pipeline.fixtures import FixturePlacesClient

                covered = FixturePlacesClient.from_file(menu.FIXTURE).available()
                if covered and (pairs[0][0].lower(), pairs[0][1].lower()) not in {
                        (n.lower(), a.lower()) for n, a in covered}:
                    pairs = [covered[0]]
                    self._log_line(f"Demo data only covers {covered[0][0]} in {covered[0][1]} — using that.")
            config_path = None
            if len(pairs) > 1:
                config_path = menu.write_pairs_config(pairs)
                self._pairs_config = config_path
            argv = find_command(
                pairs, radius=radius, top=top,
                screenshots=self.opt_screens.get(), site_checks=self.opt_site.get(),
                owner_lookup=self.opt_owner.get(), site_contacts=self.opt_contacts.get(),
                keep_dormant=self.opt_keep_dormant.get(), keep_fine=self.opt_keep_fine.get(),
                dormant_days=dormant if dormant != int(self._weights_threshold("dormant_after_days", 365)) else None,
                min_site_points=(min_points if min_points != int(
                    self._weights_threshold("min_site_points", 25)) else None),
                refresh=self.opt_refresh.get() and not demo, fixture=fixture,
                config_path=config_path,
            )

            def done(code: int) -> None:
                self.refresh_history()
                self.refresh_batches()
                self.refresh_review_picker()
                if code == 0:
                    folders = menu.list_batches()
                    if folders and self.review_dirty:
                        # A find takes minutes; the operator may well have been
                        # culling meanwhile. Never swap the sheet out from under
                        # unsaved decisions — say the new batch is ready instead.
                        self._log_line(
                            f"New batch ready: {folders[0].name}. Your unsaved "
                            f"decisions on {self.review_batch.name} are untouched — "
                            "save them, then pick the new batch on the Shortlist tab.")
                        self.status.configure(text="Done — new batch ready (unsaved "
                                              "changes kept)", foreground="#1a7f37")
                    elif folders:
                        # Straight to the list it just built — the next thing
                        # to do with it, and the reason the run was started.
                        self.review_pick.set(folders[0].name)
                        self.load_review(force=True)
                        self.tabs.select(self.tab_review)

            self.start(argv, "find prospects", after=done)

        # -- batches tab ------------------------------------------------------

        def refresh_batches(self) -> None:
            self.batch_tree.delete(*self.batch_tree.get_children())
            for folder in menu.list_batches():
                try:
                    s = batch_summary(folder)
                except Exception as exc:  # noqa: BLE001 — a half-written folder must not hide the rest
                    self.batch_tree.insert("", tk.END, values=(folder.name, "?", "", "", f"unreadable: {exc}"))
                    continue
                self.batch_tree.insert("", tk.END, values=(
                    s["name"], s["found"],
                    "" if s["approved"] is None else s["approved"],
                    s["excluded"] or "", s["status"]))

        def review_selected_batch(self) -> None:
            folder = self._one_batch()
            if folder is None:
                return
            if self.review_batch != folder:
                self.review_pick.set(folder.name)
                self.load_review()
                if self.review_batch != folder:      # unsaved changes: they said no
                    return
            if self._showing_excluded():
                self.review_filter.set("Everything")
                self.fill_review()
            self.tabs.select(self.tab_review)

        def open_best_csv(self) -> None:
            folder = self._one_batch()
            if folder is None:
                return
            approved = folder / "approved.csv"
            self._open(approved if approved.is_file() else folder / "shortlist.csv")

        def open_excluded(self) -> None:
            """The rules' drops, in the window: the Shortlist tab's Excluded view."""
            folder = self._one_batch()
            if folder is None:
                return
            if not (folder / "excluded.csv").is_file():
                messagebox.showinfo(APP_TITLE, "Nothing was excluded in this batch.")
                return
            if self.review_batch != folder:
                self.review_pick.set(folder.name)
                self.load_review()
                if self.review_batch != folder:      # unsaved changes: they said no
                    return
            self.review_filter.set("Excluded by the rules")
            self.fill_review()
            self.tabs.select(self.tab_review)

        def open_folder(self) -> None:
            folder = self._one_batch()
            if folder is not None:
                self._open(folder)

        def run_contactsheet(self) -> None:
            folder = self._one_batch()
            if folder is None:
                return
            approved_only = self.sheet_approved_only.get() and (folder / "approved.csv").is_file()
            argv = contactsheet_command(folder, approved_only=approved_only)

            def done(code: int) -> None:
                sheet = folder / "contactsheet.html"
                if code == 0 and sheet.is_file():
                    self._open(sheet)
                    self._log_line("Keep or cull each tile, pick a reason for the culls, click "
                                   "Export decisions, then use 'Import decisions.json…' here.")

            self.start(argv, "contact sheet", after=done)

        def webflow_brief(self, row) -> None:
            """Build this business's Webflow brief, put the prompt on the
            clipboard and open the build sheet — paste, and Webflow builds."""
            if self.review_batch is None:
                return
            folder = self.review_batch
            argv = mockup_command(folder, [row.place_id])
            out = folder / row.place_id / "mockup"

            def done(code: int) -> None:
                prompt, sheet = out / "prompt.txt", out / "brief.html"
                if code != 0 or not prompt.is_file():
                    return
                self.clipboard_clear()
                self.clipboard_append(prompt.read_text(encoding="utf-8"))
                if sheet.is_file():
                    self._open(sheet)
                self._log_line("The prompt is on your clipboard — in Webflow, start a new "
                               "site with the AI Site Builder and paste it.")
                self._log_line("The page that opened has everything else, with a Copy "
                               "button on each piece.")

            self.start(argv, f"Webflow brief for {row.get('name') or row.place_id}", after=done)

        def run_mockups(self) -> None:
            folder = self._one_batch()
            if folder is None:
                return
            if not (folder / "approved.csv").is_file():
                messagebox.showinfo(APP_TITLE, "Nothing kept in this batch yet — keep some "
                                    "in the Shortlist tab first.")
                return
            self.start(mockup_command(folder, approved=True), "Webflow briefs")

        def _settle_before_import(self, folder: Path) -> bool:
            """An import rewrites the files the sheet is showing. Deal with
            unsaved changes on that same batch first; True means go ahead."""
            if not self.review_dirty or self.review_batch != folder:
                return True
            answer = messagebox.askyesnocancel(
                APP_TITLE,
                f"You have unsaved decisions on {folder.name} in the Shortlist tab.\n\n"
                "Yes — save them first, then import\n"
                "No — discard them and import\n"
                "Cancel — don't import",
                icon="warning")
            if answer is None:
                return False
            if answer:
                self.save_review()
                return not self.review_dirty   # a refused save leaves it dirty
            self.set_review_dirty(False)
            return True

        def import_decisions(self) -> None:
            folder = self._one_batch()
            if folder is None:
                return
            if not self._settle_before_import(folder):
                return
            start = Path.home() / "Downloads"
            chosen = filedialog.askopenfilename(
                title="decisions.json exported from the contact sheet",
                initialdir=str(start if start.is_dir() else Path.home()),
                filetypes=[("decisions.json", "*.json"), ("All files", "*.*")])
            if not chosen:
                return
            argv = cull_import_command(folder, Path(chosen), start_over=self.cull_start_over.get())
            self.start(argv, "import decisions", after=lambda _c: self._after_cull(folder))

        def import_csv_reasons(self) -> None:
            folder = self._one_batch()
            if folder is None:
                return
            if not self._settle_before_import(folder):
                return
            culled = (folder / "approved.csv").is_file() and not self.cull_start_over.get()
            csv_name = "approved.csv" if culled else "shortlist.csv"
            if not messagebox.askokcancel(
                APP_TITLE,
                f"This reads a column headed 'reason' from {csv_name}.\n\n"
                "Open it in your spreadsheet, add that column, fill it in for the rows "
                "you're rejecting (a reason code: chain, site_fine, too_small, …), save "
                "as CSV, then press OK."):
                return
            argv = cull_csv_command(folder, csv_name, start_over=self.cull_start_over.get())
            self.start(argv, "import reasons", after=lambda _c: self._after_cull(folder))

        def _after_cull(self, folder: Path) -> None:
            """A cull run outside the sheet changed the files it reads."""
            self.refresh_batches()
            if self.review_batch is not None and self.review_batch == folder:
                if self.review_dirty:
                    # Edited while the import ran: don't discard — the disk
                    # check flags the conflict and Save will ask which to keep.
                    self._check_disk()
                else:
                    self.load_review(force=True)

        def run_combine(self, all_batches: bool = False) -> None:
            if all_batches:
                batches = [f for f in menu.list_batches()
                           if not Batch(f).read_meta().get("combined_from")]
            else:
                batches = self._selected_batches()
                if len(batches) < 2:
                    messagebox.showinfo(APP_TITLE, "Select two or more batches to combine "
                                        "(Ctrl-click), or use Combine all.")
                    return
            if not batches:
                messagebox.showinfo(APP_TITLE, "No batches to combine yet.")
                return
            argv = combine_command(
                batches, niche=self.combine_niche.get(), town=self.combine_town.get(),
                name=self.combine_name.get(), use_culls=self.combine_use_culls.get())
            self.start(argv, "combine", after=lambda _c: self.refresh_batches())

        def run_tune(self) -> None:
            batches = menu.list_batches()
            if not batches:
                messagebox.showinfo(APP_TITLE, "No batches yet.")
                return
            self.start(tune_command(batches), "tune")

        def apply_tune(self) -> None:
            """Write the tuner's proposals into weights.json, after asking."""
            batches = menu.list_batches()
            if not batches:
                messagebox.showinfo(APP_TITLE, "No batches yet.")
                return
            if not messagebox.askokcancel(
                APP_TITLE,
                "This writes tune's suggestions into weights.json, so every find from "
                "now on uses them.\n\nThe current weights.json is copied first "
                "(weights.json.bak-<date>), so it can be put back.\n\nRun 'What have "
                "my culls taught it?' first if you want to read them. Apply now?"):
                return

            def done(code: int) -> None:
                # The Find tab's controls show weights.json's values; refresh them.
                self.min_points.set(int(self._weights_threshold("min_site_points", 25)))
                self.dormant_days.set(int(self._weights_threshold("dormant_after_days", 365)))

            self.start(tune_command(batches, apply=True), "apply tune", after=done)

        # -- setup tab --------------------------------------------------------

        def _toggle_keys(self) -> None:
            show = "" if self.show_keys.get() else "•"
            for entry in self.key_entries:
                entry.configure(show=show)

        def save_keys(self) -> None:
            values = dict(menu.read_existing_env())
            for key, var in self.key_vars.items():
                values[key] = var.get().strip()
            if not any(values.get(k) for k, _, _, _ in menu.KEY_INFO):
                messagebox.showinfo(APP_TITLE, "No keys entered, so nothing was written.")
                return
            path = menu.write_env(values)
            for key, value in values.items():
                if value:
                    os.environ[key] = value
            self.config_obj = Config.from_env()
            self._log_line(f"Keys written to {path}")
            self.refresh_history()
            messagebox.showinfo(APP_TITLE, f"Saved to {path.name}. Use 'Test keys' to check they work.")

        def save_your_business(self) -> None:
            values = dict(menu.read_existing_env())
            values["YOUR_BUSINESS_NAME"] = self.your_business.get().strip()
            path = menu.write_env(values)
            os.environ["YOUR_BUSINESS_NAME"] = values["YOUR_BUSINESS_NAME"]
            self._log_line(f"Your business name saved to {path.name}.")

        def run_check(self) -> None:
            self.start(check_setup_command(), "setup check")

        def run_test_keys(self) -> None:
            self.start(test_keys_command(), "key test")

    return App


def _fatal(message: str) -> None:
    """Report a startup failure somewhere the operator will actually see it.

    Under pythonw there is no console, so stderr alone is a silent failure.
    The Windows box comes from the OS, needing no tkinter — which is the one
    thing that may be missing when this is called.
    """
    print(message, file=sys.stderr)
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, message, APP_TITLE, 0x10)
            return
        except Exception:  # noqa: BLE001
            pass
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(APP_TITLE, message)
        root.destroy()
    except Exception:  # noqa: BLE001
        pass


def main(argv=None) -> int:
    import argparse

    p = argparse.ArgumentParser(prog="gui.py", description=__doc__.split("\n")[0])
    p.add_argument("--screenshot", metavar="PNG", help=argparse.SUPPRESS)
    args = p.parse_args(argv)

    # Checked before anything else, because the usual cause of the window
    # not appearing is tkinter missing — and gui.bat starts this under
    # pythonw, where a traceback goes nowhere anyone can see it.
    try:
        import tkinter  # noqa: F401
    except ImportError:
        _fatal(
            "Python on this machine was installed without tkinter, which is "
            "what draws the window.\n\n"
            "On Windows: re-run the python.org installer, choose Modify, and "
            "tick 'tcl/tk and IDLE'.\n"
            "On Linux: install the python3-tk package.\n\n"
            "The menu version still works meanwhile: run.bat, or "
            "python run.py"
        )
        return 1

    try:
        App = build_app()
        app = App()
    except Exception as exc:  # noqa: BLE001
        _fatal(f"The window could not start:\n\n{exc}")
        return 1
    if args.screenshot:
        # Render, give the widgets a moment to lay out, then let the caller
        # grab the display. Used to check the window looks right headlessly.
        app.after(4000, lambda: (Path(args.screenshot).touch(), app.destroy()))
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
