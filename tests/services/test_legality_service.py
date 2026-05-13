"""Tests for the deck legality checker.

Scryfall cache access is monkeypatched to avoid requiring the bulk cache on
test runs. The scenarios cover each rule the service enforces so regressions
surface clearly.
"""

from __future__ import annotations

from typing import Any

import pytest

from core.domains.decks.services import legality_service
from extensions import db
from models import Card, Folder
from tests.factories import create_card, create_folder


def _mock_print(
    *,
    name: str,
    commander: str = "legal",
    color_identity: tuple[str, ...] = (),
    type_line: str = "Creature",
    legalities: dict[str, str] | None = None,
) -> dict[str, Any]:
    all_legalities = {"commander": commander}
    if legalities:
        all_legalities.update(legalities)
    return {
        "name": name,
        "type_line": type_line,
        "color_identity": list(color_identity),
        "legalities": all_legalities,
    }


@pytest.fixture
def freeze_cache(monkeypatch):
    """Pretend the Scryfall cache is loaded; callers register per-card data."""
    monkeypatch.setattr(legality_service.sc, "cache_ready", lambda: True)
    monkeypatch.setattr(legality_service.sc, "ensure_cache_loaded", lambda: True)

    registry: dict[tuple[str, str, str], dict[str, Any]] = {}

    def lookup(set_code, collector_number, name, oracle_id=None):  # noqa: ARG001 - matches real sig
        key = (
            (set_code or "").lower(),
            (collector_number or "").lower(),
            (name or "").lower(),
        )
        return registry.get(key)

    monkeypatch.setattr(legality_service, "_lookup_print_data", lookup)
    return registry


def _register(registry, card: Card, **print_kwargs) -> None:
    registry[(card.set_code.lower(), card.collector_number.lower(), card.name.lower())] = (
        _mock_print(name=card.name, **print_kwargs)
    )


def test_available_formats_exposes_public_descriptors(app, db_session):
    with app.app_context():
        data = legality_service.available_formats()
    keys = {fmt["key"] for fmt in data}
    assert {"commander", "standard", "modern", "legacy", "vintage", "pauper"}.issubset(keys)
    for fmt in data:
        assert {"label", "max_copies", "singleton"}.issubset(fmt)


def test_unsupported_format_raises_value_error(app, db_session):
    with app.app_context():
        folder = create_folder(name="Fake")
        with pytest.raises(ValueError):
            legality_service.evaluate_folder_legality(folder, "pre-release")


def test_commander_happy_path_is_legal(app, db_session, freeze_cache):
    with app.app_context():
        folder = create_folder(name="Atraxa Superfriends")
        folder.commander_name = "Atraxa, Praetors' Voice"
        folder.commander_oracle_id = "oracle-atraxa"

        commander = create_card(
            folder=folder,
            name="Atraxa, Praetors' Voice",
            oracle_id="oracle-atraxa",
            set_code="cmd",
            collector_number="001",
        )
        _register(freeze_cache, commander, color_identity=("W", "U", "B", "G"))

        # Fill to exactly 100 cards (99 basics + 1 commander).
        for idx in range(99):
            basic = create_card(
                folder=folder,
                name="Plains",
                set_code="snc",
                collector_number=str(300 + idx),
            )
            _register(freeze_cache, basic, color_identity=("W",), type_line="Basic Land — Plains")
        db.session.commit()

        report = legality_service.evaluate_folder_legality(folder, "commander")

    assert report.legal is True
    assert report.mainboard_size == 100
    assert report.summary["error"] == 0


def test_commander_detects_deck_size_and_singleton_violations(app, db_session, freeze_cache):
    with app.app_context():
        folder = create_folder(name="Bad Deck")
        folder.commander_name = "Atraxa, Praetors' Voice"

        commander = create_card(
            folder=folder,
            name="Atraxa, Praetors' Voice",
            oracle_id="oracle-atraxa",
            set_code="cmd",
            collector_number="001",
        )
        _register(freeze_cache, commander, color_identity=("W", "U", "B", "G"))

        sol_ring = create_card(
            folder=folder,
            name="Sol Ring",
            oracle_id="oracle-sol",
            set_code="c20",
            collector_number="278",
            quantity=2,
        )
        _register(freeze_cache, sol_ring, type_line="Artifact")

        db.session.commit()
        report = legality_service.evaluate_folder_legality(folder, "commander")

    codes = {issue.code for issue in report.issues}
    assert "deck_size" in codes
    assert "copy_limit" in codes
    assert report.legal is False


