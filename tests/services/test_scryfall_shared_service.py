def test_faces_from_scry_json_dedupes_duplicate_faces():
    from core.domains.cards.services import scryfall_shared_service

    data = {
        "card_faces": [
            {"image_uris": {"small": "small-a", "normal": "normal-a", "large": "large-a"}},
            {"image_uris": {"small": "small-a", "normal": "normal-a", "large": "large-a"}},
            {"image_uris": {"small": "small-b", "normal": "normal-b", "large": "large-b"}},
        ]
    }

    faces = scryfall_shared_service._faces_from_scry_json(data)

    assert faces == [
        {"small": "small-a", "normal": "normal-a", "large": "large-a"},
        {"small": "small-b", "normal": "normal-b", "large": "large-b"},
    ]


def test_price_lines_type_badges_and_rarity_badges():
    from core.domains.cards.services import scryfall_shared_service

    assert scryfall_shared_service._price_lines({"usd": "1.00", "usd_foil": "2.00"}) == [
        "USD 1.00",
        "USD Foil 2.00",
    ]
    assert scryfall_shared_service._type_badges("Artifact Creature - Golem") == ["Artifact", "Creature"]
    assert scryfall_shared_service._rarity_badge_class("mythic rare") == "danger"
