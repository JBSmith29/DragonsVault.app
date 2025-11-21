from __future__ import annotations

from pathlib import Path

import pytest

from models import Card, Folder
from services import csv_importer


def _write_csv(tmp_path, name: str, rows: str) -> Path:
    path = tmp_path / name
    path.write_text(rows, encoding="utf-8")
    return path


def test_process_csv_creates_cards_and_emits_progress(monkeypatch, tmp_path, db_session):  # noqa: ARG001
    csv_path = _write_csv(
        tmp_path,
        "import.csv",
        "Card Name,Set Code,Collector Number,Quantity,Folder,Folder Category\n"
        "Lightning Bolt,M11,146,3,Trade Binder,Collection\n",
    )
    emitted = []

    def _capture(event_type: str, **payload):
        emitted.append({"type": event_type, "payload": payload})

    monkeypatch.setattr(csv_importer, "emit_import_event", _capture)
    monkeypatch.setattr(csv_importer, "_ensure_cache_loaded", lambda: False)
    monkeypatch.setattr(csv_importer, "find_by_set_cn", lambda *args, **kwargs: None)
    monkeypatch.setattr(csv_importer, "metadata_from_print", lambda *_args, **_kwargs: {})

    stats, per_folder = csv_importer.process_csv(str(csv_path), default_folder="Fallback")

    assert stats.added == 1
    assert stats.updated == 0
    assert stats.skipped == 0
    assert per_folder == {"Trade Binder": 3}

    folder = Folder.query.one()
    assert folder.name == "Trade Binder"
    assert folder.category == Folder.CATEGORY_COLLECTION

    card = Card.query.one()
    assert card.quantity == 3
    assert card.folder_id == folder.id

    event_types = [event["type"] for event in emitted]
    assert event_types == ["started", "progress", "completed"]
    job_ids = {event["payload"]["job_id"] for event in emitted}
    assert len(job_ids) == 1  # every event shares the same job id


def test_validate_import_file_raises_user_friendly_error(tmp_path):
    csv_path = _write_csv(tmp_path, "invalid.csv", "Foo,Bar,Baz\n1,2,3\n")
    with pytest.raises(csv_importer.HeaderValidationError) as excinfo:
        csv_importer.validate_import_file(str(csv_path))
    details = excinfo.value.details
    assert any("Card name" in detail for detail in details)
    assert any("Collector number" in detail for detail in details)
