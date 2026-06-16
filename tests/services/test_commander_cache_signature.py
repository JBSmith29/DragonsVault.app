"""Regression tests for commander bracket signature computation."""

from core.domains.decks.services import commander_cache


def test_signature_handles_mixed_mana_value_types():
    """Cards with and without a numeric mana_value must not crash the sort.

    Regression for a 500 on /decks: ``mana_value`` was normalized to ``float``
    for numeric cards and ``str`` for missing ones, so the sort key compared
    ``float`` against ``str`` (TypeError) whenever the earlier tuple fields tied.
    """
    cards = [
        {"name": "Forest", "mana_cost": "", "mana_value": None, "type_line": "Land"},
        {"name": "Forest", "mana_cost": "", "mana_value": 3.0, "type_line": "Land"},
        {"name": "Forest", "mana_cost": "", "mana_value": 1, "type_line": "Land"},
        {"name": "Forest", "mana_cost": "", "mana_value": "X", "type_line": "Land"},
    ]

    signature = commander_cache.compute_bracket_signature(cards, None, epoch=1)

    assert isinstance(signature, str) and len(signature) == 40


def test_signature_is_stable_and_order_independent():
    """Identical card sets in different input order yield the same signature."""
    cards_a = [
        {"name": "Sol Ring", "mana_cost": "{1}", "mana_value": 1, "type_line": "Artifact"},
        {"name": "Island", "mana_cost": "", "mana_value": None, "type_line": "Land"},
    ]
    cards_b = list(reversed(cards_a))

    assert commander_cache.compute_bracket_signature(
        cards_a, None, epoch=1
    ) == commander_cache.compute_bracket_signature(cards_b, None, epoch=1)
