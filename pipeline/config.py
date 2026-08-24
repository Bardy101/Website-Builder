"""Configuration and .env loading.

Kept deliberately tiny: no python-dotenv dependency, just a plain-text
parser for a KEY=VALUE .env file plus a Config dataclass read from the
environment. Missing keys are allowed — the stages that need a given key
fail with a clear message only when they actually try to use it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_dotenv(path: str | os.PathLike[str] = ".env", *, override: bool = False) -> None:
    """Load KEY=VALUE lines from a .env file into os.environ.

    Silently does nothing if the file is absent. Existing environment
    variables are kept unless override=True. Lines starting with '#' and
    blank lines are ignored; surrounding quotes on values are stripped.
    """
    p = Path(path)
    if not p.is_file():
        return
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key:
            continue
        if override or key not in os.environ:
            os.environ[key] = value


@dataclass(frozen=True)
class Config:
    google_places_api_key: str | None
    pagespeed_api_key: str | None
    companies_house_api_key: str | None
    cache_dir: str
    batches_dir: str
    cache_ttl_days: int

    @classmethod
    def from_env(cls, *, load_env_file: bool = True) -> "Config":
        if load_env_file:
            load_dotenv()
            # Also try the .env beside the project, so launching from another
            # working directory still finds your keys. load_dotenv never
            # overwrites what is already set, so the local file still wins.
            load_dotenv(Path(__file__).resolve().parent.parent / ".env")
        return cls(
            google_places_api_key=os.environ.get("GOOGLE_PLACES_API_KEY") or None,
            pagespeed_api_key=os.environ.get("PAGESPEED_API_KEY") or None,
            companies_house_api_key=os.environ.get("COMPANIES_HOUSE_API_KEY") or None,
            cache_dir=os.environ.get("PIPELINE_CACHE_DIR", ".cache"),
            batches_dir=os.environ.get("PIPELINE_BATCHES_DIR", "batches"),
            cache_ttl_days=int(os.environ.get("PIPELINE_CACHE_TTL_DAYS", "30")),
        )
