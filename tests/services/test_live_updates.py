from __future__ import annotations

from queue import Empty

import pytest

from shared.events import live_updates


def test_emit_import_event_reaches_subscribers():
    queue, handler = live_updates.subscribe_import_events()
    try:
        live_updates.emit_import_event("started", job_id="abc123", file="cards.csv")
        event = queue.get(timeout=0.2)
        assert event["scope"] == "import"
        assert event["type"] == "started"
        assert event["job_id"] == "abc123"
        assert event["file"] == "cards.csv"
    finally:
        live_updates.unsubscribe_import_events(handler)


def test_unsubscribed_handlers_stop_receiving_events():
    queue, handler = live_updates.subscribe_import_events()
    live_updates.unsubscribe_import_events(handler)
    live_updates.emit_job_event("scryfall", "completed", job_id="xyz")
    with pytest.raises(Empty):
        queue.get(timeout=0.05)
