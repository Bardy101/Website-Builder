"""Screenshot capture — the part that actually judges visual quality.

Brief: docs/briefs/brief-visual-quality-signal.md, part 2.1.

Metrics cannot see whether a site looks good. Screenshots cannot rank. The
score's job is to produce a sane ordering; the operator's eye on a contact
sheet does the culling. This module supplies the pictures.

Captures are cached under the 30-day place cache keyed by place_id, so a
re-run captures only genuinely new candidates. --refresh-screenshots forces
recapture.
"""

from __future__ import annotations

import os

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

DESKTOP = {"width": 1440, "height": 900}
MOBILE = {"width": 390, "height": 844}

NETWORKIDLE_TIMEOUT_MS = 15_000
SETTLE_MS = 2_000
COOKIE_CLICK_TIMEOUT_MS = 1_000
DEFAULT_CONCURRENCY = 4

# Best-effort only. A banner left showing is a cosmetic problem, not a failure.
COOKIE_SELECTORS = [
    "button#onetrust-accept-btn-handler",
    "button[aria-label*='Accept' i]",
    "button[id*='accept' i]",
    "button[class*='accept' i]",
    "a[id*='accept' i]",
    "[class*='cookie'] button",
    "button:has-text('Accept all')",
    "button:has-text('Accept All')",
    "button:has-text('Accept')",
    "button:has-text('I agree')",
    "button:has-text('Allow all')",
    "button:has-text('Got it')",
]


@dataclass
class ShotResult:
    place_id: str
    desktop: Optional[Path] = None
    mobile: Optional[Path] = None
    ok: bool = False
    reason: Optional[str] = None


@dataclass
class ScreenshotCapturer:
    """Captures above-the-fold desktop and mobile shots, cached by place_id."""

    cache_dir: Path
    refresh: bool = False
    concurrency: int = DEFAULT_CONCURRENCY
    # Injectable so tests never launch a browser.
    capture_one: Optional[Callable] = None
    log: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.root = Path(self.cache_dir) / "screenshots"

    # -- paths --------------------------------------------------------------

    def paths_for(self, place_id: str) -> tuple[Path, Path]:
        return (
            self.root / f"{place_id}_desktop.png",
            self.root / f"{place_id}_mobile.png",
        )

    def page_path_for(self, place_id: str) -> Path:
        """The rendered page saved alongside the shots — see page_path()."""
        return page_path(self.paths_for(place_id)[0])

    def cached(self, place_id: str) -> Optional[ShotResult]:
        """A usable cached pair, unless a refresh was asked for."""
        if self.refresh:
            return None
        desktop, mobile = self.paths_for(place_id)
        if desktop.is_file() and mobile.is_file():
            return ShotResult(place_id, desktop, mobile, ok=True)
        return None

    # -- capture ------------------------------------------------------------

    def capture_all(self, targets: list[tuple[str, str]]) -> dict[str, ShotResult]:
        """targets: (place_id, url). Returns place_id -> ShotResult.

        Runs concurrently; one site failing never affects the others.
        """
        results: dict[str, ShotResult] = {}
        pending: list[tuple[str, str]] = []

        for place_id, url in targets:
            if not url:
                results[place_id] = ShotResult(place_id, reason="no website")
                continue
            hit = self.cached(place_id)
            if hit is not None:
                results[place_id] = hit
            else:
                pending.append((place_id, url))

        if not pending:
            return results

        self.root.mkdir(parents=True, exist_ok=True)
        worker = self.capture_one or self._playwright_capture

        # Check the browser once rather than failing identically per site: a
        # missing browser is one setup problem, not twenty-five site problems.
        if self.capture_one is None:
            ready, why = browser_ready()
            if not ready:
                self.log.append(
                    "Chromium is not installed, so no screenshots were taken. "
                    f"Fix: \"{sys.executable}\" -m playwright install chromium "
                    f"({why})"
                )
                for place_id, _ in pending:
                    results[place_id] = ShotResult(
                        place_id, reason="chromium not installed")
                return results

        def safely(place_id: str, url: str) -> ShotResult:
            desktop, mobile = self.paths_for(place_id)
            try:
                worker(url, desktop, mobile)
            except Exception as exc:  # noqa: BLE001 — one bad site, not the run
                # No file, empty path, reason recorded. Never points.
                for path in (desktop, mobile):
                    path.unlink(missing_ok=True)
                return ShotResult(place_id, reason=f"{type(exc).__name__}: {exc}")
            if not (desktop.is_file() and mobile.is_file()):
                return ShotResult(place_id, reason="capture produced no file")
            return ShotResult(place_id, desktop, mobile, ok=True)

        if self.concurrency <= 1 or len(pending) == 1:
            for place_id, url in pending:
                results[place_id] = safely(place_id, url)
        else:
            from concurrent.futures import ThreadPoolExecutor, as_completed

            with ThreadPoolExecutor(
                max_workers=min(self.concurrency, len(pending))
            ) as pool:
                futures = {
                    pool.submit(safely, pid, url): pid for pid, url in pending
                }
                for future in as_completed(futures):
                    results[futures[future]] = future.result()

        for result in results.values():
            if not result.ok and result.reason:
                self.log.append(f"{result.place_id}: {result.reason}")
        return results

    # -- the real browser ---------------------------------------------------

    def _playwright_capture(self, url: str, desktop: Path, mobile: Path) -> None:
        from playwright.sync_api import sync_playwright

        target = url if "://" in url else f"https://{url}"
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                args=["--no-sandbox"], **_launch_overrides()
            )
            try:
                for viewport, out in ((DESKTOP, desktop), (MOBILE, mobile)):
                    context = browser.new_context(
                        viewport=viewport,
                        is_mobile=viewport is MOBILE,
                        device_scale_factor=2 if viewport is MOBILE else 1,
                    )
                    page = context.new_page()
                    try:
                        try:
                            page.goto(
                                target, wait_until="networkidle",
                                timeout=NETWORKIDLE_TIMEOUT_MS,
                            )
                        except Exception:
                            # Networkidle never settles on sites with polling
                            # or chat widgets — take what has rendered.
                            page.goto(
                                target, wait_until="domcontentloaded",
                                timeout=NETWORKIDLE_TIMEOUT_MS,
                            )
                            page.wait_for_timeout(SETTLE_MS)
                        _dismiss_cookies(page)
                        # Above the fold only: the hero is what is judged.
                        # Written aside and renamed into place: a run stopped
                        # mid-write would otherwise leave a truncated PNG that
                        # the cache treats as a good capture for 30 days.
                        partial = out.with_name(out.name + ".part")
                        page.screenshot(path=str(partial), full_page=False,
                                        type="png")
                        os.replace(partial, out)
                        if viewport is DESKTOP:
                            _save_page(page, page_path(desktop))
                    finally:
                        context.close()
            finally:
                browser.close()


