import pytest

from services import scryfall_cache as sc


@pytest.fixture(autouse=True)
def isolated_scryfall_cache(monkeypatch):
    """Provide an isolated in-memory cache for each test."""
    monkeypatch.setattr(sc, "_cache", [])
    monkeypatch.setattr(sc, "_by_set_cn", {})
    monkeypatch.setattr(sc, "_by_oracle", {})
    monkeypatch.setattr(sc, "_idx_by_set_num", {})
    monkeypatch.setattr(sc, "_idx_by_name", {})
    monkeypatch.setattr(sc, "_idx_by_front", {})
    monkeypatch.setattr(sc, "_idx_by_back", {})
    monkeypatch.setattr(sc, "_set_names", None)
    monkeypatch.setattr(sc, "_set_releases", None)
    monkeypatch.setattr(sc, "_cache_loaded", True)
    yield
    sc._clear_in_memory_prints()


def test_unique_oracle_by_name_handles_back_face():
    oracle_id = "test-oracle-id"
    card = {
        "set": "lci",
        "collector_number": "158",
        "name": "Ojer Axonil, Deepest Might // Temple of Power",
        "oracle_id": oracle_id,
        "lang": "en",
        "digital": False,
        "card_faces": [
            {
                "name": "Ojer Axonil, Deepest Might",
                "type_line": "Legendary Creature — God",
                "image_uris": {"small": "front-small"},
            },
            {
                "name": "Temple of Power",
                "type_line": "Land",
                "image_uris": {"small": "back-small"},
            },
        ],
    }

    sc._cache.append(card)
    sc._prime_default_indexes()

    assert sc.unique_oracle_by_name("Ojer Axonil, Deepest Might") == oracle_id
    assert sc.unique_oracle_by_name("Temple of Power") == oracle_id
    assert sc.unique_oracle_by_name("Temple of Power // Ojer Axonil, Deepest Might") == oracle_id
