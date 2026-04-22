"""Runtime cache helpers shared across high-level services."""

from __future__ import annotations

from flask_login import current_user

from extensions import cache


def user_cache_key() -> str:
    """Return a stable cache key fragment for the current user context."""
    return str(getattr(current_user, "id", None) or "anon")


def cache_fetch(key: str, ttl_seconds: int, factory):
    """Fetch from the shared cache with a basic payload size guard."""
    if not cache:
        return factory()
    try:
        cached = cache.get(key)
    except Exception:
        cached = None
    if cached is not None:
        return cached

    value = factory()

    try:
        import sys

        if sys.getsizeof(value) < 1024 * 1024:
            cache.set(key, value, timeout=ttl_seconds)
    except Exception:
        pass
    return value


__all__ = ["cache_fetch", "user_cache_key"]
