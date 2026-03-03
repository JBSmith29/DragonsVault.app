from __future__ import annotations

from dataclasses import dataclass

from shared.jobs import jobs


@dataclass
class _Stats:
    added: int = 1
    updated: int = 0
    skipped: int = 0
    errors: int = 0


class _Queue:
    def __init__(self):
        self.calls = []

    def enqueue(self, *args, **kwargs):
        self.calls.append((args, kwargs))


def test_enqueue_csv_import_uses_queue_when_inline_disabled(app, monkeypatch):
    app.config["IMPORT_RUN_INLINE"] = False
    queue = _Queue()

    monkeypatch.setattr(jobs, "_jobs_available", True)
    monkeypatch.setattr(jobs, "get_queue", lambda *args, **kwargs: queue)
    monkeypatch.setattr(jobs, "validate_import_file", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        jobs,
        "run_csv_import_inline",
        lambda *args, **kwargs: (_Stats(), {}),
    )

    with app.app_context():
        result = jobs.enqueue_csv_import("ignored.csv", "new_only")

    assert result["ran_inline"] is False
    assert len(queue.calls) == 1


def test_enqueue_csv_import_falls_back_inline_when_queue_missing(app, monkeypatch):
    app.config["IMPORT_RUN_INLINE"] = False

    monkeypatch.setattr(jobs, "_jobs_available", True)
    monkeypatch.setattr(jobs, "get_queue", lambda *args, **kwargs: None)
    monkeypatch.setattr(jobs, "validate_import_file", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        jobs,
        "run_csv_import_inline",
        lambda *args, **kwargs: (_Stats(added=2), {"Deck": 2}),
    )

    with app.app_context():
        result = jobs.enqueue_csv_import("ignored.csv", "new_only")

    assert result["ran_inline"] is True
    assert result["stats"].added == 2
    assert result["per_folder"] == {"Deck": 2}


def test_enqueue_csv_import_async_uses_queue_when_available(app, monkeypatch):
    app.config["IMPORT_RUN_INLINE"] = False
    queue = _Queue()

    monkeypatch.setattr(jobs, "_jobs_available", True)
    monkeypatch.setattr(jobs, "get_queue", lambda *args, **kwargs: queue)
    monkeypatch.setattr(jobs, "validate_import_file", lambda *args, **kwargs: None)

    with app.app_context():
        result = jobs.enqueue_csv_import("ignored.csv", "new_only", run_async=True)

    assert result["ran_inline"] is False
    assert len(queue.calls) == 1
