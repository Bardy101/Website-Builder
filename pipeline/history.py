"""What have I already searched — and which searches are still free?

A search costs nothing to repeat only if the niche, area and radius match a
cached entry exactly: the cache key is built from all three, so "Hitchin" and
"Hitchin, Hertfordshire" are two different searches even though they mean the
same town. Guessing which wording was used last time is exactly the kind of
thing a person should not have to remember, so this reads it back off the
cache — the same store that decides whether the next run bills.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


@dataclass
class PastSearch:
    niche: str
    area: str
    radius_m: int
    fetched: Optional[datetime]
    results: int

    @property
    def age_days(self) -> Optional[int]:
        if self.fetched is None:
            return None
        return (datetime.now(timezone.utc) - self.fetched).days

    @property
    def still_cached(self, ttl_days: int = 30) -> bool:
        age = self.age_days
        return age is not None and age < ttl_days

    def describe(self) -> str:
        age = self.age_days
        when = "age unknown" if age is None else (
            "today" if age == 0 else f"{age} day{'s' if age != 1 else ''} ago"
        )
        return (f"{self.niche} in {self.area}   radius {self.radius_m}"
                f"   cached {when}")


def _parse_key(key: str) -> Optional[tuple[str, str, int]]:
    """Fall back for entries cached before niche/area were stored.

    The key is "<niche> in <area>|r=<radius>"; the niche comes first and is a
    short trade name, so the first " in " is the separator.
    """
    if "|r=" not in key:
        return None
    query, _, radius = key.rpartition("|r=")
    if " in " not in query:
        return None
    niche, _, area = query.partition(" in ")
    try:
        return niche.strip(), area.strip(), int(radius)
    except ValueError:
        return None


def past_searches(cache_dir: str | Path, *, ttl_days: int = 30) -> list[PastSearch]:
    """Every still-valid cached search, newest first."""
    root = Path(cache_dir) / "places_search"
    if not root.is_dir():
        return []

    found: dict[tuple[str, str, int], PastSearch] = {}
    for path in root.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        data = payload.get("data")
        if not isinstance(data, dict):
            continue

        niche, area, radius = data.get("niche"), data.get("area"), data.get("radius_m")
        if not (niche and area and radius):
            parsed = _parse_key(payload.get("key", ""))
            if not parsed:
                continue
            niche, area, radius = parsed

        fetched = None
        try:
            fetched = datetime.fromisoformat(payload["fetched"])
            if fetched.tzinfo is None:
                fetched = fetched.replace(tzinfo=timezone.utc)
        except (KeyError, ValueError):
            pass

        if fetched is not None:
            age = (datetime.now(timezone.utc) - fetched).days
            if age >= ttl_days:
                continue  # expired: repeating it would bill again

        entry = PastSearch(
            niche=str(niche), area=str(area), radius_m=int(radius),
            fetched=fetched, results=len(data.get("places") or []),
        )
        key = (entry.niche.lower(), entry.area.lower(), entry.radius_m)
        existing = found.get(key)
        if existing is None or (
            entry.fetched and existing.fetched and entry.fetched > existing.fetched
        ):
            found[key] = entry

    return sorted(
        found.values(),
        key=lambda s: s.fetched or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
