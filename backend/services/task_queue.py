"""Helpers for interacting with the Redis-backed job queue."""

from __future__ import annotations

import os

try:  # pragma: no cover - optional dependency
    import redis
    from rq import Queue
    _queue_available = True
except ImportError:  # pragma: no cover
    redis = None  # type: ignore
    Queue = None  # type: ignore
    _queue_available = False

from flask import current_app, has_app_context


def _redis_url() -> str:
    if has_app_context():
        return (
            current_app.config.get("REDIS_URL")
            or current_app.config.get("RATELIMIT_STORAGE_URI")
            or "redis://localhost:6379/0"
        )
    return (
        os.getenv("REDIS_URL")
        or os.getenv("RATELIMIT_STORAGE_URI")
        or "redis://localhost:6379/0"
    )


def get_redis_connection():
    if not _queue_available:
        raise RuntimeError("Redis/RQ not installed; background jobs are unavailable.")
    return redis.from_url(_redis_url())


def get_queue(name: str = "default"):
    if not _queue_available:
        raise RuntimeError("Redis/RQ not installed; background jobs are unavailable.")
    return Queue(name, connection=get_redis_connection())
