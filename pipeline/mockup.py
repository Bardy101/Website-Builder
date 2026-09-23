"""A Webflow-ready brief for one prospect: the prompt, the content, the colour.

Webflow's AI Site Builder and AI Assistant take text prompts; it cannot
import arbitrary HTML into the Designer (component libraries that seem to
do so write Webflow's undocumented clipboard format, which is exactly the
kind of fragile dependency this project avoids). So the few-clicks path is
a prompt good enough to paste as-is, plus a build sheet for the pieces you
fill by hand. Everything here is pure — no widgets, no network — and tested.

**Facts only.** The mockup is posted to the business it depicts. Anything
invented — a service they don't offer, a qualification they don't hold, a
testimonial nobody wrote — turns a flattering surprise into an
embarrassment. The prompt carries only what the pipeline verified (Places,
Companies House, their own current website) and tells Webflow to leave a
visible [placeholder] for anything else.

**Their words first.** Where the screenshot step saved their current page,
its headline, section headings and opening paragraphs are offered as the
raw material. A mockup that says what they already say, better presented,
is recognisably theirs.

The fields gathered here are the tier-2 variable schema the spec asks
batch one to discover (section 7): ``mockup.json`` records them per
business, so template-fill can be built against what was actually used.
"""

from __future__ import annotations

import html as html_lib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

# -- layout shapes (spec section 7, tier 2) ------------------------------------

SHAPES = {
    "clinic": ("physio", "osteo", "chiro", "dentist", "dental", "clinic", "therap",
               "podiatr", "optician", "optometr", "vet", "massage", "acupunct",
               "counsell", "psycholog", "pilates", "health"),
    "professional": ("accountant", "bookkeep", "solicitor", "lawyer", "conveyanc",
                     "architect", "surveyor", "financial", "mortgage", "estate agent",
                     "tax", "consultant", "recruit"),
    "trades": ("plumb", "electric", "roof", "builder", "carpent", "joiner", "gardener",
               "landscap", "locksmith", "plaster", "decorat", "painter", "tiler",
               "glaz", "heating", "gas", "kitchen", "bathroom", "fenc", "clean",
               "handyman", "scaffold", "drain", "window"),
}

# Per shape: the sections, the call to action, and the feel. These are the
# four masters the spec names; one Webflow master per shape, not per prospect.
PLANS = {
    "clinic": {
        "cta": "Book an appointment",
        "sections": [
            ("Hero", "Clinic name, a one-line promise built from the facts, the phone "
                     "number, and a 'Book an appointment' button. Room for one wide photo "
                     "of a treatment room or practitioner."),
            ("Treatments", "What they treat, as short cards (3 to 6)."),
            ("About the practice", "Two short paragraphs about the clinic, from the facts "
                                   "and their own wording only, beside a portrait photo."),
            ("Patient reviews", "Up to three real review quotes, attributed to 'Google "
                                "review'."),
            ("Opening hours and location", "Hours as a clean two-column list, beside the "
                                           "address and a map."),
            ("Contact", "Phone, address, and a '[What to expect on your first visit]' "
                        "placeholder note."),
        ],
        "style": "Calm and clinical, not corporate. Reassuring, uncluttered, "
                 "trustworthy — patients are often in pain and older.",
        "palette": "#2c7a7b",
    },
    "professional": {
        "cta": "Book a consultation",
        "sections": [
            ("Hero", "Firm name, a one-line statement of who they help, the phone number "
                     "and a 'Book a consultation' button."),
            ("Services", "What they do, as short cards (3 to 6)."),
            ("About the firm", "Two short paragraphs on the firm, from the facts and "
                               "their own wording only."),
            ("Client reviews", "Up to three real review quotes, attributed to 'Google "
                               "review'."),
            ("Contact and office", "Phone, address, office hours and a map."),
        ],
        "style": "Established and precise. Plenty of whitespace, restrained type, "
                 "confidence without flash — clients are trusting them with money or "
                 "legal matters.",
        "palette": "#1f3a5f",
    },
    "trades": {
        "cta": "Get a quote",
        "sections": [
            ("Hero", "Business name, the trade and the area covered, the phone number "
                     "large and tappable, and a 'Get a quote' button."),
            ("Services", "What they do, as short cards (3 to 6)."),
            ("Why choose us", "Short points drawn only from the facts below (reviews, "
                              "years trading if known)."),
            ("Reviews", "Up to three real review quotes, attributed to 'Google review'."),
            ("Contact", "Phone first, then hours and the area served."),
        ],
        "style": "Practical and direct. Big, clear phone number, strong contrast, "
                 "nothing fussy — customers often have an urgent problem.",
        "palette": "#b7521e",
    },
    "generic": {
        "cta": "Get in touch",
        "sections": [
            ("Hero", "Business name, a one-line promise built from the facts, the phone "
                     "number and a clear call to action."),
            ("What we do", "Their services or products, as short cards (3 to 6)."),
            ("About", "Two short paragraphs about the business, from the facts and "
                      "their own wording only."),
            ("Reviews", "Up to three real review quotes, attributed to 'Google review'."),
            ("Visit or contact", "Hours, address, phone and a map."),
        ],
        "style": "Clean, friendly and local. Generous whitespace, one accent colour.",
        "palette": "#2b5b84",
    },
}


