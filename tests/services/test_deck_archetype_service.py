"""Tests for deck archetype classification."""

from __future__ import annotations

from core.domains.decks.services.deck_archetype_service import (
    ARCHETYPES,
    classify_deck,
)
from extensions import db
from tests.factories import create_card, create_folder


def _seed(folder, *, name, type_line, mana_value=1, oracle_text="", quantity=1):
    card = create_card(
        folder=folder,
        name=name,
        set_code="tst",
        collector_number=str(abs(hash(name)) % 1000),
        quantity=quantity,
    )
    card.type_line = type_line
    card.mana_value = mana_value
    card.oracle_text = oracle_text
    db.session.flush()
    return card


def test_classify_aggro_deck(app, db_session):
    with app.app_context():
        folder = create_folder(name="Mixed Aggro")
        # Use varied creature subtypes so the scorer doesn't fall through to
        # tribal; this is a "pile of cheap creatures" style aggro deck.
        subtypes = ["Human Soldier", "Elf Warrior", "Goblin", "Vampire", "Kithkin",
                    "Faerie", "Zombie", "Spirit", "Bird", "Warrior"]
        for i in range(30):
            _seed(
                folder,
                name=f"Beater {i}",
                type_line=f"Creature — {subtypes[i % len(subtypes)]}",
                mana_value=1,
            )
        for i in range(6):
            _seed(folder, name=f"Burn {i}", type_line="Instant", mana_value=1,
                  oracle_text="~ deals 3 damage to target creature or player.")
        db.session.commit()
        report = classify_deck(folder)

    assert report.primary.name == "aggro"


def test_classify_control_deck(app, db_session):
    with app.app_context():
        folder = create_folder(name="Blue Control")
        # Mostly counterspells / removal, almost no creatures.
        for i in range(24):
            _seed(
                folder,
                name=f"Counter {i}",
                type_line="Instant",
                mana_value=2,
                oracle_text="Counter target spell.",
            )
        for i in range(8):
            _seed(
                folder,
                name=f"Wrath {i}",
                type_line="Sorcery",
                mana_value=4,
                oracle_text="Destroy target creature.",
            )
        db.session.commit()
        report = classify_deck(folder)

    assert report.primary.name == "control"


def test_classify_tribal_deck(app, db_session):
    with app.app_context():
        folder = create_folder(name="Merfolk Tribal")
        for i in range(30):
            _seed(folder, name=f"Merfolk {i}", type_line="Creature — Merfolk", mana_value=2)
        for i in range(5):
            _seed(folder, name=f"Land {i}", type_line="Basic Land — Island")
        db.session.commit()
        report = classify_deck(folder)

    assert report.primary.name == "tribal"


def test_classify_returns_secondary_when_scores_close(app, db_session):
    with app.app_context():
        folder = create_folder(name="Midrange-ish")
        for i in range(16):
            _seed(folder, name=f"Creature {i}", type_line="Creature", mana_value=3)
        for i in range(14):
            _seed(
                folder,
                name=f"Draw {i}",
                type_line="Sorcery",
                mana_value=3,
                oracle_text="Draw three cards.",
            )
        db.session.commit()
        report = classify_deck(folder)

    assert report.primary.name in ARCHETYPES
