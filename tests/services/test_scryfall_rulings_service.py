from __future__ import annotations

import json

from core.domains.cards.services import scryfall_rulings_service as rulings_service


def test_load_rulings_bulk_indexes_and_sorts_rows(tmp_path):
    rulings_path = tmp_path / "rulings.json"
    rulings_path.write_text(
        json.dumps(
            [
                {
                    "oracle_id": "abc",
                    "published_at": "2024-03-01",
                    "source": "wotc",
                    "comment": "later",
                },
                {
                    "oracle_id": "ABC",
                    "published_at": "2024-01-01",
                    "source": "wotc",
                    "comment": "earlier",
                },
                {
                    "oracle_id": "def",
                    "published_at": "2024-02-01",
                    "source": "wotc",
                    "comment": "other",
                },
            ]
        ),
        encoding="utf-8",
    )

    indexed, loaded_path = rulings_service.load_rulings_bulk(
        rulings_bulk_path_fn=lambda _path=None: str(rulings_path),
    )

    assert loaded_path == str(rulings_path)
    assert [entry["comment"] for entry in indexed["abc"]] == ["earlier", "later"]
    assert rulings_service.rulings_for_oracle("ABC", rulings_by_oracle=indexed)[0]["comment"] == "earlier"


def test_rulings_bulk_exists_and_stale_follow_file_state(tmp_path):
    rulings_path = tmp_path / "rulings.json"

    assert rulings_service.rulings_bulk_exists(
        rulings_bulk_path_fn=lambda _path=None: str(rulings_path),
    ) is False
    assert rulings_service.rulings_is_stale(
        rulings_bulk_path_fn=lambda _path=None: str(rulings_path),
        max_age=3600,
    ) is True

    rulings_path.write_text("[]", encoding="utf-8")

    assert rulings_service.rulings_bulk_exists(
        rulings_bulk_path_fn=lambda _path=None: str(rulings_path),
    ) is True
