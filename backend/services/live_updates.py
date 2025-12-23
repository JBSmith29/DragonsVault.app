"""Lightweight event hub for streaming import progress over WebSocket."""
from __future__ import annotations

import queue
from collections import defaultdict, deque
from typing import Callable, Tuple

from blinker import Signal

from utils.time import utcnow
_import_signal = Signal("import-events")
_RECENT_EVENT_LIMIT = 50
_recent_events: dict[tuple[str, str | None], deque] = defaultdict(lambda: deque(maxlen=_RECENT_EVENT_LIMIT))


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
