from __future__ import annotations

import os


def default_cache_type() -> str:
    """Prefer Redis cache when configured; fall back to SimpleCache."""
    if os.getenv("CACHE_TYPE"):
        return os.getenv("CACHE_TYPE", "SimpleCache")
    if os.getenv("CACHE_REDIS_URL") or os.getenv("REDIS_URL"):
        return "RedisCache"
    return "SimpleCache"
