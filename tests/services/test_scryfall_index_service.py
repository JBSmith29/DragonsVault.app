from __future__ import annotations

from core.domains.cards.services import scryfall_index_service as index_service


def test_find_by_set_cn_prefers_front_face_name_and_loose_collector_number():
    card = {
        "set": "woe",
        "collector_number": "12a",
        "name": "Twice Upon a Time // Return Again",
        "card_faces": [
            {"name": "Twice Upon a Time"},
            {"name": "Return Again"},
        ],
    }
    by_set_cn = {"woe::12a": card}
    idx_by_set_num = {("woe", 12): [card]}
    idx_by_name = {index_service.name_key(card["name"]): [card]}
    idx_by_front = {index_service.name_key("Twice Upon a Time"): [card]}

    result = index_service.find_by_set_cn(
        "woe",
        "12",
        name_hint="Twice Upon a Time",
        by_set_cn=by_set_cn,
        idx_by_set_num=idx_by_set_num,
        idx_by_name=idx_by_name,
        idx_by_front=idx_by_front,
        key_set_cn_fn=lambda set_code, collector_number: f"{set_code}::{collector_number}",
    )

    assert result is card


def test_unique_oracle_by_name_matches_back_face_names():
    oracle_id = "oracle-123"
    card = {
        "id": "print-1",
        "oracle_id": oracle_id,
        "set": "lci",
        "collector_number": "158",
        "name": "Ojer Axonil, Deepest Might // Temple of Power",
        "card_faces": [
            {"name": "Ojer Axonil, Deepest Might"},
            {"name": "Temple of Power"},
        ],
    }

    result = index_service.unique_oracle_by_name(
        "Temple of Power",
        idx_by_name={},
        idx_by_front={},
        idx_by_back={index_service.name_key("Temple of Power"): [card]},
    )

    assert result == oracle_id
