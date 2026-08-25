"""Test API keys with one minimal call each, and explain what went wrong.

Google's console offers key restrictions that are easy to get subtly wrong —
an HTTP-referrer ("Websites") restriction looks sensible but blocks
server-side calls, since Python sends no referrer. The failure then surfaces
much later as a confusing permission error mid-search.

These checks make one small call per key and map the response to a cause and
a fix, so a misconfigured key is caught at setup time.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

PLACES_URL = "https://places.googleapis.com/v1/places:searchText"
PAGESPEED_URL = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
CH_URL = "https://api.company-information.service.gov.uk/search/companies"


@dataclass
class KeyTestResult:
    name: str
    ok: bool
    detail: str
    hint: str = ""

    def __str__(self) -> str:
        mark = "OK  " if self.ok else "FAIL"
        out = f"{mark}  {self.name}: {self.detail}"
        if self.hint:
            out += "\n        " + self.hint.replace("\n", "\n        ")
        return out


def diagnose_google(status: int, body: str) -> tuple[str, str]:
    """Map a Google API error onto a cause and a fix in plain English."""
    text = (body or "").lower()

    if "api_key_http_referrer_blocked" in text or "referer" in text:
        return (
            "the key is restricted to websites (HTTP referrers)",
            "Application restrictions must be 'None' (or an IP address).\n"
            "A 'Websites' restriction checks the browser referrer header,\n"
            "which a Python script does not send, so every call is blocked.",
        )
    if "api_key_ip_address_blocked" in text or "ip address" in text:
        return (
            "the key is restricted to IP addresses that don't include yours",
            "Your IP has probably changed (most home broadband rotates it).\n"
            "Either update the allowed IP, or set restrictions to 'None'.",
        )
    if "api_key_service_blocked" in text or "not authorized to use this api" in text:
        return (
            "the key's API restrictions don't include this API",
            "Edit the key and tick this API under 'API restrictions'.\n"
            "For search, it must be 'Places API (New)', not the legacy one.",
        )
    if "service_disabled" in text or "has not been used in project" in text:
        return (
            "the API isn't enabled on this project",
            "In the console, enable the API for this project, then wait a\n"
            "minute or two before retrying.",
        )
    if "api_key_invalid" in text or "api key not valid" in text:
        return ("the key isn't valid", "Check for a stray space or a truncated paste.")
    if "billing" in text:
        return (
            "billing isn't enabled on the project",
            "Google Places requires a billing account even within the free\n"
            "allowance. Add one, and set a budget cap while you're there.",
        )
    if status == 429 or "quota" in text or "resource_exhausted" in text:
        return ("the quota is exhausted", "Check quotas and budget in the console.")
    if status == 403:
        return (
            "permission denied",
            "Usually a key restriction. Check 'Application restrictions' is\n"
            "'None' and this API is ticked under 'API restrictions'.",
        )
    return (f"unexpected response ({status})", (body or "")[:200])


def test_places(api_key: Optional[str], *, post: Optional[Callable] = None) -> KeyTestResult:
    """One tiny Text Search. Costs a single API call."""
    name = "Google Places"
    if not api_key:
        return KeyTestResult(
            name, False, "no key set",
            "Add one via Setup & configuration on the menu.",
        )

    def default_post(url, json_body, headers):
        import requests

        resp = requests.post(url, json=json_body, headers=headers, timeout=30)
        return resp.status_code, resp.text

    caller = post or default_post
    try:
        status, body = caller(
            PLACES_URL,
            {"textQuery": "cafe in London", "maxResultCount": 1},
            {
                "Content-Type": "application/json",
                "X-Goog-Api-Key": api_key,
                "X-Goog-FieldMask": "places.id",
            },
        )
    except Exception as exc:
        return KeyTestResult(name, False, f"could not reach the API ({exc})",
                             "Check your internet connection or proxy.")

    if status == 200:
        return KeyTestResult(name, True, "key works, search returned a result")
    detail, hint = diagnose_google(status, body)
    return KeyTestResult(name, False, detail, hint)


def test_pagespeed(api_key: Optional[str], *, get: Optional[Callable] = None) -> KeyTestResult:
    name = "PageSpeed Insights"
    if not api_key:
        return KeyTestResult(
            name, False, "no key set (optional)",
            "This API also answers without a key, but on a low anonymous\n"
            "quota that a batch of 25 sites can exhaust — scores then come\n"
            "back empty and those sites are rated 'unknown'.\n"
            "Your Google Places key works here too: tick 'PageSpeed Insights\n"
            "API' under that key's API restrictions and reuse it.",
        )

    def default_get(url, params):
        import requests

        resp = requests.get(url, params=params, timeout=90)
        return resp.status_code, resp.text

    caller = get or default_get
    try:
        status, body = caller(
            PAGESPEED_URL,
            {"url": "https://example.com", "strategy": "mobile",
             "category": "performance", "key": api_key},
        )
    except Exception as exc:
        return KeyTestResult(name, False, f"could not reach the API ({exc})", "")

    if status == 200:
        return KeyTestResult(name, True, "key works, scored a test page")
    detail, hint = diagnose_google(status, body)
    return KeyTestResult(name, False, detail, hint)


def test_companies_house(
    api_key: Optional[str], *, get: Optional[Callable] = None
) -> KeyTestResult:
    name = "Companies House"
    if not api_key:
        return KeyTestResult(
            name, False, "no key set (optional)",
            "Without it, letters open 'FAO the Owner' rather than a name.\n"
            "Free to add.",
        )

    def default_get(url, params, auth):
        import requests

        resp = requests.get(url, params=params, auth=auth, timeout=30)
        return resp.status_code, resp.text

    caller = get or default_get
    try:
        status, body = caller(CH_URL, {"q": "test", "items_per_page": 1}, (api_key, ""))
    except Exception as exc:
        return KeyTestResult(name, False, f"could not reach the API ({exc})", "")

    if status == 200:
        return KeyTestResult(name, True, "key works, search returned a result")
    if status in (401, 403):
        return KeyTestResult(
            name, False, "the key was rejected",
            "Companies House issues separate keys per application, and a\n"
            "'live' key differs from a test/sandbox one. Check you copied\n"
            "the right one.",
        )
    detail = f"unexpected response ({status})"
    return KeyTestResult(name, False, detail, (body or "")[:200])
