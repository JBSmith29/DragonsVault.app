import pytest

from roles.role_engine import (
    get_primary_role,
    get_roles_for_card,
    get_subroles_for_card,
)


class MockCard:
    def __init__(self, name: str, oracle_text: str, type_line: str):
        self.name = name
        self.oracle_text = oracle_text
        self.type_line = type_line


# A. RAMP DETECTION
def test_ramp_mana_dork():
    card = MockCard("Llanowar Elves", "Tap: Add {G}.", "Creature — Elf Druid")
    roles = {r.title() for r in get_roles_for_card(card)}
    subroles = {s.title() for s in get_subroles_for_card(card)}
    assert "Ramp" in roles
    assert any("Mana Dork" in s for s in subroles)


def test_ramp_mana_rock():
    card = MockCard("Arcane Signet", "Tap: Add one mana of any color in your commander's color identity.", "Artifact")
    roles = {r.title() for r in get_roles_for_card(card)}
    subroles = {s.title() for s in get_subroles_for_card(card)}
    assert "Ramp" in roles
    assert any("Mana Rock" in s for s in subroles)


def test_ramp_treasure():
    card = MockCard("Dockside Extortionist", "Create X Treasure tokens", "Creature — Goblin Pirate")
    roles = {r.title() for r in get_roles_for_card(card)}
    subroles = {s.title() for s in get_subroles_for_card(card)}
    assert "Ramp" in roles
    assert any("Treasure" in s for s in subroles)


def test_ramp_land_ramp():
    card = MockCard("Cultivate", "Search your library for up to two basic lands, put onto the battlefield tapped.", "Sorcery")
    roles = {r.title() for r in get_roles_for_card(card)}
    subroles = {s.title() for s in get_subroles_for_card(card)}
    assert "Ramp" in roles
    assert any("Land Ramp" in s for s in subroles)


def test_ramp_ritual():
    card = MockCard("Dark Ritual", "Add {B}{B}{B}.", "Instant")
    roles = {r.title() for r in get_roles_for_card(card)}
    subroles = {s.title() for s in get_subroles_for_card(card)}
    assert "Ramp" in roles
    assert any("Ritual" in s for s in subroles)


# B. REMOVAL DETECTION
def test_removal_destroy():
    card = MockCard("Murder", "Destroy target creature.", "Instant")
    roles = {r.title() for r in get_roles_for_card(card)}
    assert "Removal" in roles


def test_removal_exile():
    card = MockCard("Swords to Plowshares", "Exile target creature.", "Instant")
    roles = {r.title() for r in get_roles_for_card(card)}
    assert "Removal" in roles


def test_removal_fight():
    card = MockCard("Rabid Bite", "Target creature you control deals damage equal to its power to target creature you don't control.", "Sorcery")
    roles = {r.title() for r in get_roles_for_card(card)}
    subroles = {s.title() for s in get_subroles_for_card(card)}
    assert "Removal" in roles
    assert any("Fight" in s for s in subroles)


def test_removal_board_wipe():
    card = MockCard("Wrath of God", "Destroy all creatures. They can't be regenerated.", "Sorcery")
    roles = {r.title() for r in get_roles_for_card(card)}
    subroles = {s.title() for s in get_subroles_for_card(card)}
    assert "Removal" in roles
    assert any("Board Wipe" in s for s in subroles)


# C. DRAW DETECTION
def test_draw_burst():
    card = MockCard("Harmonize", "Draw three cards.", "Sorcery")
    roles = {r.title() for r in get_roles_for_card(card)}
    assert "Draw" in roles


def test_draw_wheel():
    card = MockCard("Windfall", "Each player discards their hand, then draws cards", "Sorcery")
    roles = {r.title() for r in get_roles_for_card(card)}
    subroles = {s.title() for s in get_subroles_for_card(card)}
    assert "Draw" in roles
    assert any("Wheel" in s for s in subroles)


def test_draw_loot():
    card = MockCard("Faithless Looting", "Draw two cards, then discard two cards.", "Sorcery")
    roles = {r.title() for r in get_roles_for_card(card)}
    subroles = {s.title() for s in get_subroles_for_card(card)}
    assert "Draw" in roles
    assert any("Loot" in s for s in subroles)


def test_draw_impulse():
    card = MockCard("Light Up the Stage", "Exile the top two cards of your library. Until the end of your next turn, you may play those cards.", "Sorcery")
    roles = {r.title() for r in get_roles_for_card(card)}
    subroles = {s.title() for s in get_subroles_for_card(card)}
    assert "Draw" in roles
    assert any("Impulse" in s for s in subroles)


