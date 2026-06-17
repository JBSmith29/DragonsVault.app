from core.domains.decks.services.opening_hand_deck_source_service import (
    _commander_name_forms,
)


def _excluded(commander_name, card_name):
    """Mirror the deck-loader's exclusion test: a card is the commander when its
    normalized name forms intersect the commander's."""
    commander_forms = _commander_name_forms(commander_name)
    return bool(commander_forms and _commander_name_forms(card_name) & commander_forms)


def test_commander_excluded_with_straight_and_curly_apostrophes():
    # Folder stores a curly apostrophe; the deck card uses a straight one.
    assert _excluded("Atraxa, Praetors’ Voice", "Atraxa, Praetors' Voice")
    assert _excluded("Atraxa, Praetors' Voice", "Atraxa, Praetors’ Voice")


def test_commander_excluded_for_double_faced_card_names():
    # Commander stored as the front face only; deck card stored as the full
    # "Front // Back" name (and the reverse).
    full = "Tovolar, Dire Overlord // Tovolar, the Midnight Scourge"
    assert _excluded("Tovolar, Dire Overlord", full)
    assert _excluded(full, "Tovolar, Dire Overlord")


def test_partner_commanders_split_on_ampersand():
    pair = "Halana, Kessig Ranger & Alena, Kessig Trapper"
    assert _excluded(pair, "Alena, Kessig Trapper")
    assert _excluded(pair, "Halana, Kessig Ranger")


def test_commander_name_comma_is_not_a_separator():
    # The comma in a single commander name must not split it into fragments that
    # wrongly match unrelated cards.
    assert not _excluded("Atraxa, Praetors' Voice", "Praetors' Voice")
    assert not _excluded("Atraxa, Praetors' Voice", "Atraxa")


def test_non_commander_card_is_not_excluded():
    assert not _excluded("Atraxa, Praetors' Voice", "Sol Ring")
    assert not _excluded("", "Sol Ring")
