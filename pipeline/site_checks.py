"""Website checks and the human-readable ``site_verdict``.

Brief: docs/briefs/brief-visual-quality-signal.md, part 1.4. The verdict now
comes from how *stale* a site looks, not how slowly it loads — mobile score
survives only as a tiebreaker and a reference column.

    none        -> no website at all (best lead)
    social_only -> a Facebook/Instagram page as the website
    dated       -> 2+ staleness signals set
    poor        -> exactly 1 staleness signal set
    fine        -> 0 staleness signals set
    unknown     -> the site could not be fetched, so nothing was established

``unknown`` is not in the brief's bucket list. It is kept because calling an
unreachable site "fine" would bury a possible prospect on no evidence, which
the fail-open rule exists to prevent. Flagged to the operator.

Pure classification is kept separate from the network so it can be tested
without hitting anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .staleness import analyse_staleness, signal_count
from urllib.parse import urlparse

# Hosts that mean "they don't really have a site" (spec: Facebook page /
# directory URL scores like no site at all).
SOCIAL_HOSTS = {
    "facebook.com", "m.facebook.com", "fb.com", "fb.me",
    "instagram.com", "twitter.com", "x.com", "linkedin.com",
    "tiktok.com", "youtube.com", "wa.me",
}
DIRECTORY_HOSTS = {
    "yell.com", "thomsonlocal.com", "yelp.com", "yelp.co.uk",
    "trustpilot.com", "checkatrade.com", "freeindex.co.uk",
    "cylex-uk.co.uk", "192.com", "scoot.co.uk", "bark.com",
    "google.com", "sites.google.com", "business.site",
    "wix.com", "wixsite.com", "godaddysites.com",
    "linktr.ee", "carrd.co", "wordpress.com", "blogspot.com",
}


def _host(url: str) -> str:
    netloc = urlparse(url if "://" in url else f"http://{url}").netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc


def classify_url(url: Optional[str]) -> str:
    """Return 'none', 'social', 'directory' or 'real' for a website URL."""
    if not url or not url.strip():
        return "none"
    host = _host(url)
    if not host:
        return "none"
    if host in SOCIAL_HOSTS:
        return "social"
    if host in DIRECTORY_HOSTS:
        return "directory"
    # Free page-builder subdomains (….business.site, ….wixsite.com) are
    # caught above via the registrable host check on common ones.
    return "real"


@dataclass
class SiteCheck:
    has_website: bool
    is_social_or_directory: bool
    https: Optional[bool] = None
    viewport: Optional[bool] = None
    # Reference and tiebreak only — it no longer contributes to lead_score.
    mobile_score: Optional[int] = None  # 0-100 PageSpeed mobile performance
    staleness: Optional[dict] = None
    verdict: str = "none"


def verdict_from(check: "SiteCheck", *, stale_years: int = 3) -> str:
    """Bucket a site by how many staleness signals fired."""
    if not check.has_website:
        return "none"
    if check.is_social_or_directory:
        return "social_only"
    analysis = check.staleness or {}
    if not analysis.get("fetch_ok"):
        # Nothing could be established. Never claim "fine" on no evidence.
        return "unknown"
    count = signal_count(analysis, stale_years=stale_years)
    if count >= 2:
        return "dated"
    if count == 1:
        return "poor"
    return "fine"


class SiteChecker:
    """Runs the network-facing checks. Injectable pieces keep it testable.

    ``http_get`` should return (status_code, final_url, html_text) or raise;
    ``pagespeed`` should return a mobile performance score 0-100 or None.
    Both default to real implementations built on ``requests``.
    """

    def __init__(
        self,
        pagespeed_api_key: Optional[str] = None,
        *,
        http_get=None,
        pagespeed=None,
        cache=None,
        timeout: float = 15.0,
        thresholds: Optional[dict] = None,
    ) -> None:
        self.pagespeed_api_key = pagespeed_api_key
        self._http_get = http_get or self._default_http_get
        self._pagespeed = pagespeed or self._default_pagespeed
        self.cache = cache
        self.timeout = timeout
        self.thresholds = thresholds or {}

    # -- network implementations -------------------------------------------

    def _default_http_get(self, url: str):
        import requests

        resp = requests.get(
            url,
            timeout=self.timeout,
            allow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (postal-pipeline site-check)"},
        )
        return resp.status_code, resp.url, resp.text

    def _default_pagespeed(self, url: str):
        """Return {"scored": True, "score": int|None} on a completed run,
        or None on a transport/quota error.

        The distinction matters for caching: "Lighthouse ran and could not
        score this page" is a durable fact worth caching (retrying costs a
        60s timeout every run), while a quota blip or network error is
        transient and must not be remembered for 30 days.
        """
        import requests

        params = {"url": url, "strategy": "mobile", "category": "performance"}
        if self.pagespeed_api_key:
            params["key"] = self.pagespeed_api_key
        try:
            resp = requests.get(
                "https://www.googleapis.com/pagespeedonline/v5/runPagespeed",
                params=params,
                timeout=60,
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            score = (
                data.get("lighthouseResult", {})
                .get("categories", {})
                .get("performance", {})
                .get("score")
            )
            return {
                "scored": True,
                "score": round(score * 100) if score is not None else None,
            }
        except Exception:
            return None

    # -- orchestration ------------------------------------------------------

    def check(self, website: Optional[str]) -> SiteCheck:
        kind = classify_url(website)
        if kind == "none":
            result = SiteCheck(has_website=False, is_social_or_directory=False)
            result.verdict = "none"
            return result
        if kind in ("social", "directory"):
            result = SiteCheck(has_website=True, is_social_or_directory=True)
            result.verdict = "social_only"
            return result

        # One fetch supplies every page-derived signal, including https and
        # viewport — the brief forbids fetching the same homepage twice.
        assert website is not None
        analysis = self._staleness(website)
        mobile = self._mobile_score(website)
        result = SiteCheck(
            has_website=True,
            is_social_or_directory=False,
            https=analysis.get("has_https"),
            viewport=analysis.get("has_viewport"),
            mobile_score=mobile,
            staleness=analysis,
        )
        result.verdict = verdict_from(
            result, stale_years=self.thresholds.get("copyright_stale_years", 3)
        )
        return result

    def _staleness(self, url: str) -> dict:
        """Staleness analysis, cached for the TTL like every external signal."""
        def fetch():
            # Route through this checker's transport so an injected one is
            # honoured, and so there is a single fetch path to reason about.
            def adapter(target: str, timeout: float):
                try:
                    status, final_url, html = self._http_get(target)
                except Exception:
                    return None
                if status and status >= 400:
                    return None
                return final_url, html

            return analyse_staleness(url, fetch=adapter)

        if self.cache is None:
            return fetch()
        # A failed analysis returns fetch_ok False; caching that would hide a
        # transient outage for 30 days, so only successes are stored.
        cached = self.cache.get("staleness", url)
        if isinstance(cached, dict) and cached.get("fetch_ok"):
            return cached
        fresh = fetch()
        if fresh.get("fetch_ok"):
            self.cache.set("staleness", url, fresh)
        return fresh

    def _fetch_page_signals(self, url: str):
        def fetch():
            try:
                status, final_url, html = self._http_get(
                    url if "://" in url else f"https://{url}"
                )
            except Exception:
                # Retry once over plain http, but only when that is actually
                # a different URL — retrying an http:// site as itself just
                # burns a second timeout on a dead host.
                fallback = (
                    url.replace("https://", "http://", 1)
                    if url.startswith("https://")
                    else (f"http://{url}" if "://" not in url else None)
                )
                if fallback is None:
                    return {"https": None, "viewport": None}
                try:
                    status, final_url, html = self._http_get(fallback)
                except Exception:
                    return {"https": None, "viewport": None}
            https = str(final_url).lower().startswith("https://")
            viewport = 'name="viewport"' in (html or "").lower() or \
                       "name='viewport'" in (html or "").lower()
            return {"https": https, "viewport": viewport}

        data = fetch() if self.cache is None else self.cache.get_or_fetch(
            "site_page", url, fetch
        )
        return data.get("https"), data.get("viewport")

    def _mobile_score(self, url: str) -> Optional[int]:
        target = url if "://" in url else f"https://{url}"

        def unwrap(result) -> Optional[int]:
            # Injected test doubles may return a bare int; the real
            # implementation returns {"scored": True, "score": ...}.
            if isinstance(result, dict):
                return result.get("score")
            return result

        if self.cache is None:
            return unwrap(self._pagespeed(target))
        # get_or_fetch skips caching None, so transport errors retry next
        # run while completed-but-unscorable results are remembered.
        return unwrap(
            self.cache.get_or_fetch("pagespeed", target, lambda: self._pagespeed(target))
        )
