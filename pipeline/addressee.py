"""Who should the letter be addressed to?

Two sources disagree more often than you'd expect:

  Companies House  the person who legally owns the business
  the website      the person who actually runs it day to day

They are frequently the same person under different names — a director filed
as "John Jay Madsen" is the "Jay" on the website — and sometimes genuinely
different people, where the registered director is a spouse, an accountant's
nominee, or a parent company officer.

This module reconciles them and *recommends*. It never decides silently: the
verdict and the reason both reach the shortlist, and the cull is where a
human settles it. That is exactly the judgement the spec says the cull exists
to capture.
"""

from __future__ import annotations

from typing import Optional

# Verdicts, and what each means for the letter.
AGREE = "agree"            # same person, both sources — address with confidence
LIKELY_SAME = "likely_same"  # nickname or middle-name match — probably one person
DIFFER = "differ"          # two different names — a human should look
OWNER_ONLY = "owner_only"  # register only; nobody named on the site
SITE_ONLY = "site_only"    # site only; no company match (often a sole trader)
NONE = "none"              # nothing found — "FAO the Owner / Practice Manager"

FALLBACK_SALUTATION = "FAO the Owner / Practice Manager"


def _tokens(name: Optional[str]) -> set[str]:
    return {w.lower() for w in (name or "").split() if w}


def compare_names(owner: dict, site_contact: Optional[dict]) -> str:
    """Decide how the two sources relate."""
    owner_name = (owner or {}).get("name")
    site_name = (site_contact or {}).get("name")

    if not owner_name and not site_name:
        return NONE
    if owner_name and not site_name:
        return OWNER_ONLY
    if site_name and not owner_name:
        return SITE_ONLY

    owner_tokens = _tokens(owner_name)
    site_tokens = _tokens(site_name)
    if owner_tokens == site_tokens:
        return AGREE

    # Surname agreement plus any shared forename is a strong same-person signal.
    parts = (owner or {}).get("parts") or {}
    surname = (parts.get("surname") or "").lower()
    forenames = {f.lower() for f in (parts.get("forenames") or [])}

    if surname and surname in site_tokens:
        # Same surname. A shared forename — including a middle name the
        # display name dropped — makes it the same person.
        if forenames & site_tokens:
            return AGREE
        return LIKELY_SAME

    if forenames & site_tokens:
        # A middle name matching the site's first name: "John Jay Madsen"
        # published as "Jay". Common, and easy to miss.
        return LIKELY_SAME

    return DIFFER


def decide(
    owner: Optional[dict],
    site_contact: Optional[dict],
    *,
    company_type: str = "unknown",
) -> dict:
    """Recommend an addressee, with the reasoning attached.

    Returns ``address_to`` (the name to use), ``salutation``, a ``verdict``
    from the constants above, and a one-line ``note`` for the human.
    """
    owner = owner or {}
    verdict = compare_names(owner, site_contact)
    owner_name = owner.get("name")
    site_name = (site_contact or {}).get("name")
    site_role = (site_contact or {}).get("role")

    if verdict == NONE:
        return {
            "verdict": NONE,
            "address_to": None,
            "salutation": FALLBACK_SALUTATION,
            "note": "No named contact found in either source.",
            "needs_human": False,
        }

    if verdict in (AGREE, LIKELY_SAME):
        # Prefer the website's rendering: it is what they call themselves.
        name = site_name or owner_name
        note = (
            "Register and website agree."
            if verdict == AGREE
            else f"Website says '{site_name}', register says '{owner_name}' — "
                 "almost certainly the same person."
        )
        return {
            "verdict": verdict,
            "address_to": name,
            "salutation": f"Dear {name.split()[0]},",
            "note": note,
            "needs_human": verdict == LIKELY_SAME,
        }

    if verdict == OWNER_ONLY:
        return {
            "verdict": OWNER_ONLY,
            "address_to": owner_name,
            "salutation": f"Dear {owner_name.split()[0]},",
            "note": "Director from Companies House; nobody named on the website.",
            "needs_human": False,
        }

    if verdict == SITE_ONLY:
        hint = (
            " Likely a sole trader — never email uninvited."
            if company_type in ("unknown", "sole_trader")
            else ""
        )
        return {
            "verdict": SITE_ONLY,
            "address_to": site_name,
            "salutation": f"Dear {site_name.split()[0]},",
            "note": f"Named on the website as {site_role}; no company match.{hint}",
            "needs_human": True,
        }

    # DIFFER — the interesting case, and the one worth a human's ten seconds.
    return {
        "verdict": DIFFER,
        # Default to the website's person: they run the place day to day and
        # a website is their problem to solve. The human can override.
        "address_to": site_name,
        "salutation": f"Dear {site_name.split()[0]},",
        "note": (
            f"Website names {site_name} ({site_role}); register names "
            f"{owner_name} as director. Different people — decide who "
            "actually owns the website decision."
        ),
        "needs_human": True,
    }
