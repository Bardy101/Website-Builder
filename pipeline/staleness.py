"""Staleness detection — what a site looks like, not how fast it loads.

Brief: docs/briefs/brief-visual-quality-signal.md, part 1.1.

PageSpeed measures page weight and technical hygiene. Neither correlates with
visual quality, and in practice they anti-correlate: good design costs bytes.
The first live run ranked a genuinely good site top because it was heavy, and
buried dated sparse ones because they were light.

These checks look for age instead of slowness — a missing viewport tag, no
media queries, a copyright year left behind, table layout, Flash. Each is
evidence the site has not been touched in years, which is the thing actually
being sold against.

Every check fails open: absent evidence is never evidence. A fetch that times
out must not add a single point.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Callable, Optional
from urllib.parse import urljoin, urlparse

# Enough to see the head, the footer and the stylesheet links.
MAX_BODY_BYTES = 2_000_000
FETCH_TIMEOUT = 10.0
STYLESHEET_TIMEOUT = 5.0
MAX_STYLESHEETS = 3

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# Managed platforms that imply someone pays for and maintains the site.
MODERN_PLATFORMS = {"squarespace", "framer", "webflow", "shopify"}
# DIY builders — neutral, they span both extremes.
DIY_PLATFORMS = {"wix", "godaddy", "weebly"}

_PLATFORM_ASSET_PATTERNS = [
    ("wix", re.compile(r"(wixstatic\.com|parastorage\.com|wix\.com)", re.I)),
    ("squarespace", re.compile(r"(squarespace\.com|sqspcdn\.com|static1\.squarespace)", re.I)),
    ("shopify", re.compile(r"(cdn\.shopify\.com|shopifycdn|myshopify\.com)", re.I)),
    ("webflow", re.compile(r"(webflow\.com|assets\.website-files\.com)", re.I)),
    ("framer", re.compile(r"(framer\.com|framerusercontent\.com)", re.I)),
    ("godaddy", re.compile(r"(godaddy\.com|img1\.wsimg\.com)", re.I)),
    ("weebly", re.compile(r"(weebly\.com|editmysite\.com)", re.I)),
    ("wordpress", re.compile(r"(wp-content|wp-includes|wordpress)", re.I)),
]

_COPYRIGHT_MARK = re.compile(r"(©|&copy;|\(c\)|copyright)", re.I)
_YEAR = re.compile(r"\b(19[89]\d|20[0-4]\d)\b")
_FLASH = re.compile(r"\.swf\b|application/x-shockwave-flash", re.I)


def _blank(fetch_ok: bool = False) -> dict:
    """Every signal absent. Nothing here can earn a point."""
    return {
        "fetch_ok": fetch_ok,
        "has_viewport": None,
        "has_https": None,
        "copyright_year": None,
        "copyright_age": None,
        "uses_table_layout": None,
        "has_media_queries": None,
        "has_flash": None,
        "platform_hint": None,
    }


def detect_platform(html: str) -> Optional[str]:
    """Platform from the generator meta tag, else from asset URL patterns."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html or "", "html.parser")
    generator = soup.find("meta", attrs={"name": re.compile(r"^generator$", re.I)})
    if generator:
        content = (generator.get("content") or "").lower()
        for name in list(MODERN_PLATFORMS) + list(DIY_PLATFORMS) + ["wordpress"]:
            if name in content:
                return name
    for name, pattern in _PLATFORM_ASSET_PATTERNS:
        if pattern.search(html or ""):
            return name
    return None


def find_copyright_year(html: str, *, now_year: Optional[int] = None) -> Optional[int]:
    """Newest plausible year near a copyright mark in a footer-ish node.

    Takes the maximum: a site showing "© 2008-2019" was last touched in 2019.
    """
    from bs4 import BeautifulSoup

    now_year = now_year or datetime.now(timezone.utc).year
    soup = BeautifulSoup(html or "", "html.parser")

    candidates = []
    for node in soup.find_all(["footer", "div", "p", "span", "small", "section"]):
        text = node.get_text(" ", strip=True)
        if not text or len(text) > 400 or not _COPYRIGHT_MARK.search(text):
            continue
        candidates.extend(int(y) for y in _YEAR.findall(text))
    if not candidates:
        # Some footers put the mark and year in sibling nodes; fall back to
        # the whole document text, still requiring a copyright mark nearby.
        text = soup.get_text(" ", strip=True)
        for match in _COPYRIGHT_MARK.finditer(text):
            window = text[match.start(): match.start() + 60]
            candidates.extend(int(y) for y in _YEAR.findall(window))
    plausible = [y for y in candidates if 1990 <= y <= now_year]
    return max(plausible) if plausible else None