def test_commander_enforces_color_identity(app, db_session, freeze_cache):
    with app.app_context():
        folder = create_folder(name="Azorius Only")
        folder.commander_name = "Brago, King Eternal"
        folder.commander_oracle_id = "oracle-brago"

        commander = create_card(
            folder=folder,
            name="Brago, King Eternal",
            oracle_id="oracle-brago",
            set_code="cnf",
            collector_number="1",
        )
        _register(freeze_cache, commander, color_identity=("W", "U"))

        # Red card is outside the commander's identity.
        offender = create_card(
            folder=folder,
            name="Lightning Bolt",
            oracle_id="oracle-bolt",
            set_code="lea",
            collector_number="161",
        )
        _register(freeze_cache, offender, color_identity=("R",), type_line="Instant")
        db.session.commit()

        report = legality_service.evaluate_folder_legality(folder, "commander")

    identity_issues = [i for i in report.issues if i.code == "color_identity"]
    assert len(identity_issues) == 1
    assert identity_issues[0].card_name == "Lightning Bolt"
    assert identity_issues[0].severity == "error"


def test_modern_copy_limit_respects_four_of(app, db_session, freeze_cache):
    with app.app_context():
        folder = create_folder(name="Modern Burn")
        db.session.flush()

        bolt = create_card(
            folder=folder,
            name="Lightning Bolt",
            oracle_id="oracle-bolt",
            set_code="m11",
            collector_number="146",
            quantity=5,  # one over the limit
        )
        _register(
            freeze_cache,
            bolt,
            type_line="Instant",
            legalities={"modern": "legal"},
        )

        db.session.commit()
        report = legality_service.evaluate_folder_legality(folder, "modern")

    copy_issues = [i for i in report.issues if i.code == "copy_limit"]
    assert copy_issues, "Expected a copy-limit error for 5 bolts"
    assert copy_issues[0].meta["count"] == 5
    assert copy_issues[0].meta["allowed"] == 4


def test_modern_detects_banned_card(app, db_session, freeze_cache):
    with app.app_context():
        folder = create_folder(name="Maybe Not Modern")

        banned_card = create_card(
            folder=folder,
            name="Mental Misstep",
            oracle_id="oracle-misstep",
            set_code="nph",
            collector_number="33",
        )
        _register(
            freeze_cache,
            banned_card,
            type_line="Instant",
            legalities={"modern": "banned"},
        )
        db.session.commit()
        report = legality_service.evaluate_folder_legality(folder, "modern")

    assert any(i.code == "card_not_legal" for i in report.issues)
    assert report.legal is False


def test_unknown_legality_is_warning_not_error(app, db_session, freeze_cache):
    with app.app_context():
        folder = create_folder(name="Unknown Format Deck")

        mystery = create_card(
            folder=folder,
            name="Mystery Card",
            oracle_id="oracle-mystery",
            set_code="???",
            collector_number="1",
        )
        # Register a print without any modern legality entry.
        _register(freeze_cache, mystery, type_line="Sorcery", legalities={})
        db.session.commit()

        report = legality_service.evaluate_folder_legality(folder, "modern")

    warning_codes = {i.code for i in report.issues if i.severity == "warning"}
    assert "card_legality_unknown" in warning_codes
    # The deck still fails for being too small, but the unknown card is a warning
    # not an error.
    assert report.summary["warning"] >= 1


def test_vintage_allows_restricted(app, db_session, freeze_cache):
    with app.app_context():
        folder = create_folder(name="Vintage Storm")

        black_lotus = create_card(
            folder=folder,
            name="Black Lotus",
            oracle_id="oracle-lotus",
            set_code="lea",
            collector_number="232",
        )
        _register(
            freeze_cache,
            black_lotus,
            type_line="Artifact",
            legalities={"vintage": "restricted"},
        )
        db.session.commit()
        report = legality_service.evaluate_folder_legality(folder, "vintage")

    assert not any(i.code == "card_not_legal" for i in report.issues)


def test_basic_lands_bypass_singleton(app, db_session, freeze_cache):
    with app.app_context():
        folder = create_folder(name="Mono-Green")
        folder.commander_name = "Azusa, Lost but Seeking"
        folder.commander_oracle_id = "oracle-azusa"

        commander = create_card(
            folder=folder,
            name="Azusa, Lost but Seeking",
            oracle_id="oracle-azusa",
            set_code="chk",
            collector_number="245",
        )
        _register(freeze_cache, commander, color_identity=("G",), type_line="Legendary Creature — Human Monk")

        # 99 forests + commander shouldn't trip the copy-limit rule.
        for idx in range(99):
            forest = create_card(
                folder=folder,
                name="Forest",
                set_code="znr",
                collector_number=str(269 + idx),
            )
            _register(freeze_cache, forest, color_identity=("G",), type_line="Basic Land — Forest")
        db.session.commit()
        report = legality_service.evaluate_folder_legality(folder, "commander")

    assert not any(i.code == "copy_limit" for i in report.issues)
    assert report.mainboard_size == 100
