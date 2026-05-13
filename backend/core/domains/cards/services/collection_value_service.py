"""Collection value tracking: snapshots, totals, and trend analysis.

This service has two responsibilities:

1. **Compute a live valuation** of a user's collection at any point in time
   by aggregating Scryfall prices across accessible folders. ``compute_valuation``
   returns a rich payload with per-folder breakdowns and top cards.

2. **Persist daily snapshots** so the UI can render trend charts and
   highlight biggest gainers/losers over rolling windows. ``capture_snapshot``
   writes a row to ``collection_value_snapshots``; ``history`` and
   ``compare_periods`` read from that table.

The service intentionally does not trigger HTTP calls to the price service or
Scryfall API. It reads from the in-memory bulk cache populated at startup so
web requests stay fast.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import selectinload

from extensions import db
from models import CollectionValueSnapshot, Folder
from core.domains.cards.services.pricing import price_has_value, prices_for_print
from core.domains.cards.services import scryfall_cache as sc
from core.shared.utils.time import utcnow
from shared.mtg import _lookup_print_data


__all__ = [
    "CardValuation",
    "FolderValuation",
    "ValuationReport",
    "VALID_CURRENCIES",
    "compute_valuation",
    "capture_snapshot",
    "history",
    "compare_periods",
]


#: Scryfall price keys grouped by currency.
_PRICE_KEYS_BY_CURRENCY: dict[str, tuple[str, str | None]] = {
    "usd": ("usd", "usd_foil"),
    "eur": ("eur", "eur_foil"),
    "tix": ("tix", None),
}

VALID_CURRENCIES: tuple[str, ...] = tuple(_PRICE_KEYS_BY_CURRENCY)


# ---------------------------------------------------------------------------
# Value dataclasses
# ---------------------------------------------------------------------------


@dataclass
class CardValuation:
    card_id: int
    folder_id: int
    name: str
    quantity: int
    is_foil: bool
    unit_price: Decimal
    total_value: Decimal
    set_code: str
    collector_number: str
    oracle_id: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "card_id": self.card_id,
            "folder_id": self.folder_id,
            "name": self.name,
            "quantity": self.quantity,
            "is_foil": self.is_foil,
            "unit_price": str(self.unit_price),
            "total_value": str(self.total_value),
            "set_code": self.set_code,
            "collector_number": self.collector_number,
            "oracle_id": self.oracle_id,
        }


@dataclass
class FolderValuation:
    folder_id: int
    name: str
    category: str
    total_value: Decimal
    unique_cards: int
    total_cards: int
    priced_cards: int
    missing_prices: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "folder_id": self.folder_id,
            "name": self.name,
            "category": self.category,
            "total_value": str(self.total_value),
            "unique_cards": self.unique_cards,
            "total_cards": self.total_cards,
            "priced_cards": self.priced_cards,
            "missing_prices": self.missing_prices,
        }


@dataclass
class ValuationReport:
    user_id: int
    folder_id: int | None
    currency: str
    captured_at: datetime
    total_value: Decimal
    unique_cards: int
    total_cards: int
    priced_cards: int
    missing_prices: int
    folders: list[FolderValuation] = field(default_factory=list)
    top_cards: list[CardValuation] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "folder_id": self.folder_id,
            "currency": self.currency,
            "captured_at": self.captured_at.isoformat(),
            "total_value": str(self.total_value),
            "unique_cards": self.unique_cards,
            "total_cards": self.total_cards,
            "priced_cards": self.priced_cards,
            "missing_prices": self.missing_prices,
            "folders": [f.to_dict() for f in self.folders],
            "top_cards": [c.to_dict() for c in self.top_cards],
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_currency(currency: str | None) -> str:
    normalized = (currency or "usd").strip().lower()
    if normalized not in VALID_CURRENCIES:
        raise ValueError(f"Unsupported currency: {currency!r}")
    return normalized


def _owned_folder_ids(user_id: int, folder_id: int | None = None) -> list[int]:
    """Return folder ids the user owns (shared folders are excluded for valuation).

    Valuation is scoped to what the user personally owns so totals match
    expectations. Friend and shared folders are excluded even if readable.
    """
    query = db.session.query(Folder.id).filter(Folder.owner_user_id == user_id)
    if folder_id is not None:
        query = query.filter(Folder.id == folder_id)
    return [row[0] for row in query.all()]


def _price_for_card(print_data: dict | None, currency: str, is_foil: bool) -> Decimal:
    if not print_data:
        return Decimal("0")
    prices = prices_for_print(print_data)
    if not price_has_value(prices):
        return Decimal("0")
    regular_key, foil_key = _PRICE_KEYS_BY_CURRENCY[currency]
    if is_foil and foil_key:
        value = prices.get(foil_key)
        if value in (None, "", 0):
            value = prices.get(regular_key)
    else:
        value = prices.get(regular_key)
        if value in (None, "", 0) and currency == "usd":
            # USD often has etched prices separately; fall back when normal is empty.
            value = prices.get("usd_etched") or value
    try:
        decimal_value = Decimal(str(value or 0))
    except Exception:
        return Decimal("0")
    return decimal_value if decimal_value > 0 else Decimal("0")


def _round_currency(value: Decimal) -> Decimal:
    """Round to 2 decimal places the same way SQL ``NUMERIC(12, 2)`` would."""
    return value.quantize(Decimal("0.01"))


# ---------------------------------------------------------------------------
# Public valuation API
# ---------------------------------------------------------------------------


def compute_valuation(
    *,
    user_id: int,
    folder_id: int | None = None,
    currency: str = "usd",
    top_n: int = 10,
) -> ValuationReport:
    """Compute the current valuation of a user's collection.

    ``folder_id`` limits the scope to a single folder. ``top_n`` controls how
    many highest-value cards are returned alongside the summary.
    """
    normalized_currency = _validate_currency(currency)
    if not sc.cache_ready():
        sc.ensure_cache_loaded()

    folder_ids = _owned_folder_ids(user_id, folder_id=folder_id)
    if not folder_ids:
        return ValuationReport(
            user_id=user_id,
            folder_id=folder_id,
            currency=normalized_currency,
            captured_at=utcnow(),
            total_value=Decimal("0"),
            unique_cards=0,
            total_cards=0,
            priced_cards=0,
            missing_prices=0,
        )

    folders = (
        db.session.query(Folder)
        .filter(Folder.id.in_(folder_ids))
        .options(selectinload(Folder.cards))
        .all()
    )
    folders_by_id = {folder.id: folder for folder in folders}

    folder_aggregates: dict[int, FolderValuation] = {
        fid: FolderValuation(
            folder_id=fid,
            name=folders_by_id[fid].name,
            category=folders_by_id[fid].category or "collection",
            total_value=Decimal("0"),
            unique_cards=0,
            total_cards=0,
            priced_cards=0,
            missing_prices=0,
        )
        for fid in folder_ids
        if fid in folders_by_id
    }

    card_valuations: list[CardValuation] = []
    missing_prices = 0
    priced_cards = 0

    for folder in folders:
        for card in folder.cards:
            qty = max(0, int(card.quantity or 0))
            if qty <= 0:
                continue
            print_data = _lookup_print_data(
                getattr(card, "set_code", None),
                getattr(card, "collector_number", None),
                getattr(card, "name", None),
                getattr(card, "oracle_id", None),
            )
            unit = _price_for_card(print_data, normalized_currency, bool(card.is_foil))
            total = unit * qty
            aggregate = folder_aggregates[folder.id]
            aggregate.unique_cards += 1
            aggregate.total_cards += qty
            if unit > 0:
                aggregate.priced_cards += 1
                aggregate.total_value += total
                priced_cards += 1
            else:
                aggregate.missing_prices += 1
                missing_prices += 1

            if unit > 0:
                card_valuations.append(
                    CardValuation(
                        card_id=card.id,
                        folder_id=folder.id,
                        name=card.name,
                        quantity=qty,
                        is_foil=bool(card.is_foil),
                        unit_price=_round_currency(unit),
                        total_value=_round_currency(total),
                        set_code=card.set_code or "",
                        collector_number=card.collector_number or "",
                        oracle_id=card.oracle_id,
                    )
                )

    total_value = sum(
        (aggregate.total_value for aggregate in folder_aggregates.values()),
        Decimal("0"),
    )
    unique_cards = sum(aggregate.unique_cards for aggregate in folder_aggregates.values())
    total_cards = sum(aggregate.total_cards for aggregate in folder_aggregates.values())

    # Round folder aggregates now that we're done summing.
    for aggregate in folder_aggregates.values():
        aggregate.total_value = _round_currency(aggregate.total_value)

    top_cards = sorted(
        card_valuations,
        key=lambda cv: (cv.total_value, cv.unit_price),
        reverse=True,
    )[: max(0, int(top_n))]

    ordered_folders = sorted(
        folder_aggregates.values(),
        key=lambda agg: (agg.total_value, agg.unique_cards),
        reverse=True,
    )

    return ValuationReport(
        user_id=user_id,
        folder_id=folder_id,
        currency=normalized_currency,
        captured_at=utcnow(),
        total_value=_round_currency(total_value),
        unique_cards=unique_cards,
        total_cards=total_cards,
        priced_cards=priced_cards,
        missing_prices=missing_prices,
        folders=ordered_folders,
        top_cards=top_cards,
    )


def capture_snapshot(
    *,
    user_id: int,
    folder_id: int | None = None,
    currency: str = "usd",
    source: str | None = None,
    top_n: int = 20,
) -> CollectionValueSnapshot:
    """Compute a valuation and persist it for historical charts."""
    report = compute_valuation(
        user_id=user_id,
        folder_id=folder_id,
        currency=currency,
        top_n=top_n,
    )
    snapshot = CollectionValueSnapshot(
        user_id=user_id,
        folder_id=folder_id,
        captured_at=report.captured_at,
        currency=report.currency,
        total_value=report.total_value,
        unique_cards=report.unique_cards,
        total_cards=report.total_cards,
        priced_cards=report.priced_cards,
        missing_prices=report.missing_prices,
        top_cards=[card.to_dict() for card in report.top_cards],
        source=source,
    )
    db.session.add(snapshot)
    db.session.flush()
    return snapshot


def history(
    *,
    user_id: int,
    folder_id: int | None = None,
    currency: str = "usd",
    days: int | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Return historical snapshots ordered oldest -> newest."""
    normalized_currency = _validate_currency(currency)
    query = CollectionValueSnapshot.query.filter(
        CollectionValueSnapshot.user_id == user_id,
        CollectionValueSnapshot.currency == normalized_currency,
    )
    if folder_id is None:
        query = query.filter(CollectionValueSnapshot.folder_id.is_(None))
    else:
        query = query.filter(CollectionValueSnapshot.folder_id == folder_id)
    if days is not None:
        cutoff = utcnow() - timedelta(days=int(days))
        query = query.filter(CollectionValueSnapshot.captured_at >= cutoff)

    rows = query.order_by(CollectionValueSnapshot.captured_at.asc()).all()
    if limit:
        rows = rows[-int(limit) :]
    return [_snapshot_to_dict(snapshot) for snapshot in rows]


