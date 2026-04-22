from core.domains.cards.services import list_checker_scryfall_service
from core.domains.cards.services import scryfall_cache


def test_build_scryfall_lookup_maps_prefers_english_and_indexes_faces(monkeypatch):
    monkeypatch.setattr(scryfall_cache, "ensure_cache_loaded", lambda: True)
    monkeypatch.setattr(
        scryfall_cache,
        "get_all_prints",
        lambda: [
            {
                "id": "jp-print",
                "name": "Wear // Tear",
                "lang": "ja",
                "oracle_id": "oid-wear-tear",
                "rarity": "rare",
                "color_identity": ["R", "W"],
                "type_line": "Instant",
            },
            {
                "id": "en-print",
                "name": "Wear // Tear",
                "lang": "en",
                "oracle_id": "oid-wear-tear",
                "rarity": "rare",
                "color_identity": ["R", "W"],
                "type_line": "Instant",
            },
        ],
    )

    name_to_sid, face_to_sid, name_to_meta, face_to_meta = list_checker_scryfall_service.build_scryfall_lookup_maps()

    assert name_to_sid["wear // tear"][0] == "en-print"
    assert face_to_sid["wear"][0] == "en-print"
    assert face_to_sid["tear"][0] == "en-print"
    assert name_to_meta["wear // tear"]["lang"] == "en"
    assert face_to_meta["wear"]["type"] == "Instant"
