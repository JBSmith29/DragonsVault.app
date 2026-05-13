"""Tests for mana base analysis."""

from __future__ import annotations

import pytest

from core.domains.decks.services import mana_base_analysis_service
from extensions import db
from tests.factories import create_card, create_folder


@pytest.fixture
def freeze_cache(monkeypatch):
    monkeypatch.setattr(mana_base_analysis_service.sc, "cache_ready", lambda: True)
    monkeypatch.setattr(mana_base_analysis_service.sc, "ensure_cache_loaded", lambda: True)
    registry: dict[str, dict] = {}

    def lookup(set_code, collector_number, name, oracle_id=None):  # noqa: ARG001
        key = f"{(set_code or '').lower()}|{(collector_number or '').lower()}|{(name or '').lower()}"
        return registry.get(key)

    monkeypatch.setattr(mana_base_analysis_service, "_lookup_print_data", lookup)
    return registry


def _land(folder, *, name, quantity=1, type_line="Land", oracle_text="", color_identity=""):
    card = create_card(
        folder=folder,
        name=name,
        set_code="tst",
        collector_number=str(abs(hash(name)) % 1000),
        quantity=quantity,
    )
    card.type_line = type_line
    card.oracle_text = oracle_text
    card.color_identity = color_identity
    db.session.flush()
    return card


def test_analyze_mana_base_counts_lands_and_basics(app, db_session, freeze_cache):
    with app.app_context():
        folder = create_folder(name="Mono-Green")
        for idx in range(37):
            _land(
                folder,
                name=f"Forest {idx}",  # differentiate by name so we have 37 rows
                type_line="Basic Land — Forest",
            )
        # Non-land card to make total_cards meaningful.
        spell = create_card(folder=folder, name="Sol Ring", set_code="tst", collector_number="X")
        spell.type_line = "Artifact"
        db.session.commit()

        # Rename them all to "Forest" for realistic land classification.
        for card in folder.cards:
            if "Forest" in card.name:
                card.name = "Forest"
        db.session.commit()

        report = mana_base_analysis_service.analyze_mana_base(folder)

    assert report.total_lands == 37
    assert report.category_counts["basic"] == 37
    assert report.untapped_lands == 37
    assert report.tapped_lands == 0
    # Green sources should be populated from the Forest basic type.
    assert report.color_sources["G"] == 37


def test_analyze_mana_base_flags_too_few_lands(app, db_session, freeze_cache):
    with app.app_context():
        folder = create_folder(name="Spell Pile")
        _land(folder, name="Forest", type_line="Basic Land — Forest")  # only one land
        spell = create_card(folder=folder, name="Lightning Bolt", set_code="tst", collector_number="1")
        spell.type_line = "Instant"
        db.session.commit()

        report = mana_base_analysis_service.analyze_mana_base(folder)

    assert any("lands" in warning.lower() for warning in report.warnings)


def test_analyze_mana_base_classifies_tapped_lands(app, db_session, freeze_cache):
    with app.app_context():
        folder = create_folder(name="Tap Town")
        # Plenty of lands, all ETB tapped.
        for idx in range(36):
            _land(
                folder,
                name=f"Guildgate {idx}",
                type_line="Land — Gate",
                oracle_text="~ enters tapped.",
            )
        db.session.commit()
        for card in folder.cards:
            card.name = "Azorius Guildgate"
        db.session.commit()

        report = mana_base_analysis_service.analyze_mana_base(folder)

    assert report.tapped_lands == 36
    assert any("tapped" in warning.lower() for warning in report.warnings)


def test_analyze_mana_base_detects_fetch_like_wording(app, db_session, freeze_cache):
    with app.app_context():
        folder = create_folder(name="Fetch Experiment")
        _land(
            folder,
            name="Flooded Strand",
            type_line="Land",
            oracle_text=(
                "{T}, Pay 1 life, Sacrifice Flooded Strand: "
                "Search your library for a Plains or Island card, put it onto the battlefield, "
                "then shuffle."
            ),
        )
        db.session.commit()
        report = mana_base_analysis_service.analyze_mana_base(folder)

    assert report.category_counts.get("fetch", 0) == 1
