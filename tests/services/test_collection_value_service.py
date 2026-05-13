"""Tests for the collection value service and snapshot history."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from core.domains.cards.services import collection_value_service
from core.shared.utils.time import utcnow
from extensions import db
from models import CollectionValueSnapshot, Folder, User
from tests.factories import create_card, create_folder


@pytest.fixture
def freeze_prices(monkeypatch):
    """Register per-card prices without needing the Scryfall bulk cache."""
    monkeypatch.setattr(collection_value_service.sc, "cache_ready", lambda: True)
    monkeypatch.setattr(collection_value_service.sc, "ensure_cache_loaded", lambda: True)

    registry: dict[str, dict] = {}

    def lookup(set_code, collector_number, name, oracle_id=None):  # noqa: ARG001
        key = f"{(set_code or '').lower()}|{(collector_number or '').lower()}|{(name or '').lower()}"
        return registry.get(key)

    monkeypatch.setattr(collection_value_service, "_lookup_print_data", lookup)
    return registry


def _register_price(registry, card, *, usd=None, usd_foil=None, eur=None, tix=None):
    prices = {}
    if usd is not None:
        prices["usd"] = str(usd)
    if usd_foil is not None:
        prices["usd_foil"] = str(usd_foil)
    if eur is not None:
        prices["eur"] = str(eur)
    if tix is not None:
        prices["tix"] = str(tix)
    registry[f"{card.set_code.lower()}|{card.collector_number.lower()}|{card.name.lower()}"] = {
        "prices": prices,
    }


def _create_user(email="collector@example.com"):
    user = User(email=email, username=email.split("@")[0])
    user.set_password("password123")
    db.session.add(user)
    db.session.flush()
    return user


def test_compute_valuation_returns_zero_when_no_folders(app, db_session, freeze_prices):
    with app.app_context():
        user = _create_user()
        report = collection_value_service.compute_valuation(user_id=user.id)
    assert report.total_value == Decimal("0")
    assert report.total_cards == 0
    assert report.folders == []


def test_compute_valuation_aggregates_per_folder_and_top_cards(app, db_session, freeze_prices):
    with app.app_context():
        user = _create_user()
        folder_a = create_folder(name="EDH Pile", category=Folder.CATEGORY_DECK)
        folder_a.owner_user_id = user.id
        folder_b = create_folder(name="Standard Box", category=Folder.CATEGORY_COLLECTION)
        folder_b.owner_user_id = user.id

        bolt = create_card(
            folder=folder_a,
            name="Lightning Bolt",
            set_code="m11",
            collector_number="146",
            quantity=4,
        )
        _register_price(freeze_prices, bolt, usd="1.50")

        reliquary = create_card(
            folder=folder_a,
            name="Reliquary Tower",
            set_code="m15",
            collector_number="238",
            quantity=1,
        )
        _register_price(freeze_prices, reliquary, usd="5.00")

        foil_bolt = create_card(
            folder=folder_b,
            name="Lightning Bolt",
            set_code="m11",
            collector_number="146",
            quantity=1,
            is_foil=True,
        )
        _register_price(freeze_prices, foil_bolt, usd="1.50", usd_foil="25.00")

        unpriced = create_card(
            folder=folder_b,
            name="Mystery Card",
            set_code="???",
            collector_number="1",
        )
        # Intentionally no price registered.
        del unpriced  # silence unused warning

        db.session.commit()
        report = collection_value_service.compute_valuation(user_id=user.id)

    assert report.total_value == Decimal("36.00")
    # 4 bolts (non-foil) + 1 reliquary + 1 foil bolt + 1 unpriced mystery
    assert report.total_cards == 7
    # priced_cards counts unique card rows with a resolved price
    assert report.priced_cards == 3
    assert report.missing_prices == 1
    assert len(report.folders) == 2

    folder_a_valuation = next(f for f in report.folders if f.folder_id == folder_a.id)
    assert folder_a_valuation.total_value == Decimal("11.00")

    top_names = [cv.name for cv in report.top_cards]
    assert top_names[0] == "Lightning Bolt"  # foil bolt is the top total
    assert report.top_cards[0].is_foil is True
    assert report.top_cards[0].total_value == Decimal("25.00")


def test_compute_valuation_respects_folder_scope(app, db_session, freeze_prices):
    with app.app_context():
        user = _create_user()
        deck = create_folder(name="Alpha Deck")
        deck.owner_user_id = user.id
        other = create_folder(name="Other")
        other.owner_user_id = user.id

        bolt = create_card(
            folder=deck,
            name="Lightning Bolt",
            set_code="m11",
            collector_number="146",
            quantity=4,
        )
        _register_price(freeze_prices, bolt, usd="1.50")

        misc = create_card(
            folder=other,
            name="Sol Ring",
            set_code="c20",
            collector_number="278",
            quantity=1,
        )
        _register_price(freeze_prices, misc, usd="3.00")

        db.session.commit()
        report = collection_value_service.compute_valuation(
            user_id=user.id, folder_id=deck.id
        )

    assert report.total_value == Decimal("6.00")
    assert len(report.folders) == 1


def test_capture_snapshot_persists_row_and_top_cards(app, db_session, freeze_prices):
    with app.app_context():
        user = _create_user()
        folder = create_folder(name="Chase Cards")
        folder.owner_user_id = user.id
        card = create_card(
            folder=folder,
            name="Ancestral Recall",
            set_code="lea",
            collector_number="48",
        )
        _register_price(freeze_prices, card, usd="5000")

        db.session.commit()
        snapshot = collection_value_service.capture_snapshot(
            user_id=user.id,
            source="manual",
        )
        db.session.commit()
        fresh = db.session.get(CollectionValueSnapshot, snapshot.id)

    assert fresh.total_value == Decimal("5000.00")
    assert fresh.currency == "usd"
    assert fresh.source == "manual"
    assert fresh.top_cards
    assert fresh.top_cards[0]["name"] == "Ancestral Recall"


def test_history_orders_oldest_first_and_respects_days(app, db_session, freeze_prices):
    with app.app_context():
        user = _create_user()
        now = utcnow()
        for offset, value in [(45, "10"), (20, "15"), (1, "20")]:
            db.session.add(
                CollectionValueSnapshot(
                    user_id=user.id,
                    folder_id=None,
                    captured_at=now - timedelta(days=offset),
                    currency="usd",
                    total_value=Decimal(value),
                    unique_cards=1,
                    total_cards=1,
                    priced_cards=1,
                    missing_prices=0,
                )
            )
        db.session.commit()
        rows = collection_value_service.history(user_id=user.id, days=30)

    assert len(rows) == 2
    assert rows[0]["total_value"] < rows[1]["total_value"]


def test_compare_periods_returns_delta_and_percent(app, db_session, freeze_prices):
    with app.app_context():
        user = _create_user()
        now = utcnow()
        db.session.add(
            CollectionValueSnapshot(
                user_id=user.id,
                folder_id=None,
                captured_at=now - timedelta(days=20),
                currency="usd",
                total_value=Decimal("100"),
                unique_cards=1,
                total_cards=1,
                priced_cards=1,
                missing_prices=0,
            )
        )
        folder = create_folder(name="Gainers")
        folder.owner_user_id = user.id
        card = create_card(folder=folder, name="Chase Rare", set_code="aaa", collector_number="1")
        _register_price(freeze_prices, card, usd="150")
        db.session.commit()

        trend = collection_value_service.compare_periods(user_id=user.id, days=30)

    assert Decimal(trend["baseline"]["total_value"]) == Decimal("100")
    assert trend["current"]["total_value"] == "150.00"
    assert trend["delta"]["absolute"] == "50.00"
    assert trend["delta"]["percent"] == pytest.approx(50.0)


def test_invalid_currency_raises(app, db_session):
    with app.app_context():
        user = _create_user()
        with pytest.raises(ValueError):
            collection_value_service.compute_valuation(user_id=user.id, currency="gbp")
