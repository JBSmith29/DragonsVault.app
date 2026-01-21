"""Lightweight event hub for streaming import/job progress over HTTP polling."""
from __future__ import annotations

import json
import os
import queue
import re
from collections import defaultdict, deque
from pathlib import Path
from typing import Callable, Tuple

from flask import current_app, has_app_context
from blinker import Signal

from core.shared.utils.time import utcnow
_import_signal = Signal("import-events")
_RECENT_EVENT_LIMIT = 50
_REDIS_EVENT_TTL_SECONDS = int(os.getenv("JOB_EVENT_TTL_SECONDS", "7200"))
_recent_events: dict[tuple[str, str | None], deque] = defaultdict(lambda: deque(maxlen=_RECENT_EVENT_LIMIT))

try:  # pragma: no cover - optional dependency
    import redis
    _redis_available = True
except ImportError:  # pragma: no cover
    redis = None  # type: ignore
    _redis_available = False

_redis_client = None


def _event_file_dir() -> Path:
    if has_app_context():
        return Path(current_app.instance_path) / "data" / "job_events"
    instance_env = os.getenv("INSTANCE_DIR")
    if instance_env:
        return Path(instance_env) / "data" / "job_events"
    return Path(__file__).resolve().parents[3] / "instance" / "data" / "job_events"


def _event_file_path(scope: str, dataset: str | None) -> Path:
    safe_scope = re.sub(r"[^A-Za-z0-9_.-]+", "_", scope or "scope")
    safe_dataset = re.sub(r"[^A-Za-z0-9_.-]+", "_", dataset or "none")
    return _event_file_dir() / f"{safe_scope}__{safe_dataset}.json"


def _store_event_file(event: dict) -> None:
    scope = event.get("scope") or ""
    dataset = event.get("dataset")
    if not scope:
        return
    path = _event_file_path(scope, dataset)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            raw = path.read_text(encoding="utf-8")
            events = json.loads(raw) if raw else []
        else:
            events = []
        if not isinstance(events, list):
            events = []
        events.append(event)
        events = events[-_RECENT_EVENT_LIMIT:]
        path.write_text(json.dumps(events, ensure_ascii=True), encoding="utf-8")
    except Exception:
        pass


def _load_event_file(scope: str, dataset: str | None) -> list[dict]:
    if not scope:
        return []
    path = _event_file_path(scope, dataset)
    try:
        if not path.exists():
            return []
        raw = path.read_text(encoding="utf-8")
        events = json.loads(raw) if raw else []
        if isinstance(events, list):
            return events
    except Exception:
        return []
    return []


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


def _redis_connection():
    global _redis_client
    if not _redis_available:
        return None
    if _redis_client is None:
        try:
            _redis_client = redis.from_url(_redis_url())
        except Exception:
            return None
    return _redis_client


def _redis_key(scope: str, dataset: str | None) -> str:
    suffix = dataset if dataset else "none"
    return f"job-events:{scope}:{suffix}"


def _store_recent_event(event: dict) -> None:
    scope = event.get("scope")
    dataset = event.get("dataset")
    if not scope:
        return
    key = (scope, dataset)
    bucket = _recent_events.get(key)
    if bucket is None:
        bucket = deque(maxlen=_RECENT_EVENT_LIMIT)
        _recent_events[key] = bucket
    bucket.append(event)

    conn = _redis_connection()
    if conn is None:
        _store_event_file(event)
        return
    try:
        payload = json.dumps(event, ensure_ascii=True)
        redis_key = _redis_key(scope, dataset)
        pipe = conn.pipeline()
        pipe.lpush(redis_key, payload)
        pipe.ltrim(redis_key, 0, _RECENT_EVENT_LIMIT - 1)
        if _REDIS_EVENT_TTL_SECONDS > 0:
            pipe.expire(redis_key, _REDIS_EVENT_TTL_SECONDS)
        pipe.execute()
    except Exception:
        _store_event_file(event)


def emit_job_event(scope: str, event_type: str, **payload) -> None:
    """Publish a job-related event (import, scryfall, etc.) to all subscribers."""
    event = {"scope": scope, "type": event_type, **payload}
    event["recorded_at"] = utcnow().isoformat() + "Z"
    _store_recent_event(event)
    _import_signal.send("imports", event=event)


def emit_import_event(event_type: str, **payload) -> None:
    """Backward-compatible wrapper for import events."""
    emit_job_event("import", event_type, **payload)


def latest_job_events(scope: str, dataset: str | None = None) -> list[dict]:
    """Return the recorded events for a job scope/dataset combo (newest last)."""
    if not scope:
        return []
    conn = _redis_connection()
    if conn is not None:
        try:
            redis_key = _redis_key(scope, dataset)
            raw = conn.lrange(redis_key, 0, _RECENT_EVENT_LIMIT - 1)
            if raw:
                events = []
                for item in raw:
                    if isinstance(item, bytes):
                        item = item.decode("utf-8")
                    events.append(json.loads(item))
                events.reverse()
                return events
        except Exception:
            pass
    file_events = _load_event_file(scope, dataset)
    if file_events:
        return file_events
    key = (scope, dataset)
    events = _recent_events.get(key)
    if not events:
        return []
    return list(events)


def subscribe_import_events() -> Tuple[queue.Queue, Callable]:
    """Return a FIFO queue that receives import events and the connected handler."""
    q: queue.Queue = queue.Queue()

    def _handler(sender, event, **_extra):
        q.put(event)

    _import_signal.connect(_handler, weak=False)
    return q, _handler


def unsubscribe_import_events(handler: Callable) -> None:
    """Detach a previously registered listener."""
    try:
        _import_signal.disconnect(handler)
    except Exception:
        pass


__all__ = [
    "emit_import_event",
    "emit_job_event",
    "latest_job_events",
    "subscribe_import_events",
    "unsubscribe_import_events",
]