def compare_periods(
    *,
    user_id: int,
    folder_id: int | None = None,
    currency: str = "usd",
    days: int = 30,
) -> dict[str, Any]:
    """Return a delta summary between the oldest snapshot in the window and now."""
    normalized_currency = _validate_currency(currency)
    window_history = history(
        user_id=user_id,
        folder_id=folder_id,
        currency=normalized_currency,
        days=days,
    )
    current = compute_valuation(
        user_id=user_id,
        folder_id=folder_id,
        currency=normalized_currency,
        top_n=0,
    )

    baseline = window_history[0] if window_history else None
    baseline_value = Decimal(baseline["total_value"]) if baseline else Decimal("0")
    delta = current.total_value - baseline_value
    percent = None
    if baseline_value > 0:
        percent = float((delta / baseline_value) * 100)
    return {
        "currency": normalized_currency,
        "days": days,
        "baseline": baseline,
        "current": {
            "captured_at": current.captured_at.isoformat(),
            "total_value": str(current.total_value),
            "unique_cards": current.unique_cards,
            "total_cards": current.total_cards,
        },
        "delta": {
            "absolute": str(_round_currency(delta)),
            "percent": percent,
        },
    }


# ---------------------------------------------------------------------------
# Serialization helper
# ---------------------------------------------------------------------------


def _snapshot_to_dict(snapshot: CollectionValueSnapshot) -> dict[str, Any]:
    return {
        "id": snapshot.id,
        "captured_at": snapshot.captured_at.isoformat(),
        "currency": snapshot.currency,
        "total_value": str(snapshot.total_value),
        "unique_cards": snapshot.unique_cards,
        "total_cards": snapshot.total_cards,
        "priced_cards": snapshot.priced_cards,
        "missing_prices": snapshot.missing_prices,
        "folder_id": snapshot.folder_id,
        "source": snapshot.source,
    }