def page_path(desktop_png: Path) -> Path:
    """Where the rendered page for a capture lives: beside its desktop shot."""
    return desktop_png.with_name(desktop_png.name.replace("_desktop.png", "_page.json"))


# Asked of the browser, which has loaded every stylesheet — including ones a
# plain HTTP fetch cannot read. True/False is evidence; null means some sheets
# were cross-origin and unreadable, which proves nothing either way.
_MEDIA_QUERY_PROBE = """() => {
  let unreadable = false;
  for (const sheet of Array.from(document.styleSheets)) {
    let rules;
    try { rules = sheet.cssRules; } catch (e) { unreadable = true; continue; }
    for (const rule of Array.from(rules || [])) {
      if (rule.type === CSSRule.MEDIA_RULE) return true;
    }
    if (sheet.media && sheet.media.mediaText) return true;
  }
  return unreadable ? null : false;
}"""


def _save_page(page, path: Path) -> None:
    """Keep what the browser saw, so a site that blocked the direct check
    can still be measured. Best effort: a failure here never fails a capture."""
    try:
        try:
            media = page.evaluate(_MEDIA_QUERY_PROBE)
        except Exception:  # noqa: BLE001
            media = None
        payload = {"url": page.url, "html": page.content(), "media_queries": media}
        partial = path.with_name(path.name + ".part")
        partial.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(partial, path)
    except Exception:  # noqa: BLE001
        pass


def read_page(path: Path) -> Optional[dict]:
    """A saved page, or None if absent or unreadable."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("html"):
        return None
    return data


def browser_ready() -> tuple[bool, str]:
    """Can a browser actually launch? Checked once per run, not per site."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        return False, f"playwright not installed: {exc}"
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--no-sandbox"], **_launch_overrides())
            browser.close()
        return True, ""
    except Exception as exc:  # noqa: BLE001
        text = str(exc).strip()
        return False, (text.splitlines()[0] if text else type(exc).__name__)[:160]


def _launch_overrides() -> dict:
    """Use a pre-installed Chromium when Playwright's bundled build is absent.

    Machines that ship a browser separately (CI images, this project's remote
    sandbox) can have a build number Playwright does not expect. Pointing at
    the installed binary beats failing every capture, and is a no-op on a
    normal `playwright install` setup.
    """
    import os

    explicit = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
    if explicit and Path(explicit).exists():
        return {"executable_path": explicit}

    root = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", ""))
    if not root.is_dir():
        return {}
    for pattern in ("chromium-*/chrome-linux/chrome",
                    "chromium_headless_shell-*/chrome-linux/headless_shell",
                    "chromium-*/chrome-win/chrome.exe",
                    "chromium-*/chrome-mac/Chromium.app/Contents/MacOS/Chromium"):
        found = sorted(root.glob(pattern))
        if found:
            return {"executable_path": str(found[-1])}
    return {}


def _dismiss_cookies(page) -> None:
    """Click the first visible accept-looking control. Failure is fine."""
    for selector in COOKIE_SELECTORS:
        try:
            element = page.locator(selector).first
            if element.is_visible(timeout=COOKIE_CLICK_TIMEOUT_MS):
                element.click(timeout=COOKIE_CLICK_TIMEOUT_MS)
                page.wait_for_timeout(300)
                return
        except Exception:
            continue


def write_capture_log(batch_dir: Path, entries: list[str]) -> Optional[Path]:
    """Record why any capture is missing, per the brief's 'logged reason'."""
    if not entries:
        return None
    path = Path(batch_dir) / "screenshot-failures.log"
    path.write_text("\n".join(entries) + "\n", encoding="utf-8")
    return path