def layout_shape(niche: str, types: Optional[list] = None) -> str:
    """Which of the four masters fits: clinic, professional, trades, generic."""
    haystack = " ".join([niche or ""] + [str(t) for t in (types or [])]).lower()
    for shape, words in SHAPES.items():
        if any(w in haystack for w in words):
            return shape
    return "generic"


# -- their current website, as raw material --------------------------------------

_JUNK = {
    "home", "about", "about us", "contact", "contact us", "blog", "news", "menu",
    "gallery", "faq", "faqs", "book", "book now", "book online", "privacy",
    "privacy policy", "cookies", "cookie policy", "terms", "login", "log in",
    "search", "our team", "team", "testimonials", "reviews", "prices", "pricing",
    "fees", "location", "locations", "find us", "skip to content", "welcome",
    "get in touch", "call us", "email us", "follow us", "useful links", "links",
    "quick links", "opening hours", "our services", "services", "more", "read more",
    "close", "open menu", "toggle navigation", "navigation", "sitemap", "careers",
}
_BOILERPLATE = re.compile(
    r"cookie|privacy|all rights reserved|©|copyright|javascript|browser|"
    r"subscribe|newsletter|sign up|lorem ipsum", re.I)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def extract_site_content(html: str) -> dict:
    """Headline, description, likely services and a few paragraphs.

    Heuristic and deliberately cautious: navigation chrome, cookie banners
    and footers are dropped, and everything found is offered as material to
    reuse — the build sheet shows it, so a wrong pick is visible, not hidden.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "form", "iframe"]):
        tag.decompose()

    def ok(text: str, lo: int, hi: int) -> bool:
        t = text.lower().strip(" :|-–—")
        return (lo <= len(text) <= hi and t not in _JUNK and not t.startswith("welcome")
                and not _BOILERPLATE.search(text))

    title = _clean(soup.title.get_text()) if soup.title else ""
    meta = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
    description = _clean(meta.get("content", "")) if meta else ""

    h1 = next((_clean(h.get_text()) for h in soup.find_all("h1")
               if ok(_clean(h.get_text()), 3, 120)), "")

    seen: set[str] = set()
    services: list[str] = []

    def add(text: str) -> None:
        key = text.lower()
        if key not in seen and ok(text, 3, 60):
            seen.add(key)
            services.append(text)

    for h in soup.find_all(["h2", "h3"]):
        add(_clean(h.get_text()))
    for nav in soup.find_all("nav"):
        for a in nav.find_all("a"):
            add(_clean(a.get_text()))

    paragraphs = []
    for p in soup.find_all("p"):
        text = _clean(p.get_text())
        if ok(text, 60, 420) and text not in paragraphs:
            paragraphs.append(text)
        if len(paragraphs) == 3:
            break

    return {
        "title": title[:120],
        "description": description[:300],
        "headline": h1,
        "services": services[:8],
        "paragraphs": paragraphs,
    }


# -- their colour -------------------------------------------------------------------

def brand_colour(screenshot: Optional[Path]) -> Optional[str]:
    """The most-used real colour on their current site, as #rrggbb, or None.

    Near-white, near-black and greys are skipped — they are background and
    text, not brand. Needs Pillow; without it (or without a screenshot) the
    caller uses the layout shape's safe palette.
    """
    if screenshot is None or not Path(screenshot).is_file():
        return None
    try:
        import colorsys

        from PIL import Image
    except ImportError:
        return None
    try:
        with Image.open(screenshot) as im:
            small = im.convert("RGB").resize((160, 100))
    except Exception:  # noqa: BLE001 — an unreadable image means no colour, not a crash
        return None
    counts: dict[tuple, int] = {}
    raw = small.tobytes()  # RGB triples; getdata() is deprecated in newer Pillow
    for i in range(0, len(raw), 3):
        r, g, b = raw[i], raw[i + 1], raw[i + 2]
        # Bucket to 16 levels a channel, so anti-aliased edges join their colour.
        key = (r // 16 * 16 + 8, g // 16 * 16 + 8, b // 16 * 16 + 8)
        counts[key] = counts.get(key, 0) + 1
    for (r, g, b), n in sorted(counts.items(), key=lambda kv: -kv[1]):
        _h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        if s >= 0.3 and 0.2 <= v <= 0.95 and n >= 40:
            return f"#{r:02x}{g:02x}{b:02x}"
    return None


def text_on(hex_colour: str) -> str:
    """White or near-black, whichever reads better on the colour."""
    r, g, b = (int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5))
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return "#ffffff" if lum < 0.55 else "#1d2127"


# -- the brief ------------------------------------------------------------------------

PLACEHOLDER = "[{}]"

# A review that carries a complaint ("…booking by phone only is a pain
# though") is true, but not something to print on the business's own
# concept. Such quotes are left out; the build sheet still has the rest.
_COMPLAINT = re.compile(
    r"\b(though|however|unfortunately|shame|disappoint\w*|only (downside|complaint|"
    r"issue|problem|negative)|would have been|could be better)\b", re.I)


def pick_reviews(reviews: list, limit: int = 3) -> list[str]:
    """Up to ``limit`` quotable reviews: 4 stars or more, a readable length,
    no complaint in them; five-star ones first. Verbatim, only whitespace
    tidied."""
    usable = []
    for i, review in enumerate(reviews or []):
        text = _clean(review.get("text") or "")
        rating = review.get("rating")
        if not 30 <= len(text) <= 320 or (rating is not None and rating < 4):
            continue
        if _COMPLAINT.search(text):
            continue
        usable.append((-(rating or 0), i, text))
    return [text for _r, _i, text in sorted(usable)[:limit]]


@dataclass
class Brief:
    place_id: str
    name: str
    shape: str
    accent: str
    accent_source: str
    accent_text: str
    facts: list[tuple[str, str]] = field(default_factory=list)
    reviews: list[str] = field(default_factory=list)
    site: dict = field(default_factory=dict)
    sections: list[tuple[str, str]] = field(default_factory=list)
    placeholders: list[str] = field(default_factory=list)
    banner: str = ""
    one_liner: str = ""
    prompt: str = ""


def _niche_phrase(niche: str) -> str:
    n = (niche or "").strip()
    return n[:1].upper() + n[1:] if n else "Local business"


def build_brief(row: dict, business: Optional[dict] = None, *,
                site_html: Optional[str] = None, screenshot: Optional[Path] = None,
                your_business: str = "") -> Brief:
    """Everything needed to build this prospect's mockup in Webflow."""
    b = business or {}
    address = b.get("address") or {}
    name = row.get("name") or b.get("name") or "(unnamed)"
    niche = b.get("niche") or ""
    town = row.get("town") or address.get("town") or ""
    shape = layout_shape(niche, b.get("types"))
    plan = PLANS[shape]

    # Only their own site speaks for them: a Facebook or directory page's
    # colours and wording belong to Facebook or the directory.
    from .site_checks import classify_url

    website = row.get("website_url") or row.get("website") or b.get("website") or ""
    kind = classify_url(website)
    own_site = kind == "real"
    sampled = brand_colour(screenshot) if own_site else None
    accent = sampled or plan["palette"]
    if sampled:
        source = "sampled from their current website"
    elif own_site:
        source = f"a safe {shape} palette — no screenshot of their site to sample"
    else:
        source = f"a safe {shape} palette — they have no website of their own"
    brief = Brief(
        place_id=row.get("place_id") or b.get("place_id") or "",
        name=name, shape=shape, accent=accent, accent_source=source,
        accent_text=text_on(accent),
    )

    placeholders: list[str] = []

    def fact(label: str, value, missing: str = "") -> None:
        value = "" if value is None else str(value).strip()
        if value:
            brief.facts.append((label, value))
        elif missing:
            brief.facts.append((label, PLACEHOLDER.format(missing)))
            placeholders.append(missing)

    phone = row.get("phone") or b.get("phone")
    full_address = row.get("address") or address.get("formatted")
    rating = row.get("rating") or b.get("rating")
    count = row.get("review_count") or b.get("review_count")
    hours = b.get("opening_hours") or []

    fact("Business name", name)
    fact("What they are", f"{_niche_phrase(niche)} in {town}" if town else _niche_phrase(niche))
    fact("Address", full_address, "Add their address")
    fact("Phone", phone, "Add their phone number")
    if rating and count:
        fact("Google rating", f"{rating} stars from {count} Google reviews")
    fact("Opening hours", "; ".join(hours), "Add their opening hours")
    contact = b.get("site_contact") or {}
    if contact.get("name"):
        fact("Named on their website", contact["name"]
             + (f", {contact['role']}" if contact.get("role") else ""))

    brief.reviews = pick_reviews(b.get("reviews") or [])
    if not brief.reviews:
        placeholders.append("Add up to three review quotes from their Google profile")

    if site_html and own_site:
        brief.site = extract_site_content(site_html)
    if not brief.site.get("services"):
        fact("Services", "", "List their services — from their current site or "
                             "Google profile")

    brief.sections = list(plan["sections"])
    brief.banner = (f"Design concept for {name}, prepared by "
                    f"{your_business or PLACEHOLDER.format('your business name')}. "
                    "Not a live website.")
    rating_bit = (f", rated {rating} stars from {count} Google reviews"
                  if rating and count else "")
    brief.one_liner = f"{_niche_phrase(niche)} in {town or 'their area'}{rating_bit}."
    brief.placeholders = placeholders
    brief.prompt = _prompt(brief, plan)
    return brief


