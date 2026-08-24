"""Step 2 of the spec's owner lookup: the business's own About page.

Spec section 5 orders the lookup Companies House -> website "About" page ->
nothing. Companies House tells you who *legally owns* the business; the
website tells you who *actually runs it day to day*. They are often the same
person, and when they are not, the difference decides who the letter should
be addressed to.

Everything found here is confidence "low" by design — it is pattern matching
over someone's marketing copy, not a register. It is offered to the human
during the cull, never silently trusted.

Only pages the business has published publicly are read, robots.txt is
respected, and results are cached for 30 days like every other lookup.

The emails found here are a *signal about who to address a letter to*. They
are not a mailing list: the spec is explicit that unsolicited email is not
permitted, and for sole traders it is unlawful under PECR.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Callable, Optional
from urllib.parse import urljoin, urlparse

# Paths worth trying for a named human, in rough order of likelihood.
CONTACT_PATHS = [
    "/about", "/about-us", "/our-team", "/team", "/meet-the-team",
    "/staff", "/people", "/who-we-are", "/contact", "/contact-us",
]

# Roles that indicate someone who could say yes to a new website.
DECISION_ROLES = [
    "owner", "founder", "co-founder", "proprietor", "principal",
    "managing director", "clinical director", "clinic director",
    "practice manager", "clinic manager", "practice owner",
    "director", "partner", "head of practice", "lead physiotherapist",
    "lead clinician", "senior partner",
]

# Mailbox names that belong to the business, not to a person.
GENERIC_MAILBOXES = {
    "info", "hello", "hi", "admin", "enquiries", "enquiry", "contact",
    "reception", "bookings", "booking", "appointments", "office", "mail",
    "team", "support", "help", "accounts", "sales", "no-reply", "noreply",
    "privacy", "dpo", "webmaster", "post",
}

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# Two or three capitalised words: "Jay Patel", "Sarah Jane Smith".
# The separator excludes newlines because extract_text joins each HTML text
# node with one: without that, a name can be assembled from the tail of one
# element and the head of the next ("...Physio" + "Meet the team").
NAME_RE = re.compile(
    r"\b([A-Z][a-z]{1,15}(?:[^\S\n]+[A-Z][a-z]{1,15}){1,2})\b"
)

# Words that make a capitalised phrase a job title, not a person's name.
ROLE_WORDS = {
    "practice", "manager", "director", "owner", "founder", "principal",
    "partner", "clinical", "clinic", "senior", "head", "lead", "proprietor",
    "physiotherapist", "physio", "clinician", "therapist", "team", "staff",
    "managing", "meet", "our", "the", "contact", "about", "us", "home",
}

# Capitalised phrases that look like names but aren't.
NOT_NAMES = {
    "physiotherapy", "sports massage", "our team", "meet the", "contact us",
    "about us", "book now", "get in", "read more", "find us", "opening hours",
    "privacy policy", "terms conditions", "all rights", "cookie policy",
    "sports injury", "back pain", "shoulder pain", "knee pain",
}


class _TextExtractor(HTMLParser):
    """Strip tags, keeping text and mailto targets."""

    def __init__(self) -> None:
        super().__init__()
        self.chunks: list[str] = []
        self.mailtos: list[str] = []
        self.links: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        attrs_d = dict(attrs)
        href = attrs_d.get("href") or ""
        if href.lower().startswith("mailto:"):
            self.mailtos.append(href[7:].split("?")[0])
        elif href:
            self.links.append(href)

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip and data.strip():
            self.chunks.append(data.strip())

    @property
    def text(self) -> str:
        return "\n".join(self.chunks)


def extract_text(html: str) -> tuple[str, list[str], list[str]]:
    parser = _TextExtractor()
    try:
        parser.feed(html or "")
    except Exception:
        return "", [], []
    return parser.text, parser.mailtos, parser.links


def classify_email(address: str) -> str:
    """'personal' if the mailbox looks like a person, else 'generic'."""
    local = address.split("@")[0].lower()
    stem = re.split(r"[._-]", local)[0]
    if local in GENERIC_MAILBOXES or stem in GENERIC_MAILBOXES:
        return "generic"
    return "personal"


def find_emails(html: str) -> list[dict]:
    text, mailtos, _ = extract_text(html)
    found: dict[str, dict] = {}
    for address in mailtos + EMAIL_RE.findall(text or ""):
        address = address.strip().rstrip(".,;")
        if not address or address.lower() in found:
            continue
        # Skip image filenames and similar noise picked up by the regex.
        if any(address.lower().endswith(ext) for ext in (".png", ".jpg", ".gif")):
            continue
        found[address.lower()] = {
            "email": address,
            "kind": classify_email(address),
        }
    return list(found.values())


def _looks_like_name(candidate: str) -> bool:
    lowered = candidate.lower()
    if lowered in NOT_NAMES or any(bad in lowered for bad in NOT_NAMES):
        return False
    words = [w.lower() for w in candidate.split()]
    # "Practice Manager" and "Lead Physiotherapist" are titles, not people.
    if any(w in ROLE_WORDS for w in words):
        return False
    # A real name rarely repeats a word.
    return len(set(words)) == len(words)


def find_people(html: str, *, window: int = 60) -> list[dict]:
    """Names appearing near a decision-making role word.

    Deliberately conservative: a name is only returned when a role word sits
    within ``window`` characters of it, and the pair is reported with the role
    so a human can judge it.
    """
    text, _, _ = extract_text(html)
    if not text:
        return []
    lowered = text.lower()
    people: list[dict] = []
    seen: set[tuple[str, str]] = set()

    for role in DECISION_ROLES:
        start = 0
        while True:
            idx = lowered.find(role, start)
            if idx == -1:
                break
            start = idx + len(role)
            segment = text[max(0, idx - window): idx + len(role) + window]
            for match in NAME_RE.findall(segment):
                if not _looks_like_name(match):
                    continue
                key = (match.lower(), role)
                if key in seen:
                    continue
                seen.add(key)
                people.append({"name": match, "role": role})
    return people


def choose_site_contact(people: list[dict], emails: list[dict]) -> Optional[dict]:
    """Pick the most senior-looking named person, and pair an email if one fits."""
    if not people:
        return None
    # DECISION_ROLES is ordered by seniority, so prefer the earliest match.
    ranked = sorted(people, key=lambda p: DECISION_ROLES.index(p["role"]))
    best = ranked[0]

    matched_email = None
    first_name = best["name"].split()[0].lower()
    surname = best["name"].split()[-1].lower()
    for entry in emails:
        local = entry["email"].split("@")[0].lower()
        if first_name in local or surname in local:
            matched_email = entry["email"]
            break

    # Other people named on the site, each once — the same person is often
    # matched by several role words and across several pages.
    others: list[str] = []
    for person in ranked[1:]:
        if person["name"] != best["name"] and person["name"] not in others:
            others.append(person["name"])

    return {
        "name": best["name"],
        "role": best["role"],
        "email": matched_email,
        "source": "website",
        "confidence": "low",
        "other_names": others[:3],
    }


@dataclass
class SiteContactFinder:
    """Fetches a small number of the business's own pages, politely."""

    http_get: Optional[Callable] = None
    cache: object = None
    timeout: float = 15.0
    max_pages: int = 4
    user_agent: str = "postal-pipeline contact-lookup"
    stats: dict = field(default_factory=lambda: {"fetched": 0, "skipped_by_robots": 0})

    def _get(self, url: str) -> Optional[str]:
        if self.http_get is not None:
            return self.http_get(url)
        import requests

        try:
            resp = requests.get(
                url, timeout=self.timeout, allow_redirects=True,
                headers={"User-Agent": self.user_agent},
            )
            if resp.status_code >= 400:
                return None
            return resp.text
        except Exception:
            return None

    def _robots_allows(self, base: str) -> Callable[[str], bool]:
        """Respect robots.txt — we are reading someone else's site."""
        from urllib.robotparser import RobotFileParser

        robots_url = urljoin(base, "/robots.txt")
        body = self._get(robots_url)
        parser = RobotFileParser()
        if not body:
            parser.parse([])  # no robots.txt means no restrictions
            return lambda _url: True
        parser.parse(body.splitlines())
        return lambda url: parser.can_fetch(self.user_agent, url)

    def find(self, website: Optional[str]) -> Optional[dict]:
        if not website:
            return None

        def work() -> dict:
            base = website if "://" in website else f"https://{website}"
            root = f"{urlparse(base).scheme}://{urlparse(base).netloc}"
            allowed = self._robots_allows(root)

            people: list[dict] = []
            emails: list[dict] = []
            pages_read: list[str] = []

            candidates = [base] + [urljoin(root, path) for path in CONTACT_PATHS]
            for url in candidates:
                if len(pages_read) >= self.max_pages:
                    break
                if not allowed(url):
                    self.stats["skipped_by_robots"] += 1
                    continue
                html = self._get(url)
                if not html:
                    continue
                self.stats["fetched"] += 1
                pages_read.append(url)
                people.extend(find_people(html))
                emails.extend(find_emails(html))

            # De-duplicate emails while keeping first-seen order.
            unique_emails: dict[str, dict] = {}
            for entry in emails:
                unique_emails.setdefault(entry["email"].lower(), entry)

            contact = choose_site_contact(people, list(unique_emails.values()))
            return {
                "contact": contact,
                "emails": list(unique_emails.values()),
                "pages_read": pages_read,
            }

        if self.cache is None:
            return work()
        return self.cache.get_or_fetch("site_contacts", website, work)