def uses_table_layout(html: str) -> bool:
    """A table with 3+ rows and no header cells, outside <article>.

    Data tables have <th>; layout tables from the 2000s do not.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html or "", "html.parser")
    for table in soup.find_all("table"):
        if table.find_parent("article"):
            continue
        if len(table.find_all("tr")) >= 3 and not table.find("th"):
            return True
    return False


def has_flash(html: str) -> bool:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup.find_all(["embed", "object"]):
        blob = " ".join(str(v) for v in tag.attrs.values())
        if _FLASH.search(blob):
            return True
        for param in tag.find_all("param"):
            if _FLASH.search(" ".join(str(v) for v in param.attrs.values())):
                return True
    return False


def _stylesheet_urls(html: str, base_url: str) -> list[str]:
    """First-party stylesheets only, capped."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html or "", "html.parser")
    host = urlparse(base_url).netloc.lower()
    urls = []
    for link in soup.find_all("link"):
        rel = " ".join(link.get("rel") or []).lower()
        if "stylesheet" not in rel:
            continue
        href = link.get("href")
        if not href:
            continue
        full = urljoin(base_url, href)
        if urlparse(full).netloc.lower() in ("", host):
            urls.append(full)
        if len(urls) >= MAX_STYLESHEETS:
            break
    return urls


def has_media_queries(html: str, base_url: str, fetch_text: Callable) -> bool:
    """@media in an inline <style> or a first-party stylesheet.

    Fails open to True: if the CSS cannot be read we must not claim the site
    is non-responsive, because that would award points for a failed fetch.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html or "", "html.parser")
    for style in soup.find_all("style"):
        if "@media" in (style.string or style.get_text() or ""):
            return True

    sheets = _stylesheet_urls(html, base_url)
    if not sheets:
        # No inline @media and no readable stylesheet: no evidence either way.
        return True
    any_read = False
    for url in sheets:
        css = fetch_text(url, STYLESHEET_TIMEOUT)
        if css is None:
            continue
        any_read = True
        if "@media" in css:
            return True
    return False if any_read else True


def _default_fetch(url: str, timeout: float):
    """Return (final_url, text) or None. Caps the body read."""
    import requests

    try:
        resp = requests.get(
            url, timeout=timeout, allow_redirects=True,
            headers={"User-Agent": BROWSER_UA}, stream=True,
        )
        if resp.status_code >= 400:
            return None
        body = resp.raw.read(MAX_BODY_BYTES, decode_content=True) or b""
        encoding = resp.encoding or "utf-8"
        return resp.url, body.decode(encoding, errors="replace")
    except Exception:
        return None


def analyse_staleness(
    url: Optional[str],
    *,
    fetch=None,
    prefetched: Optional[tuple[str, str]] = None,
    now_year: Optional[int] = None,
) -> dict:
    """Analyse one site. ``prefetched`` is (final_url, html) if the enrich
    stage already fetched the page — the brief requires not fetching twice.
    """
    if not url:
        return _blank()

    fetch = fetch or _default_fetch
    if prefetched is not None:
        final_url, html = prefetched
    else:
        got = fetch(url if "://" in url else f"https://{url}", FETCH_TIMEOUT)
        if got is None:
            return _blank()
        final_url, html = got

    if not html:
        return _blank()

    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    year = find_copyright_year(html, now_year=now_year)
    this_year = now_year or datetime.now(timezone.utc).year

    def fetch_text(u, timeout):
        got = fetch(u, timeout)
        return got[1] if got else None

    return {
        "fetch_ok": True,
        "has_viewport": soup.find(
            "meta", attrs={"name": re.compile(r"^viewport$", re.I)}
        ) is not None,
        "has_https": str(final_url).lower().startswith("https://"),
        "copyright_year": year,
        "copyright_age": (this_year - year) if year is not None else None,
        "uses_table_layout": uses_table_layout(html),
        "has_media_queries": has_media_queries(html, final_url, fetch_text),
        "has_flash": has_flash(html),
        "platform_hint": detect_platform(html),
    }


# Which findings count as staleness signals, for both the points subtotal and
# the site_verdict bucket. One definition so the two can never disagree.
def staleness_flags(analysis: dict, *, stale_years: int = 3) -> dict[str, bool]:
    """The signals that are positively established. Unknown is never a flag."""
    a = analysis or {}
    age = a.get("copyright_age")
    return {
        "no_viewport": a.get("has_viewport") is False,
        "no_media_queries": a.get("has_media_queries") is False,
        "copyright_stale": age is not None and age >= stale_years,
        "table_layout": a.get("uses_table_layout") is True,
        "no_https": a.get("has_https") is False,
        "flash": a.get("has_flash") is True,
    }


def signal_count(analysis: dict, *, stale_years: int = 3) -> int:
    return sum(staleness_flags(analysis, stale_years=stale_years).values())
