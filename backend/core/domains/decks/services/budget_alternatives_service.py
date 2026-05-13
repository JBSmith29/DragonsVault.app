"""Suggest budget alternatives for expensive cards in a deck.

Given a deck folder, find cards whose market price exceeds a threshold and
for each one suggest up to a handful of cheaper alternatives that share the
card's "role" (defined by its primary type and oracle-role tagging when
available). Suggestions come from the user's collection when possible so the
final pick is already owned; otherwise we fall back to Scryfall cache entries
that match the type line and color identity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Iterable

from sqlalchemy.orm import selectinload

from extensions import db
from models import Card, Folder
from core.domains.cards.services import scryfall_cache as sc
from core.domains.cards.services.pricing import price_has_value, prices_for_print
from shared.mtg import _lookup_print_data


__all__ = [
    "BudgetSuggestion",
    "BudgetAlternativesReport",
    "find_budget_alternatives",
]


DEFAULT_EXPENSIVE_THRESHOLD = Decimal("20.00")
DEFAULT_SUGGESTIONS_PER_CARD = 5


@dataclass
class BudgetSuggestion:
    name: str
    oracle_id: str | None
    type_line: str | None
    color_identity: list[str]
    price_usd: Decimal | None
    in_user_collection: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "oracle_id": self.oracle_id,
            "type_line": self.type_line,
            "color_identity": list(self.color_identity),
            "price_usd": str(self.price_usd) if self.price_usd is not None else None,
            "in_user_collection": self.in_user_collection,
        }


@dataclass
class ExpensiveSlot:
    name: str
    oracle_id: str | None
    price_usd: Decimal
    type_line: str | None
    color_identity: list[str]
    alternatives: list[BudgetSuggestion] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "oracle_id": self.oracle_id,
            "type_line": self.type_line,
            "color_identity": list(self.color_identity),
            "price_usd": str(self.price_usd),
            "alternatives": [alt.to_dict() for alt in self.alternatives],
        }


@dataclass
class BudgetAlternativesReport:
    folder_id: int
    threshold_usd: Decimal
    currency: str
    suggestions: list[ExpensiveSlot]

    def to_dict(self) -> dict[str, Any]:
        return {
            "folder_id": self.folder_id,
            "threshold_usd": str(self.threshold_usd),
            "currency": self.currency,
            "suggestions": [slot.to_dict() for slot in self.suggestions],
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _usd_price(print_data: dict | None) -> Decimal | None:
    if not print_data:
        return None
    prices = prices_for_print(print_data)
    if not price_has_value(prices):
        return None
    try:
        value = Decimal(str(prices.get("usd") or 0))
    except Exception:
        return None
    return value if value > 0 else None


def _primary_type(type_line: str | None) -> str:
    lowered = (type_line or "").lower()
    for label in ("planeswalker", "battle", "creature", "land", "artifact", "enchantment", "instant", "sorcery"):
        if label in lowered:
            return label
    return ""


def _color_identity_from_print(print_data: dict | None) -> list[str]:
    if not print_data:
        return []
    return sorted(str(ch).upper() for ch in (print_data.get("color_identity") or []) if ch)


def _same_or_subset(colors_a: Iterable[str], colors_b: Iterable[str]) -> bool:
    set_a, set_b = set(colors_a), set(colors_b)
    return set_a.issubset(set_b)


# ---------------------------------------------------------------------------
# Collection candidate search
# ---------------------------------------------------------------------------


def _collection_candidates(
    user_id: int,
    *,
    primary_type: str,
    deck_identity: set[str],
    exclude_oracle_ids: set[str],
    price_ceiling: Decimal,
) -> list[BudgetSuggestion]:
    if not primary_type:
        return []
    query = (
        db.session.query(Card)
        .join(Folder, Folder.id == Card.folder_id)
        .filter(Folder.owner_user_id == user_id)
    )
    candidates: list[BudgetSuggestion] = []
    seen_oracles: set[str] = set()
    for card in query.limit(5000).all():
        oracle_id = (card.oracle_id or "").strip()
        if oracle_id and oracle_id in exclude_oracle_ids:
            continue
        if oracle_id and oracle_id in seen_oracles:
            continue
        type_line = (card.type_line or "").lower()
        if primary_type not in type_line:
            continue
        pr = _lookup_print_data(card.set_code, card.collector_number, card.name, card.oracle_id)
        identity = _color_identity_from_print(pr)
        if deck_identity and not _same_or_subset(identity, deck_identity):
            continue
        price = _usd_price(pr)
        if price is None or price > price_ceiling:
            continue
        if oracle_id:
            seen_oracles.add(oracle_id)
        candidates.append(
            BudgetSuggestion(
                name=card.name,
                oracle_id=card.oracle_id,
                type_line=card.type_line,
                color_identity=identity,
                price_usd=price,
                in_user_collection=True,
            )
        )
    candidates.sort(key=lambda s: (s.price_usd or Decimal("0")))
    return candidates


# ---------------------------------------------------------------------------
# Scryfall cache candidate search
# ---------------------------------------------------------------------------


def _cache_candidates(
    *,
    primary_type: str,
    deck_identity: set[str],
    exclude_oracle_ids: set[str],
    price_ceiling: Decimal,
    limit: int,
) -> list[BudgetSuggestion]:
    if not primary_type:
        return []
    if not sc.cache_ready():
        sc.ensure_cache_loaded()
    all_prints = sc.get_all_prints() or {}
    results: list[BudgetSuggestion] = []
    seen_oracles: set[str] = set()
    for print_obj in all_prints.values() if isinstance(all_prints, dict) else all_prints:
        if len(results) >= limit:
            break
        oracle_id = (print_obj.get("oracle_id") or "").strip()
        if not oracle_id or oracle_id in exclude_oracle_ids or oracle_id in seen_oracles:
            continue
        type_line = (print_obj.get("type_line") or "").lower()
        if primary_type not in type_line:
            continue
        identity = _color_identity_from_print(print_obj)
        if deck_identity and not _same_or_subset(identity, deck_identity):
            continue
        price = _usd_price(print_obj)
        if price is None or price > price_ceiling:
            continue
        seen_oracles.add(oracle_id)
        results.append(
            BudgetSuggestion(
                name=print_obj.get("name") or "Unknown",
                oracle_id=oracle_id,
                type_line=print_obj.get("type_line"),
                color_identity=identity,
                price_usd=price,
                in_user_collection=False,
            )
        )
    results.sort(key=lambda s: (s.price_usd or Decimal("0")))
    return results[:limit]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def find_budget_alternatives(
    *,
    user_id: int,
    folder: Folder,
    threshold_usd: Decimal | str | float = DEFAULT_EXPENSIVE_THRESHOLD,
    max_price_multiplier: Decimal = Decimal("0.25"),
    suggestions_per_card: int = DEFAULT_SUGGESTIONS_PER_CARD,
) -> BudgetAlternativesReport:
    """Return alternative suggestions for every card above the threshold."""
    threshold = Decimal(str(threshold_usd))
    if threshold <= 0:
        raise ValueError("threshold_usd must be positive")
    suggestions_per_card = max(1, int(suggestions_per_card))

    folder = (
        db.session.query(Folder)
        .options(selectinload(Folder.cards))
        .filter(Folder.id == folder.id)
        .one()
    )

    deck_identity: set[str] = set()
    for ch in (folder.commander_name or ""):
        pass  # commander color identity is derived from the deck rows below

    expensive_slots: list[ExpensiveSlot] = []
    exclude_oracles: set[str] = set()
    # Accumulate the deck-wide color identity from the card rows so we don't
    # recommend cards outside the deck's legal colors.
    for card in folder.cards:
        for ch in (card.color_identity or ""):
            if ch.isalpha():
                deck_identity.add(ch.upper())
        if card.oracle_id:
            exclude_oracles.add(card.oracle_id)

    for card in folder.cards:
        qty = max(0, int(card.quantity or 0))
        if qty <= 0:
            continue
        pr = _lookup_print_data(card.set_code, card.collector_number, card.name, card.oracle_id)
        price = _usd_price(pr)
        if price is None or price < threshold:
            continue
        primary = _primary_type(card.type_line)
        if not primary:
            continue
        price_ceiling = (price * max_price_multiplier).quantize(Decimal("0.01"))
        owned = _collection_candidates(
            user_id,
            primary_type=primary,
            deck_identity=deck_identity,
            exclude_oracle_ids=exclude_oracles,
            price_ceiling=price_ceiling,
        )
        alternatives = owned[:suggestions_per_card]
        if len(alternatives) < suggestions_per_card:
            needed = suggestions_per_card - len(alternatives)
            alternatives.extend(
                _cache_candidates(
                    primary_type=primary,
                    deck_identity=deck_identity,
                    exclude_oracle_ids=exclude_oracles
                    | {alt.oracle_id for alt in alternatives if alt.oracle_id},
                    price_ceiling=price_ceiling,
                    limit=needed,
                )
            )
        expensive_slots.append(
            ExpensiveSlot(
                name=card.name,
                oracle_id=card.oracle_id,
                price_usd=price,
                type_line=card.type_line,
                color_identity=_color_identity_from_print(pr),
                alternatives=alternatives[:suggestions_per_card],
            )
        )

    expensive_slots.sort(key=lambda slot: slot.price_usd, reverse=True)
    return BudgetAlternativesReport(
        folder_id=folder.id,
        threshold_usd=threshold,
        currency="usd",
        suggestions=expensive_slots,
    )
