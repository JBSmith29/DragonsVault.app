"""Tests for budget-alternative suggestions."""

from __future__ import annotations

from decimal import Decimal

import pytest

from core.domains.decks.services import budget_alternatives_service
from extensions import db
from models import Folder, User
from tests.factories import create_card, create_folder


@pytest.fixture
def freeze_prices(monkeypatch):
    monkeypatch.setattr(budget_alternatives_service.sc, "cache_ready", lambda: True)
    monkeypatch.setattr(budget_alternatives_service.sc, "ensure_cache_loaded", lambda: True)
    monkeypatch.setattr(budget_alternatives_service.sc, "get_all_prints", lambda: {})
    registry: dict[str, dict] = {}

    def lookup(set_code, collector_number, name, oracle_id=None):  # noqa: ARG001
        key = f"{(set_code or '').lower()}|{(collector_number or '').lower()}|{(name or '').lower()}"
        return registry.get(key)

    monkeypatch.setattr(budget_alternatives_service, "_lookup_print_data", lookup)
    return registry


def _register(registry, card, *, price_usd, color_identity=()):
    registry[
        f"{card.set_code.lower()}|{card.collector_number.lower()}|{card.name.lower()}"
    ] = {
        "prices": {"usd": str(price_usd)},
        "color_identity": list(color_identity),
        "type_line": card.type_line,
        "oracle_id": card.oracle_id,
        "name": card.name,
    }


def _create_user():
    user = User(email="budget@example.com", username="budget")
    user.set_password("password123")
    db.session.add(user)
    db.session.flush()
    return user


def test_find_budget_alternatives_suggests_cheaper_owned_cards(
    app, db_session, freeze_prices
):
    with app.app_context():
        user = _create_user()
        deck = create_folder(name="Expensive Deck")
        deck.owner_user_id = user.id

        # Expensive card in the deck.
        jitte = create_card(
            folder=deck,
            name="Umezawa's Jitte",
            set_code="bok",
            collector_number="154",
            oracle_id="oracle-jitte",
        )
        jitte.type_line = "Legendary Artifact — Equipment"
        jitte.color_identity = ""
        _register(freeze_prices, jitte, price_usd="50.00")

        # Cheaper equipment in another folder (still user-owned).
        other_folder = create_folder(name="Binder")
        other_folder.owner_user_id = user.id
        swiftfoot = create_card(
            folder=other_folder,
            name="Swiftfoot Boots",
            set_code="m13",
            collector_number="217",
            oracle_id="oracle-swiftfoot",
        )
        swiftfoot.type_line = "Artifact — Equipment"
        swiftfoot.color_identity = ""
        _register(freeze_prices, swiftfoot, price_usd="1.50")

        db.session.commit()
        report = budget_alternatives_service.find_budget_alternatives(
            user_id=user.id,
            folder=deck,
            threshold_usd=Decimal("10"),
        )

    assert len(report.suggestions) == 1
    slot = report.suggestions[0]
    assert slot.name == "Umezawa's Jitte"
    assert any(alt.name == "Swiftfoot Boots" for alt in slot.alternatives)
    assert all(alt.in_user_collection for alt in slot.alternatives)


def test_find_budget_alternatives_skips_when_no_expensive_cards(
    app, db_session, freeze_prices
):
    with app.app_context():
        user = _create_user()
        deck = create_folder(name="Cheap Deck")
        deck.owner_user_id = user.id
        card = create_card(
            folder=deck,
            name="Lightning Bolt",
            set_code="m11",
            collector_number="146",
        )
        card.type_line = "Instant"
        _register(freeze_prices, card, price_usd="2.00")
        db.session.commit()
        report = budget_alternatives_service.find_budget_alternatives(
            user_id=user.id,
            folder=deck,
            threshold_usd=Decimal("10"),
        )
    assert report.suggestions == []


def test_find_budget_alternatives_rejects_invalid_threshold(app, db_session, freeze_prices):
    with app.app_context():
        user = _create_user()
        deck = create_folder(name="Empty Deck")
        deck.owner_user_id = user.id
        db.session.commit()
        with pytest.raises(ValueError):
            budget_alternatives_service.find_budget_alternatives(
                user_id=user.id, folder=deck, threshold_usd=0
            )