def _prompt(brief: Brief, plan: dict) -> str:
    """The text to paste into Webflow's AI Site Builder, as-is."""
    lines = [
        f"Build a single-page website for {brief.name}. {brief.one_liner}",
        "",
        "This is a design concept to show the owner. Use ONLY the facts below. "
        "Where something is not given, leave a clearly marked placeholder in "
        "[square brackets] — never invent services, qualifications, prices, awards, "
        "staff names or testimonials. A section with no facts to fill it keeps its "
        "layout, with [placeholder] text saying what belongs there.",
        "",
        "Structure — one page only, no blog, no CMS collections:",
        f"0. A thin banner across the very top reading: \"{brief.banner}\"",
        "1. Sticky header: business name on the left; phone number and a "
        f"'{plan['cta']}' button on the right.",
    ]
    for i, (title, what) in enumerate(brief.sections, start=2):
        lines.append(f"{i}. {title}: {what}")
    lines.append(f"{len(brief.sections) + 2}. Simple footer with the address and phone.")
    lines += ["", "Facts:"]
    lines += [f"- {label}: {value}" for label, value in brief.facts]
    if brief.reviews:
        lines.append("- Real review quotes (use verbatim, attribute each to 'Google review'):")
        lines += [f'  "{q}"' for q in brief.reviews]
    site = brief.site or {}
    if any(site.get(k) for k in ("headline", "services", "paragraphs", "description")):
        lines += ["", "From their current website — reuse this wording, tidied up, "
                      "rather than writing new claims:"]
        if site.get("headline"):
            lines.append(f"- Their headline: {site['headline']}")
        if site.get("description"):
            lines.append(f"- Their description: {site['description']}")
        if site.get("services"):
            lines.append("- Sections/services they list: " + "; ".join(site["services"]))
        for para in site.get("paragraphs") or []:
            lines.append(f"- They say: \"{para}\"")
    lines += [
        "",
        f"Style: {plan['style']} Accent colour {brief.accent}"
        f"{' (from their current branding)' if brief.accent_source.startswith('sampled') else ''}"
        " used sparingly for buttons and links; otherwise white and a soft neutral. Two fonts "
        "at most. Body text at least 18px. High contrast. Mobile-first. Generous "
        "whitespace. No carousels, no stock-photo clichés — label each image area with "
        "the photo that belongs there.",
    ]
    return "\n".join(lines)


