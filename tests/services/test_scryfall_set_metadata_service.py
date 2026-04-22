from __future__ import annotations

from core.domains.cards.services import scryfall_set_metadata_service as metadata


def test_build_set_name_release_maps_and_all_codes():
    cache = [
        {"set": "woe", "set_name": "Wilds of Eldraine", "released_at": "2023-09-08"},
        {"set": "woe", "set_name": "Wilds of Eldraine", "released_at": "2023-09-01"},
        {"set": "bro", "set_name": "The Brothers' War", "released_at": "2022-11-18"},
    ]

    assert metadata.build_set_name_map(cache) == {
        "woe": "Wilds of Eldraine",
        "bro": "The Brothers' War",
    }
    assert metadata.build_set_release_map(cache) == {
        "woe": "2023-09-01",
        "bro": "2022-11-18",
    }
    assert metadata.all_set_codes(cache) == ["bro", "woe"]


def test_normalize_set_code_trims_and_lowercases():
    assert metadata.normalize_set_code(" WOE ") == "woe"
    assert metadata.normalize_set_code("") == ""
    assert metadata.normalize_set_code(None) == ""
