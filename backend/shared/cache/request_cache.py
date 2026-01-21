"""Request-scoped cache helpers to avoid duplicate work within a single request."""

from __future__ import annotations

from typing import Any, Callable, Hashable

from flask import g, has_request_context
from sqlalchemy import event

from extensions import db

_CACHE_KEY = "_dv_request_cache"
_LISTENERS_REGISTERED = False


def _get_cache() -> dict[Hashable, Any]:
    if not has_request_context():
        return {}
    cache = getattr(g, _CACHE_KEY, None)
    if cache is None:
        cache = {}
        setattr(g, _CACHE_KEY, cache)
    return cache


def request_cache_get(key: Hashable) -> tuple[bool, Any]:
    cache = _get_cache()
    if key in cache:
        return True, cache[key]
    return False, None


def request_cache_set(key: Hashable, value: Any) -> Any:
    if not has_request_context():
        return value
    cache = _get_cache()
    cache[key] = value
    return value


def request_cache_clear(prefix: str | None = None) -> None:
    if not has_request_context():
        return
    cache = getattr(g, _CACHE_KEY, None)
    if not isinstance(cache, dict):
        return
    if prefix is None:
        cache.clear()
        return
    keys = [
        key
        for key in list(cache.keys())
        if key == prefix or (isinstance(key, tuple) and key and key[0] == prefix)
    ]
    for key in keys:
        cache.pop(key, None)


def request_cached(key: Hashable, factory: Callable[[], Any]) -> Any:
    hit, value = request_cache_get(key)
    if hit:
        return value
    return request_cache_set(key, factory())


def register_request_cache_listeners() -> None:
    global _LISTENERS_REGISTERED
    if _LISTENERS_REGISTERED:
        return
    _LISTENERS_REGISTERED = True

    @event.listens_for(db.session, "after_flush")
    def _clear_request_cache_on_write(session, _flush_context) -> None:
        if not (session.new or session.dirty or session.deleted):
            return
        request_cache_clear()
