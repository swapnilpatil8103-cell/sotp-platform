"""Simple file-based JSON cache with TTL, used to avoid hammering SEC EDGAR.

Cache entries are stored as JSON files under a cache directory (default
``data/cache/sec/``), keyed by a hash of the cache key (typically the request
URL). Each entry stores the cached payload alongside a timestamp so reads can
enforce a TTL.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Optional

DEFAULT_CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "cache" / "sec"


class FileCache:
    """A minimal file-based JSON cache keyed by an arbitrary string (e.g. a URL)."""

    def __init__(self, cache_dir: Path | str = DEFAULT_CACHE_DIR, ttl_seconds: int = 24 * 60 * 60):
        self.cache_dir = Path(cache_dir)
        self.ttl_seconds = ttl_seconds
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _path_for(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}.json"

    def get(self, key: str) -> Optional[Any]:
        """Return the cached value for ``key``, or None if missing/expired/unreadable."""
        path = self._path_for(key)
        if not path.exists():
            return None
        try:
            with path.open("r", encoding="utf-8") as f:
                envelope = json.load(f)
        except (OSError, json.JSONDecodeError):
            return None

        cached_at = envelope.get("cached_at", 0)
        if time.time() - cached_at > self.ttl_seconds:
            return None
        return envelope.get("payload")

    def set(self, key: str, payload: Any) -> None:
        """Persist ``payload`` under ``key`` with the current timestamp."""
        path = self._path_for(key)
        envelope = {"key": key, "cached_at": time.time(), "payload": payload}
        try:
            tmp_path = path.with_suffix(".tmp")
            with tmp_path.open("w", encoding="utf-8") as f:
                json.dump(envelope, f)
            tmp_path.replace(path)
        except OSError:
            # Caching is a best-effort optimization; failures shouldn't break callers.
            pass