# -- files -----------------------------------------------------------------------------

def render_html(brief: Brief, *, screenshot_uri: str = "") -> str:
    """The build sheet: a self-contained page with a Copy button per block."""
    e = html_lib.escape
    blocks = [("The prompt — paste into Webflow's AI Site Builder", brief.prompt, True),
              ("One-line description", brief.one_liner, False),
              ("Top banner text (required on every concept)", brief.banner, False)]
    for label, value in brief.facts:
        blocks.append((label, value, False))
    for i, quote in enumerate(brief.reviews, 1):
        blocks.append((f"Review quote {i}", quote, False))
    site = brief.site or {}
    if site.get("services"):
        blocks.append(("Services they list", "\n".join(site["services"]), False))
    for i, para in enumerate(site.get("paragraphs") or [], 1):
        blocks.append((f"Their wording {i}", para, False))

    cards = []
    for i, (label, value, big) in enumerate(blocks):
        cards.append(
            f'<section class="card{" big" if big else ""}"><div class="row"><h3>{e(label)}'
            f'</h3><button onclick="copyBlock({i}, this)">Copy</button></div>'
            f'<textarea id="b{i}" rows="{18 if big else max(2, min(8, value.count(chr(10)) + 2))}"'
            f'>{e(value)}</textarea></section>')
    todo = "".join(f"<li>{e(p)}</li>" for p in brief.placeholders) or \
        "<li>Nothing — every fact was found.</li>"
    shot = (f'<img src="{screenshot_uri}" alt="Their current website">'
            if screenshot_uri else "<p class='muted'>No screenshot of a current site.</p>")
    steps = "".join(f"<li>{s}</li>" for s in (
        "Copy the prompt (it is already on your clipboard if you came from the app).",
        "In Webflow, start a new site with the <b>AI Site Builder</b> and paste it.",
        "Work through <b>Fill in by hand</b>: replace each [placeholder] you can.",
        f"Set the accent colour to <code>{e(brief.accent)}</code> if the builder changed it.",
        "Keep it to one page with no CMS Collections, so the export matches the live site.",
    ))
    master = (
        f"Once you have a <b>{e(brief.shape)}</b> master you like, skip the AI: duplicate "
        "the master and paste each block on this page into its matching element. It is quicker, "
        "every mockup gets the design you chose, and it doesn't use up a staging site — "
        "Core allows ten, and each AI-built site takes one until you delete it.")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Webflow brief — {e(brief.name)}</title>
