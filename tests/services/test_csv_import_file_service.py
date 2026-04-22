from __future__ import annotations

from pathlib import Path

import pytest

from core.domains.cards.services import csv_import_file_service as file_service


def _write_file(tmp_path: Path, name: str, contents: str) -> Path:
    path = tmp_path / name
    path.write_text(contents, encoding="utf-8")
    return path


def test_read_import_headers_supports_sep_preamble(tmp_path):
    csv_path = _write_file(
        tmp_path,
        "semicolon.csv",
        "sep=;\nCard Name;Set Code;Collector Number;Quantity\nLightning Bolt;M11;146;3\n",
    )

    headers = file_service.read_import_headers(str(csv_path))

    assert headers == ["Card Name", "Set Code", "Collector Number", "Quantity"]


def test_resolve_header_mapping_honors_valid_override():
    mapping, missing, invalid = file_service.resolve_header_mapping(
        ["Deck Name", "Edition", "Card No.", "Card"],
        mapping_override={
            "folder": "Deck Name",
            "set_code": "Edition",
            "collector_number": "Card No.",
            "name": "Card",
        },
        allow_missing=False,
    )

    assert missing == []
    assert invalid == []
    assert mapping["folder"] == "Deck Name"
    assert mapping["set_code"] == "Edition"
    assert mapping["collector_number"] == "Card No."
    assert mapping["name"] == "Card"


def test_count_rows_skips_blank_rows(tmp_path):
    csv_path = _write_file(
        tmp_path,
        "rows.csv",
        "Card Name,Set Code,Collector Number\nLightning Bolt,M11,146\n\nShock,M19,156\n",
    )

    assert file_service.count_rows(str(csv_path)) == 2


def test_validate_import_file_reports_user_friendly_missing_headers(tmp_path):
    csv_path = _write_file(tmp_path, "invalid.csv", "Foo,Bar,Baz\n1,2,3\n")

    with pytest.raises(file_service.HeaderValidationError) as excinfo:
        file_service.validate_import_file(str(csv_path))

    assert any("Card name" in detail for detail in excinfo.value.details)
    assert any("Collector number" in detail for detail in excinfo.value.details)
