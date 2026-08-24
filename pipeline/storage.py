"""Batch folders — the whole persistence layer (spec section 3).

One batch = one folder. One business = one subfolder keyed by Place ID.
No database until batch five and it's clearly needed.

    batches/2026-09-01_physios_stevenage/
      batch.json          niche, area, dates, status counts
      shortlist.csv       ranked output of discover, before the cull
      approved.csv        the 10-15 actually chosen
      rejections.jsonl    every rejection, with the full record attached
      <place_id>/business.json
"""

from __future__ import annotations

import csv
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

# Columns in shortlist.csv, ordered for fast human scanning (spec section 2).
SHORTLIST_COLUMNS = [
    "lead_score",
    "name",
    "town",
    "website",
    "site_verdict",
    "mobile_score",
    "https",
    "rating",
    "review_count",
    "recent_review_date",
    "address_to",
    "contact_check",
    "owner_name",
    "site_contact",
    "contact_email",
    "company_type",
    "phone",
    "address",
    "postcode",
    "maps_url",
    "website_url",
    "photo_count",
    "place_id",
]


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug or "business"


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Batch:
    """A batch folder on disk."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    # -- creation / naming --------------------------------------------------

    @classmethod
    def create(
        cls,
        batches_dir: str | Path,
        *,
        niche: str,
        area: str,
        name: Optional[str] = None,
    ) -> "Batch":
        folder = name or f"{date.today().isoformat()}_{slugify(niche)}_{slugify(area)}"
        batch = cls(Path(batches_dir) / folder)
        batch.path.mkdir(parents=True, exist_ok=True)
        if not batch.meta_path.is_file():
            batch.write_meta(
                {
                    "niche": niche,
                    "area": area,
                    "created": utcnow(),
                    "status_counts": {},
                    "rejection_reasons": {},
                }
            )
        return batch

    # -- paths --------------------------------------------------------------

    @property
    def meta_path(self) -> Path:
        return self.path / "batch.json"

    @property
    def shortlist_path(self) -> Path:
        return self.path / "shortlist.csv"

    @property
    def approved_path(self) -> Path:
        return self.path / "approved.csv"

    @property
    def rejections_path(self) -> Path:
        return self.path / "rejections.jsonl"

    def business_dir(self, place_id: str) -> Path:
        return self.path / place_id

    def business_path(self, place_id: str) -> Path:
        return self.business_dir(place_id) / "business.json"

    # -- read / write -------------------------------------------------------

    def write_meta(self, meta: dict) -> None:
        self.meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    def read_meta(self) -> dict:
        if not self.meta_path.is_file():
            return {}
        return json.loads(self.meta_path.read_text(encoding="utf-8"))

    def update_meta(self, **changes: Any) -> dict:
        meta = self.read_meta()
        meta.update(changes)
        self.write_meta(meta)
        return meta

    def write_business(self, business: dict) -> Path:
        place_id = business["place_id"]
        self.business_dir(place_id).mkdir(parents=True, exist_ok=True)
        path = self.business_path(place_id)
        path.write_text(json.dumps(business, indent=2), encoding="utf-8")
        return path

    def read_business(self, place_id: str) -> Optional[dict]:
        path = self.business_path(place_id)
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def iter_businesses(self) -> Iterator[dict]:
        for child in sorted(self.path.iterdir()):
            if child.is_dir() and (child / "business.json").is_file():
                yield json.loads((child / "business.json").read_text(encoding="utf-8"))

    # -- CSV ----------------------------------------------------------------

    def write_shortlist(self, rows: Iterable[dict], path: Optional[Path] = None) -> Path:
        target = path or self.shortlist_path
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=SHORTLIST_COLUMNS)
            writer.writeheader()
            for row in rows:
                writer.writerow({k: row.get(k, "") for k in SHORTLIST_COLUMNS})
        return target

    def read_shortlist(self, path: Optional[Path] = None) -> list[dict]:
        target = path or self.shortlist_path
        if not target.is_file():
            return []
        with target.open(newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))

    def append_rejection(self, record: dict) -> None:
        with self.rejections_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")

    def read_rejections(self) -> list[dict]:
        if not self.rejections_path.is_file():
            return []
        out = []
        for line in self.rejections_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                out.append(json.loads(line))
        return out


def business_to_row(business: dict) -> dict:
    """Flatten a business record into a shortlist.csv row."""
    from .places import most_recent_review_date

    address = business.get("address") or {}
    site = business.get("site_score") or {}
    owner = business.get("owner") or {}
    company = business.get("company") or {}
    https = site.get("https")

    site_contact = business.get("site_contact") or {}
    addressee = business.get("addressee") or {}
    site_label = ""
    if site_contact.get("name"):
        site_label = site_contact["name"]
        if site_contact.get("role"):
            site_label += f" ({site_contact['role']})"

    return {
        "lead_score": business.get("lead_score", 0),
        "address_to": addressee.get("address_to") or "",
        # 'differ' and 'likely_same' are the rows worth ten seconds of your eyes.
        "contact_check": addressee.get("verdict") or "",
        "site_contact": site_label,
        # A signal about who to address, NOT a mailing list: unsolicited
        # email is not permitted, and for sole traders it is unlawful.
        "contact_email": site_contact.get("email") or "",
        "name": business.get("name") or "",
        "town": address.get("town") or "",
        "website": business.get("website") or "",
        "site_verdict": site.get("verdict") or "",
        "mobile_score": "" if site.get("mobile_score") is None else site["mobile_score"],
        "https": "" if https is None else ("yes" if https else "no"),
        "rating": business.get("rating") if business.get("rating") is not None else "",
        "review_count": business.get("review_count")
        if business.get("review_count") is not None
        else "",
        "recent_review_date": most_recent_review_date(business) or "",
        "owner_name": owner.get("name") or "",
        "company_type": company.get("type") or "unknown",
        "phone": business.get("phone") or "",
        "address": address.get("formatted") or address.get("line1") or "",
        "postcode": address.get("postcode") or "",
        "maps_url": business.get("maps_url") or "",
        "website_url": business.get("website") or "",
        "photo_count": business.get("photo_count", 0),
        "place_id": business.get("place_id") or "",
    }
