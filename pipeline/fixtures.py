"""Offline fixture clients — run the whole stage with no API key.

A fixture is a JSON file of raw Places-shaped detail responses:

    {"places": [ {<place details response>}, ... ]}

``find.py --fixture examples/sample_fixture.json`` then exercises search,
scoring, ranking and CSV writing end to end with no network. Useful for
trying the tool before keys arrive, and for the test suite.
"""

from __future__ import annotations

import json
from pathlib import Path


class FixturePlacesClient:
    """Drop-in replacement for PlacesClient backed by a JSON file."""

    def __init__(self, places: list[dict]) -> None:
        self.places = places
        self._by_id = {p.get("id"): p for p in places}

    @classmethod
    def from_file(cls, path: str | Path) -> "FixturePlacesClient":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(data.get("places", []))

    def search(self, niche, area, *, radius_m=8000, max_results=60, region="gb"):
        """Return stubs for fixture places matching the niche, if tagged.

        A fixture place may carry a "_niche" / "_area" hint; places without
        hints match every query so small fixtures stay simple.
        """
        out = []
        for place in self.places:
            hint_niche = place.get("_niche")
            hint_area = place.get("_area")
            if hint_niche and hint_niche.lower() != niche.lower():
                continue
            if hint_area and hint_area.lower() != area.lower():
                continue
            out.append(
                {
                    "id": place.get("id"),
                    "displayName": place.get("displayName"),
                    "formattedAddress": place.get("formattedAddress"),
                    # The real search returns this too, and discover screens
                    # on it to avoid a needless details call.
                    "businessStatus": place.get("businessStatus"),
                }
            )
        return out[:max_results]

    def details(self, place_id: str):
        return self._by_id.get(place_id, {})

    def available(self) -> list[tuple[str, str]]:
        """The (niche, area) pairs this fixture actually covers.

        A fixture is a handful of invented businesses, not the internet. Asking
        it for a niche it doesn't hold returns nothing, which looks like a
        broken tool unless we say what it does hold.
        """
        pairs = {
            (p.get("_niche"), p.get("_area"))
            for p in self.places
            if p.get("_niche") and p.get("_area")
        }
        return sorted(pairs)

    def covers(self, niche: str, area: str) -> bool:
        pairs = self.available()
        if not pairs:  # untagged fixture matches everything
            return True
        return any(
            n.lower() == niche.lower() and a.lower() == area.lower() for n, a in pairs
        )