# D. TOKEN GENERATION
def test_tokens_creature():
    card = MockCard("Lingering Souls", "Create two 1/1 white Spirit creature tokens with flying.", "Sorcery")
    roles = {r.title() for r in get_roles_for_card(card)}
    subroles = {s.title() for s in get_subroles_for_card(card)}
    assert "Tokens" in roles
    assert any("Creature" in s for s in subroles)


def test_tokens_treasure():
    card = MockCard("Big Score", "Create two Treasure tokens.", "Instant")
    roles = {r.title() for r in get_roles_for_card(card)}
    subroles = {s.title() for s in get_subroles_for_card(card)}
    assert "Tokens" in roles
    assert any("Treasure" in s for s in subroles)


def test_tokens_clue_food():
    card = MockCard("Tireless Provisioner", "Investigate or create a Food token.", "Creature — Elf Scout")
    roles = {r.title() for r in get_roles_for_card(card)}
    subroles = {s.title() for s in get_subroles_for_card(card)}
    assert "Tokens" in roles
    assert any("Clue" in s or "Food" in s for s in subroles)


# E. TUTOR DETECTION
def test_tutor_creature():
    card = MockCard("Worldly Tutor", "Search your library for a creature card", "Instant")
    roles = {r.title() for r in get_roles_for_card(card)}
    subroles = {s.title() for s in get_subroles_for_card(card)}
    assert "Tutor" in roles
    assert any("Creature Tutor" in s for s in subroles)


def test_tutor_universal():
    card = MockCard("Demonic Tutor", "Search your library for a card", "Sorcery")
    roles = {r.title() for r in get_roles_for_card(card)}
    subroles = {s.title() for s in get_subroles_for_card(card)}
    assert "Tutor" in roles
    assert any("Universal Tutor" in s or "Land Tutor" in s or "Spell Tutor" in s for s in subroles)


def test_tutor_land():
    card = MockCard("Crop Rotation", "Sacrifice a land. Search your library for a land card", "Instant")
    roles = {r.title() for r in get_roles_for_card(card)}
    subroles = {s.title() for s in get_subroles_for_card(card)}
    assert "Tutor" in roles
    assert any("Land Tutor" in s for s in subroles)


# F. RECURSION
def test_recursion_reanimate():
    card = MockCard("Reanimate", "Put target creature card from a graveyard onto the battlefield", "Sorcery")
    roles = {r.title() for r in get_roles_for_card(card)}
    subroles = {s.title() for s in get_subroles_for_card(card)}
    assert "Recursion" in roles
    assert any("Creature Reanimation" in s for s in subroles)


def test_recursion_regrowth():
    card = MockCard("Regrowth", "Return target card from your graveyard to your hand.", "Sorcery")
    roles = {r.title() for r in get_roles_for_card(card)}
    subroles = {s.title() for s in get_subroles_for_card(card)}
    assert "Recursion" in roles
    assert any("Regrowth" in s for s in subroles)


def test_recursion_flashback():
    card = MockCard("Memory Deluge", "Flashback", "Instant")
    roles = {r.title() for r in get_roles_for_card(card)}
    subroles = {s.title() for s in get_subroles_for_card(card)}
    assert "Recursion" in roles
    assert any("Gravecast" in s or "Mass Reanimation" in s for s in subroles)


# G. PROTECTION
def test_protection_hexproof():
    card = MockCard("Heroic Intervention", "Permanents you control gain hexproof and indestructible", "Instant")
    roles = {r.title() for r in get_roles_for_card(card)}
    subroles = {s.title() for s in get_subroles_for_card(card)}
    assert "Protection" in roles
    assert any("Hexproof" in s for s in subroles)


def test_protection_indestructible():
    card = MockCard("Boros Charm", "Permanents you control gain indestructible", "Instant")
    roles = {r.title() for r in get_roles_for_card(card)}
    subroles = {s.title() for s in get_subroles_for_card(card)}
    assert "Protection" in roles
    assert any("Indestructible" in s for s in subroles)


def test_protection_blink():
    card = MockCard("Ephemerate", "Exile target creature you control, then return it", "Instant")
    roles = {r.title() for r in get_roles_for_card(card)}
    subroles = {s.title() for s in get_subroles_for_card(card)}
    assert "Protection" in roles
    assert any("Blink" in s for s in subroles)


# H. PRIMARY ROLE PRIORITY
def test_primary_prefers_removal_over_draw():
    card = MockCard("Terminate and Draw", "Destroy target creature. Draw a card.", "Instant")
    roles = get_roles_for_card(card)
    primary = get_primary_role(roles)
    assert primary == "removal"


def test_primary_prefers_ramp_over_draw():
    card = MockCard("Growth and Cantrip", "Search your library for a land card, put it tapped. Draw a card.", "Sorcery")
    roles = get_roles_for_card(card)
    primary = get_primary_role(roles)
    assert primary == "ramp"
