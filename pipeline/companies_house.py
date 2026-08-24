"""Companies House owner lookup — free API, search by name + postcode.

Spec section 5, lookup order:
  1. Companies House match  -> active director(s), company.type = ltd, confidence high
  2. The business's own website "About" page -> confidence low  (Phase 1)
  3. Nothing -> leave null; the letter goes "FAO the Owner / Practice Manager"

company.type matters downstream: ltd companies are corporate subscribers
under PECR (email permissible if they reply), sole traders are not.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Optional

SEARCH_URL = "https://api.company-information.service.gov.uk/search/companies"
OFFICERS_URL = "https://api.company-information.service.gov.uk/company/{number}/officers"

_SUFFIXES = re.compile(
    r"\b(limited|ltd|llp|plc|l\.?t\.?d\.?|company|co|uk|the)\b\.?", re.IGNORECASE
)
_PUNCT = re.compile(r"[^a-z0-9 ]+")


def normalise_company_name(name: str) -> str:
    """Strip legal suffixes and punctuation so names compare fairly."""
    lowered = (name or "").lower()
    lowered = _PUNCT.sub(" ", lowered)
    lowered = _SUFFIXES.sub(" ", lowered)
    return " ".join(lowered.split())


def name_similarity(a: str, b: str) -> float:
    """Token overlap (Jaccard) of two normalised names, 0.0-1.0."""
    ta = set(normalise_company_name(a).split())
    tb = set(normalise_company_name(b).split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def normalise_postcode(pc: Optional[str]) -> str:
    return re.sub(r"\s+", "", (pc or "")).upper()


def format_officer_name(raw: str) -> str:
    """Companies House gives 'SMITH, Sarah Jane' -> 'Sarah Smith'."""
    if "," in raw:
        surname, _, forenames = raw.partition(",")
        first = forenames.strip().split(" ")[0] if forenames.strip() else ""
        surname = surname.strip().title()
        return f"{first.title()} {surname}".strip()
    return raw.title()


def officer_name_parts(raw: str) -> dict:
    """All the name parts, not just the two we print.

    Middle names matter: a director filed as 'MADSEN, John Jay' may be the
    'Jay' named on the practice website. Discarding the middle name loses
    the only evidence that they are the same person.
    """
    if "," in raw:
        surname, _, forenames = raw.partition(",")
        parts = [p for p in forenames.strip().split() if p]
        return {
            "surname": surname.strip().title(),
            "forenames": [p.title() for p in parts],
            "display": format_officer_name(raw),
        }
    words = raw.split()
    return {
        "surname": words[-1].title() if words else "",
        "forenames": [w.title() for w in words[:-1]],
        "display": raw.title(),
    }


@dataclass
class CompaniesHouseClient:
    api_key: Optional[str] = None
    cache: Any = None
    get: Optional[Callable[..., Any]] = None
    timeout: float = 20.0
    min_similarity: float = 0.5

    def _do_get(self, url: str, params: dict) -> Optional[dict]:
        if self.get is not None:
            return self.get(url, params=params)
        if not self.api_key:
            return None
        import requests

        try:
            resp = requests.get(
                url, params=params, auth=(self.api_key, ""), timeout=self.timeout
            )
            if resp.status_code >= 400:
                return None
            return resp.json()
        except Exception:
            return None

    def _search(self, query: str) -> Optional[dict]:
        def fetch():
            return self._do_get(SEARCH_URL, {"q": query, "items_per_page": 20})

        if self.cache is None:
            return fetch()
        return self.cache.get_or_fetch("ch_search", query, fetch)

    def _officers(self, number: str) -> Optional[dict]:
        def fetch():
            return self._do_get(
                OFFICERS_URL.format(number=number), {"items_per_page": 50}
            )

        if self.cache is None:
            return fetch()
        return self.cache.get_or_fetch("ch_officers", number, fetch)

    # -- public API ---------------------------------------------------------

    def lookup(self, name: str, postcode: Optional[str]) -> dict:
        """Return {'owner': {...}, 'company': {...}} for a business.

        Unknown stays None — a wrong owner name on a letter is worse than
        no name at all, so a weak match is discarded rather than guessed.
        """
        blank = {
            "owner": {
                "name": None, "source": None, "confidence": "none",
                "parts": None, "all_directors": [],
            },
            "company": {"number": None, "type": "unknown"},
        }
        results = self._search(name) or {}
        items = results.get("items") or []
        if not items:
            return blank

        target_pc = normalise_postcode(postcode)
        best = None
        best_score = 0.0
        for item in items:
            if item.get("company_status") not in (None, "active"):
                continue
            similarity = name_similarity(name, item.get("title", ""))
            item_pc = normalise_postcode(
                (item.get("address") or {}).get("postal_code")
            )
            # Postcode agreement is the strongest signal; it lets a weaker
            # name match through, and its absence demands a stronger one.
            score = similarity + (0.4 if target_pc and item_pc == target_pc else 0.0)
            if score > best_score:
                best, best_score = item, score

        if best is None or best_score < self.min_similarity:
            return blank

        number = best.get("company_number")
        company_type_raw = (best.get("company_type") or "").lower()
        company_type = "llp" if "llp" in company_type_raw else "ltd"

        owner_name = None
        owner_parts = None
        all_directors = []
        officers = self._officers(number) if number else None
        for officer in (officers or {}).get("items", []) or []:
            if officer.get("resigned_on"):
                continue
            role = (officer.get("officer_role") or "").lower()
            if "director" in role or "member" in role:
                parts = officer_name_parts(officer.get("name", ""))
                all_directors.append(parts)
                if owner_name is None:
                    owner_name = parts["display"]
                    owner_parts = parts

        return {
            "owner": {
                "name": owner_name,
                "source": "companies_house" if owner_name else None,
                "confidence": "high" if owner_name else "none",
                "parts": owner_parts,
                # Several directors is itself a signal: a partnership may not
                # have one obvious person to address.
                "all_directors": [d["display"] for d in all_directors],
            },
            "company": {"number": number, "type": company_type},
        }
