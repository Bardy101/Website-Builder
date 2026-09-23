"""Everything worth knowing about one prospect, arranged for reading.

The window's detail pane draws this; nothing here touches a widget, so what
the pane says is unit-tested. It replaces the reason for opening the CSV in a
spreadsheet: every column the shortlist carries, plus what only the business
record holds — the score's itemised breakdown, review quotes, opening hours,
and any lookup that failed.

Sources, in order of preference: the business record (``business.json``)
for anything it has, and the shortlist row for the rest — so a batch from
before a field existed still shows what it can, and a missing record (an old
combined batch, a test) degrades to the row rather than to nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# What each scoring signal means, in the words you'd use about a business.
SIGNAL_LABELS = {
    "no_website": "No website at all",
    "social_or_directory_only": "Only a social or directory page",
    "no_viewport": "Not built for phones (no viewport tag)",
    "no_media_queries": "Layout doesn't adapt to screen size",
    "copyright_stale": "Copyright year out of date",
    "table_layout": "Built with table layout (2000s-era)",
    "no_https": "No HTTPS — browsers warn 'not secure'",
    "flash": "Uses Flash",
    "established_and_reputable": "Established and well reviewed",
    "too_few_reviews": "Very few reviews",
    "recent_review_activity": "Reviewed in the last 90 days",
    "modern_platform": "On a modern managed platform",
}

VERDICT_LABELS = {
    "none": "No website",
    "social_only": "Social page only",
    "dated": "Dated site",
    "poor": "Weak site",
    "fine": "Site looks fine",
    "unknown": "Couldn't check the site",
}

# Which verdicts are good news for you (a sale to make) — for colouring.
VERDICT_TONE = {
    "none": "good", "social_only": "good", "dated": "good",
    "poor": "ok", "fine": "bad", "unknown": "muted",
}


@dataclass
class Details:
    """One prospect, ready to draw."""

    place_id: str
    name: str
    subtitle: str
    score: str
    verdict: str
    verdict_label: str
    verdict_tone: str
    website: str = ""
    maps_url: str = ""
    # (label, value) pairs, grouped under a heading, in display order.
    sections: list[tuple[str, list[tuple[str, str]]]] = field(default_factory=list)
    # Score breakdown as (label, points), largest effect first.
    breakdown: list[tuple[str, int]] = field(default_factory=list)
    quotes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _tri(value) -> str:
    """Yes / No / blank-for-unknown, never a guess."""
    if value is None or value == "":
        return ""
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("yes", "true", "1"):
            return "Yes"
        if v in ("no", "false", "0"):
            return "No"
        return value
    return "Yes" if value else "No"


def _first(*values) -> str:
    for v in values:
        if v not in (None, ""):
            return str(v)
    return ""


def maps_link(place_id: str, maps_url: str = "") -> str:
    """A Google Maps link that works even when the row carries none."""
    if maps_url:
        return maps_url
    if place_id:
        return f"https://www.google.com/maps/place/?q=place_id:{place_id}"
    return ""


def build(row: dict, business: Optional[dict] = None, *,
          excluded: Optional[dict] = None) -> Details:
    """Assemble the detail view for one shortlist row.

    ``excluded`` is an excluded.csv row, for a business the rules dropped;
    it has far fewer fields, and the view says why it was dropped first.
    """
    b = business or {}
    address = b.get("address") or {}
    site = b.get("site_score") or {}
    stale = b.get("staleness") or {}
    owner = b.get("owner") or {}
    company = b.get("company") or {}
    contact = b.get("site_contact") or {}
    addressee = b.get("addressee") or {}

    place_id = _first(row.get("place_id"), b.get("place_id"))
    name = _first(row.get("name"), b.get("name"), "(unnamed)")
    town = _first(row.get("town"), address.get("town"))
    niche = _first(b.get("niche"))
    verdict = _first(row.get("site_verdict"), site.get("verdict"), "unknown")
    website = _first(row.get("website_url"), row.get("website"), b.get("website"))

    d = Details(
        place_id=place_id,
        name=name,
        subtitle=" · ".join(x for x in (niche, town) if x),
        score=_first(row.get("lead_score"), b.get("lead_score")),
        verdict=verdict,
        verdict_label=VERDICT_LABELS.get(verdict, verdict),
        verdict_tone=VERDICT_TONE.get(verdict, "muted"),
        website=website,
        maps_url=maps_link(place_id, _first(row.get("maps_url"), b.get("maps_url"))),
    )

    if excluded is not None:
        reason = excluded.get("reason") or ""
        detail = excluded.get("detail") or ""
        d.warnings.append(
            "Excluded by the rules: " + reason.replace("_", " ")
            + (f" — {detail}" if detail else ""))
        d.sections.append(("What was known", [
            ("Reviews", _first(excluded.get("review_count"))),
            ("Last review", _first(excluded.get("last_review"))),
            ("Site", VERDICT_LABELS.get(excluded.get("site_verdict") or "", "")),
        ]))
        d.sections = [(h, [(k, v) for k, v in rows if v]) for h, rows in d.sections]
        return d

    # -- who to write to ----------------------------------------------------
    owner_name = _first(row.get("owner_name"), owner.get("name"))
    site_name = _first(row.get("site_contact"))
    if not site_name and contact.get("name"):
        site_name = contact["name"] + (f" ({contact['role']})" if contact.get("role") else "")
    d.sections.append(("Who to write to", [
        ("Address to", _first(row.get("address_to"), addressee.get("address_to"))),
        ("Companies House", owner_name
         + (f" — {company.get('type') or row.get('company_type')}"
            if owner_name and (company.get("type") or row.get("company_type")) not in (None, "", "unknown")
            else "")),
        ("On their website", site_name),
        ("Why", _first(addressee.get("note"))),
    ]))
    if addressee.get("needs_human") or row.get("contact_check") in ("differ", "likely_same"):
        d.warnings.append("The register and the website name different people — "
                          "check who actually runs it before addressing the letter.")

    # -- contact details ----------------------------------------------------
    d.sections.append(("Contact", [
        ("Phone", _first(row.get("phone"), b.get("phone"))),
        ("Address", _first(row.get("address"), address.get("formatted"))),
        ("Postcode", _first(row.get("postcode"), address.get("postcode"))),
        ("Website", website),
        # A signal about who to address, never a mailing list: unsolicited
        # email is not permitted, and for sole traders it is unlawful.
        ("Email on site", _first(row.get("contact_email"), contact.get("email"))
         + (" — to identify the addressee, not to email"
            if _first(row.get("contact_email"), contact.get("email")) else "")),
    ]))

    # -- reviews ------------------------------------------------------------
    rating = _first(row.get("rating"), b.get("rating"))
    count = _first(row.get("review_count"), b.get("review_count"))
    d.sections.append(("Reviews", [
        ("Rating", f"{rating} ★" if rating else ""),
        ("Reviews", count),
        ("Last review", _first(row.get("recent_review_date"))),
    ]))
    for review in (b.get("reviews") or [])[:3]:
        text = (review.get("text") or "").strip()
        if text:
            d.quotes.append(text if len(text) <= 240 else text[:237].rstrip() + "…")

    # -- the website, measured ---------------------------------------------
    copyright_year = _first(row.get("copyright_year"), stale.get("copyright_year"))
    d.sections.append(("The website", [
        ("Verdict", d.verdict_label),
        ("Works on phones", _tri(_first(row.get("has_viewport"), stale.get("has_viewport")))),
        ("Adapts to screen", _tri(_first(row.get("has_media_queries"),
                                         stale.get("has_media_queries")))),
        ("HTTPS", _tri(_first(row.get("https"), site.get("https")))),
        ("Copyright year", copyright_year),
        ("Table layout", _tri(_first(row.get("uses_table_layout"),
                                     stale.get("uses_table_layout")))),
        ("Platform", _first(row.get("platform_hint"), stale.get("platform_hint"))),
        ("Mobile speed score", _first(row.get("mobile_score"), site.get("mobile_score"))),
    ]))

    # -- opening hours ------------------------------------------------------
    hours = b.get("opening_hours") or []
    if hours:
        d.sections.append(("Opening hours", [("", "\n".join(hours))]))

    # -- why this score ------------------------------------------------------
    for key, points in sorted((b.get("score_breakdown") or {}).items(),
                              key=lambda kv: -abs(kv[1])):
        d.breakdown.append((SIGNAL_LABELS.get(key, key.replace("_", " ")), int(points)))

    # -- anything that went wrong --------------------------------------------
    for step, error in (b.get("enrich_errors") or {}).items():
        d.warnings.append(f"The {step} failed, so that part is blank: {error}")

    # Drop empty fields and then empty sections: blank means unknown, and a
    # screen full of blank labels reads as broken rather than as unknown.
    d.sections = [(h, [(k, v) for k, v in rows if v]) for h, rows in d.sections]
    d.sections = [(h, rows) for h, rows in d.sections if rows]
    return d