<style>
:root {{ --accent: {brief.accent}; --on: {brief.accent_text}; --bg:#f3f4f6; --card:#fff;
  --text:#1d2127; --muted:#5d6673; --border:#d6dbe1; }}
* {{ box-sizing: border-box; }}
body {{ margin:0; font:15px/1.5 system-ui, "Segoe UI", sans-serif; background:var(--bg); color:var(--text); }}
header {{ background:#1f2d3d; color:#fff; padding:18px 28px; }}
header h1 {{ margin:0; font-size:22px; }} header p {{ margin:4px 0 0; color:#9fb0c3; }}
main {{ display:grid; grid-template-columns: minmax(0,1fr) 360px; gap:20px; padding:20px 28px; max-width:1300px; }}
@media (max-width: 900px) {{ main {{ grid-template-columns: 1fr; }} }}
.card {{ background:var(--card); border:1px solid var(--border); border-radius:8px; padding:12px 14px; margin-bottom:12px; }}
.row {{ display:flex; justify-content:space-between; align-items:center; gap:10px; }}
h3 {{ margin:0 0 6px; font-size:14px; }} h2 {{ font-size:15px; margin:0 0 8px; }}
textarea {{ width:100%; border:1px solid var(--border); border-radius:6px; padding:8px; font:13px/1.45 ui-monospace, Consolas, monospace; resize:vertical; }}
.big textarea {{ font-size:13px; }}
button {{ background:var(--accent); color:var(--on); border:0; border-radius:6px; padding:6px 14px; font-weight:600; cursor:pointer; }}
button.done {{ background:#1a7f37; color:#fff; }}
.swatch {{ height:64px; border-radius:6px; background:var(--accent); color:var(--on); display:flex; align-items:center; justify-content:center; font-weight:700; }}
.muted {{ color:var(--muted); }} img {{ width:100%; border:1px solid var(--border); border-radius:6px; }}
ol, ul {{ padding-left:20px; margin:6px 0; }} a.open {{ display:inline-block; margin-top:8px; }}
</style></head><body>
<header><h1>{e(brief.name)} — Webflow mockup brief</h1>
<p>Layout: {e(brief.shape)} · everything below is from verified sources; nothing invented</p></header>
<main><div>
{''.join(cards)}
</div><aside>
<section class="card"><h2>Steps</h2><ol>{steps}</ol>
<a class="open" href="https://webflow.com/dashboard" target="_blank" rel="noopener">Open Webflow ↗</a>
<p class="muted">{master}</p></section>
<section class="card"><h2>Accent colour</h2><div class="swatch">{e(brief.accent)}</div>
<p class="muted">{e(brief.accent_source)}</p></section>
<section class="card"><h2>Fill in by hand</h2><ul>{todo}</ul></section>
<section class="card"><h2>Their current web presence</h2>{shot}</section>
</aside></main>
<script>
function copyBlock(i, button) {{
  const box = document.getElementById("b" + i);
  const done = () => {{ const t = button.textContent; button.textContent = "Copied";
    button.classList.add("done"); setTimeout(() => {{ button.textContent = t;
    button.classList.remove("done"); }}, 1400); }};
  if (navigator.clipboard && window.isSecureContext) {{
    navigator.clipboard.writeText(box.value).then(done, () => {{ box.select(); document.execCommand("copy"); done(); }});
  }} else {{ box.select(); document.execCommand("copy"); done(); }}
}}
</script></body></html>
"""


def write(folder: Path, brief: Brief, *, screenshot: Optional[Path] = None) -> dict:
    """Write prompt.txt, mockup.json and brief.html into ``folder``."""
    import base64

    folder.mkdir(parents=True, exist_ok=True)
    uri = ""
    if screenshot is not None and Path(screenshot).is_file():
        uri = "data:image/png;base64," + base64.b64encode(Path(screenshot).read_bytes()).decode()
    paths = {
        "prompt": folder / "prompt.txt",
        "json": folder / "mockup.json",
        "html": folder / "brief.html",
    }
    paths["prompt"].write_text(brief.prompt + "\n", encoding="utf-8")
    paths["json"].write_text(json.dumps(asdict(brief), indent=2, ensure_ascii=False),
                             encoding="utf-8")
    paths["html"].write_text(render_html(brief, screenshot_uri=uri), encoding="utf-8")
    return paths
