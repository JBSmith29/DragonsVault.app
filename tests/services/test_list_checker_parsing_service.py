from core.domains.cards.services import list_checker_parsing_service


def test_parse_card_list_merges_multiple_quantity_formats():
    parsed = list_checker_parsing_service.parse_card_list(
        """
        2x Sol Ring
        Sol Ring x 3
        1 Arcane Signet
        """
    )

    assert list(parsed) == ["sol ring", "arcane signet"]
    assert parsed["sol ring"] == {"display": "Sol Ring", "qty": 5}
    assert parsed["arcane signet"] == {"display": "Arcane Signet", "qty": 1}
