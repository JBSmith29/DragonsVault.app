def test_normalize_quantity_mode_aliases():
    from core.domains.cards.services import import_shared_service

    assert import_shared_service._normalize_quantity_mode("replace") == "absolute"
    assert import_shared_service._normalize_quantity_mode("add") == "new_only"
    assert import_shared_service._normalize_quantity_mode("reset") == "purge"
    assert import_shared_service._normalize_quantity_mode("unknown") == "new_only"


def test_parse_manual_card_list_supports_quantity_prefixes():
    from core.domains.cards.services import import_shared_service

    entries = import_shared_service._parse_manual_card_list(
        "3 Sol Ring\nLightning Bolt\n2x Arcane Signet\n"
    )

    assert entries == [
        {"name": "Sol Ring", "quantity": 3},
        {"name": "Lightning Bolt", "quantity": 1},
        {"name": "Arcane Signet", "quantity": 2},
    ]
