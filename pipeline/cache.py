"""On-disk JSON cache with a TTL, keyed by namespace + key.

Every Places / PageSpeed / Companies House result is cached to disk for
30 days (spec section 2). Re-running a niche is then near-instant and
near-free, and the radius can be widened without re-paying for what was
already fetched.

Layout: <cache_dir>/<namespace>/<safe-key>.json
Each file stores {"fetched": <iso8601>, "key": <original>, "data": ...}.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

_SAFE = re.compile(r"[^A-Za-z0-9._-]")


def _safe_name(key: str) -> str:
    """Filesystem-safe filename for a cache key.

    Short, well-behaved keys stay human-readable; anything long or awkward
    is hashed so the filename stays bounded and collision-resistant.
    """
    slug = _SAFE.sub("_", key)
    if len(slug) <= 80 and slug == key.replace("/", "_"):
        return slug
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    return f"{slug[:60]}_{digest}"


class Cache:
    def __init__(self, cache_dir: str | Path, ttl_days: int = 30) -> None:
        self.root = Path(cache_dir)
        self.ttl = timedelta(days=ttl_days)

    def _path(self, namespace: str, key: str) -> Path:
        return self.root / namespace / f"{_safe_name(key)}.json"

    def get(self, namespace: str, key: str) -> Any | None:
        """Return cached data if present and not expired, else None."""
        path = self._path(namespace, key)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            fetched = datetime.fromisoformat(payload["fetched"])
        except (json.JSONDecodeError, KeyError, ValueError):
            return None
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - fetched > self.ttl:
            return None
        return payload.get("data")

    def set(self, namespace: str, key: str, data: Any) -> None:
        path = self._path(namespace, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "fetched": datetime.now(timezone.utc).isoformat(),
            "key": key,
            "data": data,
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def get_or_fetch(
        self, namespace: str, key: str, fetch: Callable[[], Any]
    ) -> Any:
        """Return cached data, or call fetch(), store, and return it.

        A fetch that returns None is not cached (treated as a transient
        miss), so a failed API call does not poison the cache for 30 days.
        """
        cached = self.get(namespace, key)
        if cached is not None:
            return cached
        fresh = fetch()
        if fresh is not None:
            self.set(namespace, key, fresh)
        return fresh
