from __future__ import annotations

from core.domains.cards.services import scryfall_metadata_service as metadata


def test_normalize_color_identity_orders_colors_and_builds_mask():
    letters, mask = metadata.normalize_color_identity(["g", "u", "r"])

    assert letters == "URG"
    assert mask == 2 | 8 | 16


def test_metadata_from_print_joins_faces_and_builds_faces_json():
    payload = metadata.metadata_from_print(
        {
            "type_line": "Instant",
            "rarity": "Rare",
            "layout": "split",
            "cmc": 2,
            "color_identity": ["R", "W"],
            "card_faces": [
                {
                    "name": "Wear",
                    "oracle_text": "Destroy target artifact.",
                    "mana_cost": "{1}{R}",
                    "type_line": "Instant",
                    "colors": ["R"],
                    "image_uris": {"small": "wear-small", "normal": "wear-normal"},
                },
                {
                    "name": "Tear",
                    "oracle_text": "Destroy target enchantment.",
                    "mana_cost": "{W}",
                    "type_line": "Instant",
                    "colors": ["W"],
                    "image_uris": {"small": "tear-small", "normal": "tear-normal"},
                },
            ],
        }
    )

    assert payload["oracle_text"] == "Destroy target artifact. // Destroy target enchantment."
    assert payload["rarity"] == "rare"
    assert payload["layout"] == "split"
    assert payload["color_identity"] == "WR"
    assert payload["color_identity_mask"] == 1 | 8
    assert payload["faces_json"][0]["name"] == "Wear"
    assert payload["faces_json"][1]["name"] == "Tear"


def test_search_local_cards_filters_and_sorts_results():
    cache = [
        {
            "name": "Lightning Bolt",
            "set": "lea",
            "collector_number": "161",
            "type_line": "Instant",
            "color_identity": ["R"],
            "rarity": "common",
            "cmc": 1,
            "legalities": {"commander": "legal"},
        },
        {
            "name": "Wear // Tear",
            "set": "dgm",
            "collector_number": "150",
            "type_line": "Instant",
            "color_identity": ["R", "W"],
            "rarity": "rare",
            "cmc": 2,
            "legalities": {"commander": "legal"},
        },
        {
            "name": "Forest",
            "set": "lea",
            "collector_number": "301",
            "type_line": "Basic Land — Forest",
            "color_identity": ["G"],
            "rarity": "common",
            "cmc": 0,
            "legalities": {"commander": "legal"},
        },
    ]

    result = metadata.search_local_cards(
        ensure_cache_loaded_fn=lambda: True,
        cache=cache,
        base_types=["Instant"],
        colors=["R"],
        color_mode="contains",
        order="collector",
        direction="asc",
        page=1,
        per=10,
    )

    assert result["total_cards"] == 2
    assert [card["name"] for card in result["data"]] == ["Wear // Tear", "Lightning Bolt"]
    assert result["has_more"] is False
