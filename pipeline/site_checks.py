"""Lightweight website checks: HTTPS, viewport meta, PageSpeed mobile score,
and a human-readable ``site_verdict``.

The verdict is the single column a human scans (spec section 2):
    none        -> no website at all (best lead)
    social_only -> website is a Facebook page / directory URL (effectively none)
    poor        -> real site but slow on mobile or no HTTPS (visible problem)
    dated       -> real site, ok-ish, but not mobile-friendly / middling score
    fine        -> genuinely good enough; a weak lead

Pure classification is kept separate from the network so it can be tested
without hitting any API.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
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
    mobile_score: Optional[int] = None  # 0-100 PageSpeed mobile performance
    verdict: str = "none"


def verdict_from(
    check: SiteCheck,
    *,
    mobile_score_poor: int = 50,
    mobile_score_dated_max: int = 70,
) -> str:
    """Derive the site_verdict from the raw signals."""
    if not check.has_website:
        return "none"
    if check.is_social_or_directory:
        return "social_only"
    # Real site from here on.
    if check.https is False:
        return "poor"
    if check.mobile_score is not None:
        if check.mobile_score < mobile_score_poor:
            return "poor"
        if check.mobile_score < mobile_score_dated_max or check.viewport is False:
            return "dated"
        return "fine"
    # No mobile score available (PageSpeed failed / no key): fall back to
    # the cheap signals so we never silently rate an unknown site "fine".
    if check.viewport is False:
        return "dated"
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

    def _default_pagespeed(self, url: str) -> Optional[int]:
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
            resp.raise_for_status()
            data = resp.json()
            score = (
                data.get("lighthouseResult", {})
                .get("categories", {})
                .get("performance", {})
                .get("score")
            )
            return round(score * 100) if score is not None else None
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

        # Real site: gather signals (cached where possible).
        assert website is not None
        https, viewport = self._fetch_page_signals(website)
        mobile = self._mobile_score(website)
        result = SiteCheck(
            has_website=True,
            is_social_or_directory=False,
            https=https,
            viewport=viewport,
            mobile_score=mobile,
        )
        result.verdict = verdict_from(
            result,
            mobile_score_poor=self.thresholds.get("mobile_score_poor", 50),
            mobile_score_dated_max=self.thresholds.get("mobile_score_dated_max", 70),
        )
        return result

    def _fetch_page_signals(self, url: str):
        def fetch():
            try:
                status, final_url, html = self._http_get(
                    url if "://" in url else f"https://{url}"
                )
            except Exception:
                # Retry once over http if https failed outright.
                try:
                    status, final_url, html = self._http_get(
                        url.replace("https://", "http://")
                        if "://" in url
                        else f"http://{url}"
                    )
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
        if self.cache is None:
            return self._pagespeed(target)
        return self.cache.get_or_fetch(
            "pagespeed", target, lambda: self._pagespeed(target)
        )
