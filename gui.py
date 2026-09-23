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


def tune_command(batches: list[Path]) -> list[str]:
    if not batches:
        raise ValueError("nothing to tune from")
    return ["tune.py", "--batch"] + [str(b) for b in batches]


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
        if self.busy and self.proc is not None:
            self.proc.terminate()

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
            try:
                ttk.Style(self).theme_use("vista" if os.name == "nt" else "clam")
            except tk.TclError:
                pass
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

        # -- layout ---------------------------------------------------------

        def _build(self) -> None:
            outer = ttk.PanedWindow(self, orient=tk.VERTICAL)
            outer.pack(fill=tk.BOTH, expand=True)

            self.tabs = ttk.Notebook(outer)
            self.tab_find = ttk.Frame(self.tabs, padding=10)
            self.tab_review = ttk.Frame(self.tabs, padding=10)
            self.tab_batches = ttk.Frame(self.tabs, padding=10)
            self.tab_setup = ttk.Frame(self.tabs, padding=10)
            self.tabs.add(self.tab_find, text="  Find prospects  ")
            self.tabs.add(self.tab_review, text="  Shortlist  ")
            self.tabs.add(self.tab_batches, text="  Batches  ")
            self.tabs.add(self.tab_setup, text="  Setup  ")
            outer.add(self.tabs, weight=4)

            log_frame = ttk.Frame(outer, padding=(10, 4, 10, 6))
            outer.add(log_frame, weight=1)
            bar = ttk.Frame(log_frame)
            bar.pack(fill=tk.X)
            ttk.Label(bar, text="Output").pack(side=tk.LEFT)
            self.status = ttk.Label(bar, text="Ready", foreground="#555")
            self.status.pack(side=tk.LEFT, padx=12)
            self.btn_stop = ttk.Button(bar, text="Stop", command=self.stop_run,
                                       state=tk.DISABLED)
            self.btn_stop.pack(side=tk.RIGHT)
            ttk.Button(bar, text="Clear", command=self.clear_log).pack(
                side=tk.RIGHT, padx=(0, 6))
            self.log = tk.Text(log_frame, height=12, wrap=tk.NONE,
                               font=("Consolas" if os.name == "nt" else "Monospace", 10),
                               state=tk.DISABLED, background="#1e1e1e",
                               foreground="#d4d4d4", insertbackground="#d4d4d4")
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
                    outer.sashpos(0, int(height * 0.72))

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
            self.pairs = tk.Listbox(left, height=4, exportselection=False)
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
            ttk.Checkbutton(right, text="Keep sites rated 'fine'",
                            variable=self.opt_keep_fine).grid(row=7, column=0, sticky="w", pady=1)
            dorm = ttk.Frame(right)
            dorm.grid(row=8, column=0, sticky="w", pady=(2, 0))
            ttk.Label(dorm, text="Dormant after").pack(side=tk.LEFT)
            self.dormant_days = ttk.Spinbox(dorm, from_=30, to=1500, increment=30, width=6)
            self.dormant_days.set(int(self._weights_threshold("dormant_after_days", 365)))
            self.dormant_days.pack(side=tk.LEFT, padx=4)
            ttk.Label(dorm, text="days without a review").pack(side=tk.LEFT)

            ttk.Separator(right).grid(row=9, column=0, sticky="ew", pady=8)
            self.opt_refresh = tk.BooleanVar(value=False)
            ttk.Checkbutton(right, text="Ignore the 30-day cache and re-fetch",
                            variable=self.opt_refresh, command=self.update_cost).grid(
                row=10, column=0, sticky="w")
            ttk.Label(right, text="costs real API calls for data you already have",
                      foreground="#666").grid(row=11, column=0, sticky="w", padx=(20, 0))

            # Run --------------------------------------------------------------
            go = ttk.Frame(f, padding=(0, 10, 0, 0))
            go.grid(row=1, column=1, sticky="sew")
            self.cost = ttk.Label(go, text="", foreground="#1a7f37", wraplength=380)
            self.cost.pack(anchor="w", pady=(0, 6))
            self.btn_find = ttk.Button(go, text="Find prospects", command=self.run_find)
            self.btn_find.pack(anchor="e", ipadx=12, ipady=4)
            for w in (self.niche, self.area, self.radius):
                w.bind("<KeyRelease>", lambda _e: self.update_cost())
                w.bind("<<ComboboxSelected>>", lambda _e: self.update_cost())

        # -- shortlist tab ----------------------------------------------------

        # What the sheet shows, and how wide. Decision first: it is the column
        # you are here to change, and the eye should land on it.
        REVIEW_COLUMNS = [
            ("decision", "", 34, "center"),
            ("lead_score", "Score", 68, "e"),
            ("name", "Business", 210, "w"),
            ("town", "Town", 100, "w"),
            ("site_verdict", "Site", 100, "w"),
            ("staleness_points", "Stale", 66, "e"),
            ("review_count", "Reviews", 86, "e"),
            ("recent_review_date", "Last review", 116, "w"),
            ("address_to", "Address to", 150, "w"),
            ("reason", "Cull reason", 124, "w"),
            ("notes", "Notes", 200, "w"),
        ]
        EDITABLE_COLUMNS = {"address_to", "notes"}

        def _build_review(self) -> None:
            from pipeline.cull import REASON_CODES

            self.reason_codes = list(REASON_CODES)
            self.sheet = None
            self.review_batch: Optional[Path] = None
            self.review_dirty = False
            self._editor = None

            f = self.tab_review
            f.columnconfigure(0, weight=1)
            f.rowconfigure(2, weight=1)

            # Row 0: which batch, and where it stands.
            top = ttk.Frame(f)
            top.grid(row=0, column=0, sticky="ew")
            ttk.Label(top, text="Batch").pack(side=tk.LEFT)
            self.review_pick = ttk.Combobox(top, state="readonly", width=44)
            self.review_pick.pack(side=tk.LEFT, padx=(6, 8))
            self.review_pick.bind("<<ComboboxSelected>>", lambda _e: self.load_review())
            ttk.Button(top, text="Reload from disk",
                       command=self.reload_review).pack(side=tk.LEFT)
            ttk.Button(top, text="Open in spreadsheet",
                       command=self.open_review_csv).pack(side=tk.LEFT, padx=6)
            self.review_counts = ttk.Label(top, text="", foreground="#333")
            self.review_counts.pack(side=tk.LEFT, padx=12)

            # Row 1: narrowing the list down.
            bar = ttk.Frame(f)
            bar.grid(row=1, column=0, sticky="ew", pady=(8, 4))
            ttk.Label(bar, text="Show").pack(side=tk.LEFT)
            self.review_filter = ttk.Combobox(
                bar, state="readonly", width=16,
                values=["Everything", "Not yet decided", "Keeps", "Culls"])
            self.review_filter.set("Everything")
            self.review_filter.pack(side=tk.LEFT, padx=(6, 12))
            self.review_filter.bind("<<ComboboxSelected>>", lambda _e: self.fill_review())
            ttk.Label(bar, text="Find").pack(side=tk.LEFT)
            self.review_search = ttk.Entry(bar, width=26)
            self.review_search.pack(side=tk.LEFT, padx=6)
            self.review_search.bind("<KeyRelease>", lambda _e: self.fill_review())
            ttk.Label(bar, text="name, town or notes", foreground="#666").pack(side=tk.LEFT)
            # On this row, not the top one: the batch picker and counts fill
            # that, and the conflict message is long enough to be clipped.
            self.review_dirty_label = ttk.Label(bar, text="", foreground="#b35c00")
            self.review_dirty_label.pack(side=tk.RIGHT)

            # Row 2: the sheet.
            wrap = ttk.Frame(f)
            wrap.grid(row=2, column=0, sticky="nsew")
            wrap.columnconfigure(0, weight=1)
            wrap.rowconfigure(0, weight=1)
            cols = [c[0] for c in self.REVIEW_COLUMNS]
            self.review_tree = ttk.Treeview(wrap, columns=cols, show="headings",
                                            selectmode="extended")
            for key, title, width, anchor in self.REVIEW_COLUMNS:
                self.review_tree.heading(
                    key, text=title, command=lambda k=key: self.sort_review(k))
                self.review_tree.column(key, width=width, minwidth=width,
                                        anchor=anchor, stretch=key in ("name", "notes"))
            self.review_tree.column("name", minwidth=120)
            self.review_tree.column("notes", minwidth=90)
            self.review_tree.grid(row=0, column=0, sticky="nsew")
            vsb = ttk.Scrollbar(wrap, command=self.review_tree.yview)
            self.review_tree.configure(yscrollcommand=vsb.set)
            vsb.grid(row=0, column=1, sticky="ns")
            hsb = ttk.Scrollbar(wrap, orient=tk.HORIZONTAL, command=self.review_tree.xview)
            self.review_tree.configure(xscrollcommand=hsb.set)
            hsb.grid(row=1, column=0, sticky="ew")

            # A kept row should read as settled and a culled one as struck
            # out, at a glance, without reading the first column.
            self.review_tree.tag_configure("keep", background="#eaf6ec")
            self.review_tree.tag_configure("cull", background="#f2f2f2",
                                           foreground="#8a8a8a")
            self.review_tree.tag_configure("todo", background="#ffffff")

            self.review_tree.bind("<Double-1>", self.begin_edit)
            self.review_tree.bind("<Return>", lambda _e: self.mark(review_mod.KEEP))
            self.review_tree.bind("k", lambda _e: self.mark(review_mod.KEEP))
            self.review_tree.bind("c", lambda _e: self.mark(review_mod.CULL))
            self.review_tree.bind("u", lambda _e: self.mark(review_mod.UNDECIDED))

            # Row 3: what to do about the selection.
            act = ttk.Frame(f)
            act.grid(row=3, column=0, sticky="ew", pady=(8, 0))
            ttk.Label(act, text="Selected rows:").pack(side=tk.LEFT)
            ttk.Button(act, text="Keep  (k)",
                       command=lambda: self.mark(review_mod.KEEP)).pack(side=tk.LEFT, padx=(8, 4))
            ttk.Button(act, text="Cull  (c)",
                       command=lambda: self.mark(review_mod.CULL)).pack(side=tk.LEFT, padx=4)
            ttk.Button(act, text="Undecide  (u)",
                       command=lambda: self.mark(review_mod.UNDECIDED)).pack(side=tk.LEFT, padx=4)
            ttk.Label(act, text="reason").pack(side=tk.LEFT, padx=(16, 4))
            self.review_reason = ttk.Combobox(act, state="readonly", width=17,
                                              values=self.reason_codes)
            self.review_reason.set(self.reason_codes[0])
            self.review_reason.pack(side=tk.LEFT)
            self.review_reason.bind("<<ComboboxSelected>>",
                                    lambda _e: self.apply_reason())
            self.review_save = ttk.Button(act, text="Save decisions",
                                          command=self.save_review)
            self.review_save.pack(side=tk.RIGHT, ipadx=8)

            ttk.Label(f, text="Double-click 'Address to' or 'Notes' to edit. "
                      "Culling needs a reason code — that is what tune.py learns from. "
                      "Rows you never touch count as keeps.",
                      foreground="#666").grid(row=4, column=0, sticky="w", pady=(6, 0))

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
            self._cancel_edit()
            try:
                self.sheet = review_mod.load(Batch(folder))
            except Exception as exc:  # noqa: BLE001
                messagebox.showerror(APP_TITLE, f"Could not read that batch:\n{exc}")
                return
            self.review_batch = folder
            self.set_review_dirty(False)
            self.fill_review()
            if not len(self.sheet):
                self._log_line(f"{folder.name}: shortlist.csv is empty.")

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
                    text="unsaved changes — and the file changed on disk")
                self._log_line(f"{self.review_batch.name}: changed outside the window while "
                               "you have unsaved changes here. Saving will ask which to keep.")

        def _visible_rows(self) -> list:
            if self.sheet is None:
                return []
            wanted = self.review_filter.get()
            needle = self.review_search.get().strip().lower()
            out = []
            for row in self.sheet.rows:
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

        MARKS = {"keep": "✓", "cull": "✗", "": "·"}

        def fill_review(self) -> None:
            self._cancel_edit()
            self.review_tree.delete(*self.review_tree.get_children())
            for row in self._visible_rows():
                tag = row.decision if row.decision else "todo"
                values = []
                for key, *_ in self.REVIEW_COLUMNS:
                    if key == "decision":
                        values.append(self.MARKS.get(row.decision, "·"))
                    elif key == "reason":
                        values.append(row.reason)
                    elif key == "notes":
                        values.append(row.notes)
                    else:
                        values.append(row.get(key))
                self.review_tree.insert("", tk.END, iid=row.place_id,
                                        values=values, tags=(tag,))
            self.update_review_counts()

        def update_review_counts(self) -> None:
            if self.sheet is None:
                self.review_counts.configure(text="")
                return
            counts = self.sheet.counts()
            shown = len(self.review_tree.get_children())
            text = (f"{counts[review_mod.KEEP]} keep · "
                    f"{counts[review_mod.CULL]} cull · "
                    f"{counts[review_mod.UNDECIDED]} undecided")
            if shown != len(self.sheet):
                text += f"   (showing {shown} of {len(self.sheet)})"
            self.review_counts.configure(text=text)

        def set_review_dirty(self, dirty: bool) -> None:
            self.review_dirty = dirty
            self.review_dirty_label.configure(
                text="unsaved changes" if dirty else "")

        def sort_review(self, key: str) -> None:
            """Click a header to sort; click again to reverse. Score starts
            highest-first, everything else A→Z / low→high."""
            if self.sheet is None:
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

        def mark(self, decision: str) -> None:
            rows = self._selected_rows()
            if not rows:
                return
            reason = self.review_reason.get() if decision == review_mod.CULL else ""
            for row in rows:
                row.decision = decision
                row.reason = reason
            self.set_review_dirty(True)
            self._refresh_rows(rows)
            return "break"

        def apply_reason(self) -> None:
            """Changing the dropdown re-codes any culled rows selected."""
            rows = [r for r in self._selected_rows() if r.decision == review_mod.CULL]
            if not rows:
                return
            for row in rows:
                row.reason = self.review_reason.get()
            self.set_review_dirty(True)
            self._refresh_rows(rows)

        def _refresh_rows(self, rows) -> None:
            """Repaint just the rows that changed, keeping scroll and selection."""
            visible = {r.place_id for r in self._visible_rows()}
            for row in rows:
                if row.place_id not in visible:
                    # It no longer matches the filter: drop it from view.
                    if self.review_tree.exists(row.place_id):
                        self.review_tree.delete(row.place_id)
                    continue
                if not self.review_tree.exists(row.place_id):
                    self.fill_review()
                    return
                self.review_tree.item(
                    row.place_id,
                    values=[(self.MARKS.get(row.decision, "·") if key == "decision"
                             else row.reason if key == "reason"
                             else row.notes if key == "notes"
                             else row.get(key))
                            for key, *_ in self.REVIEW_COLUMNS],
                    tags=(row.decision if row.decision else "todo",))
            self.update_review_counts()

        # -- shortlist: inline editing ----------------------------------------

        def begin_edit(self, event) -> None:
            """Put an Entry over the cell that was double-clicked."""
            if self.sheet is None:
                return
            item = self.review_tree.identify_row(event.y)
            column = self.review_tree.identify_column(event.x)
            if not item or not column:
                return
            index = int(column[1:]) - 1
            if not 0 <= index < len(self.REVIEW_COLUMNS):
                return
            key = self.REVIEW_COLUMNS[index][0]
            if key not in self.EDITABLE_COLUMNS:
                return
            row = self.sheet.by_id(item)
            if row is None:
                return
            box = self.review_tree.bbox(item, column)
            if not box:
                return
            self._cancel_edit()
            x, y, width, height = box
            entry = ttk.Entry(self.review_tree)
            entry.insert(0, row.notes if key == "notes" else row.get(key))
            entry.select_range(0, tk.END)
            entry.place(x=x, y=y, width=width, height=height)
            entry.focus_set()
            entry.bind("<Return>", lambda _e: self._commit_edit())
            entry.bind("<Escape>", lambda _e: self._cancel_edit())
            entry.bind("<FocusOut>", lambda _e: self._commit_edit())
            self._editor = (entry, row, key)

        def _commit_edit(self) -> None:
            if self._editor is None:
                return
            entry, row, key = self._editor
            value = entry.get().strip()
            self._editor = None
            entry.destroy()
            if value != (row.notes if key == "notes" else row.get(key)):
                row.set(key, value)
                self.set_review_dirty(True)
                self._refresh_rows([row])

        def _cancel_edit(self) -> None:
            if self._editor is None:
                return
            entry, _row, _key = self._editor
            self._editor = None
            entry.destroy()

        # -- shortlist: saving -------------------------------------------------

        def save_review(self) -> None:
            if self.sheet is None or self.review_batch is None:
                return
            self._commit_edit()
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
            self.batch_tree.bind("<Double-1>", lambda _e: self.open_best_csv())

            holder, side = self._scrollable(f, width=300)
            holder.grid(row=0, column=2, sticky="ns", padx=(12, 0))

            def section(title):
                lf = ttk.LabelFrame(side, text=title, padding=8)
                lf.pack(fill=tk.X, pady=(0, 8), padx=(0, 4))
                return lf

            ttk.Label(side, text="Select a batch on the left, then:",
                      foreground="#666").pack(anchor="w", pady=(0, 6))

            s = section("Open")
            ttk.Button(s, text="Open shortlist / approved", command=self.open_best_csv).pack(fill=tk.X, pady=1)
            ttk.Button(s, text="Open excluded list", command=self.open_excluded).pack(fill=tk.X, pady=1)
            ttk.Button(s, text="Open batch folder", command=self.open_folder).pack(fill=tk.X, pady=1)
            ttk.Button(s, text="Refresh", command=self.refresh_batches).pack(fill=tk.X, pady=(6, 1))

            s = section("Cull")
            self.sheet_approved_only = tk.BooleanVar(value=True)
            ttk.Button(s, text="Review on the Shortlist tab",
                       command=self.review_selected_batch).pack(fill=tk.X, pady=(1, 6))
            ttk.Button(s, text="Build contact sheet", command=self.run_contactsheet).pack(fill=tk.X, pady=1)
            ttk.Checkbutton(s, text="approved rows only, if culled",
                            variable=self.sheet_approved_only).pack(anchor="w")
            ttk.Button(s, text="Import decisions.json…", command=self.import_decisions).pack(fill=tk.X, pady=(6, 1))
            ttk.Button(s, text="Import 'reason' column from CSV", command=self.import_csv_reasons).pack(fill=tk.X, pady=1)
            self.cull_start_over = tk.BooleanVar(value=False)
            ttk.Checkbutton(s, text="start over from the original shortlist",
                            variable=self.cull_start_over).pack(anchor="w")

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
            ttk.Button(s, text="Tune scoring from every cull", command=self.run_tune).pack(fill=tk.X, pady=1)

        def _build_setup(self) -> None:
            f = self.tab_setup
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
            where.grid(row=1, column=0, sticky="ew", pady=(10, 0))
            for key, label, _purpose, howto in menu.KEY_INFO:
                text = " ".join(line.strip() for line in howto.splitlines())
                ttk.Label(where, text=f"{label}: {text}", wraplength=880, justify="left",
                          foreground="#333").pack(anchor="w", pady=(0, 6))

            paths = ttk.LabelFrame(f, text="Folders", padding=10)
            paths.grid(row=2, column=0, sticky="ew", pady=(10, 0))
            cache = menu.cache_dir()
            ttk.Label(paths, text=f"Batches:  {menu.batches_dir().resolve()}").pack(anchor="w")
            ttk.Label(paths, text=f"Cache:    {cache}   — keep this folder: it is what makes "
                      "re-runs free").pack(anchor="w")

        # -- helpers ----------------------------------------------------------

        @staticmethod
        def _scrollable(parent, width):
            """A fixed-width column that grows a scrollbar when it has to.

            The action panel is taller than a 768px laptop screen once the
            log pane takes its share, and a button you cannot reach is the
            same as a button that isn't there.
            """
            holder = ttk.Frame(parent)
            canvas = tk.Canvas(holder, width=width, highlightthickness=0,
                               borderwidth=0)
            bar = ttk.Scrollbar(holder, orient=tk.VERTICAL, command=canvas.yview)
            inner = ttk.Frame(canvas)
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

            inner.bind("<Configure>", resized)
            canvas.bind("<Configure>", resized)
            return holder, inner

        def _weights_threshold(self, name, default):
            try:
                from pipeline.scoring import Weights

                return Weights.load(HERE / "weights.json").thresholds.get(name, default)
            except Exception:  # noqa: BLE001
                return default

        def _log_line(self, line: str) -> None:
            self.log.configure(state=tk.NORMAL)
            self.log.insert(tk.END, line + "\n")
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
            self.review_pick.set(folder.name)
            self.load_review()
            self.tabs.select(self.tab_review)

        def open_best_csv(self) -> None:
            folder = self._one_batch()
            if folder is None:
                return
            approved = folder / "approved.csv"
            self._open(approved if approved.is_file() else folder / "shortlist.csv")

        def open_excluded(self) -> None:
            folder = self._one_batch()
            if folder is None:
                return
            path = folder / "excluded.csv"
            if not path.is_file():
                messagebox.showinfo(APP_TITLE, "Nothing was excluded in this batch — no file to open.")
                return
            self._open(path)

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
