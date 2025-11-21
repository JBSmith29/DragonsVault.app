"""Shared pricing helpers for DragonsVault services and routes.

These utilities convert Scryfall price payloads into structures that can be
reused across the web views (Flask routes) and background analytics (services).
By keeping the logic here we avoid circular imports between the service layer
and the Flask blueprint modules.
"""
from __future__ import annotations

from typing import Any, Dict

from services import scryfall_cache as sc
from services.scryfall_cache import prints_for_oracle

__all__ = [
    "PRICE_KEYS",
    "price_has_value",
    "oracle_price_lookup",
    "prices_for_print",
    "format_price_text",
]

PRICE_KEYS: tuple[str, ...] = ("usd", "usd_foil", "usd_etched", "eur", "eur_foil", "tix")


def price_has_value(prices: Dict[str, Any] | None) -> bool:
    """Return True when the provided price mapping contains a positive value."""
    if not prices:
        return False
    for key in PRICE_KEYS:
        val = prices.get(key)
        if val in (None, "", 0, "0", "0.0", "0.00"):
            continue
        try:
            if float(val) > 0:
                return True
        except (TypeError, ValueError):
            continue
    return False


def oracle_price_lookup(oracle_id: str | None) -> Dict[str, Any]:
    """Backfill price information by scanning all prints of a given oracle id."""
    if not oracle_id:
        return {}
    try:
        alts = prints_for_oracle(str(oracle_id)) or []
    except Exception:
        try:
            alts = sc.prints_for_oracle(str(oracle_id)) or []
        except Exception:
            alts = []
    for alt in alts:
        prices = alt.get("prices") or {}
        if price_has_value(prices):
            return prices
    return {}


def prices_for_print(pr: Dict[str, Any] | None) -> Dict[str, Any]:
    """Return the most useful price payload for a print, falling back to oracle data."""
    if not pr:
        return {}
    prices = pr.get("prices") or {}
    if price_has_value(prices):
        return prices
    return oracle_price_lookup(pr.get("oracle_id"))


def format_price_text(prices: Dict[str, Any] | None) -> str | None:
    """Convert a Scryfall price dict into a compact human string."""
    if not prices:
        return None

    def _fmt(value, prefix):
        if value in (None, "", 0, "0", "0.0", "0.00"):
            return None
        try:
            num = float(value)
        except (TypeError, ValueError):
            return None
        if num <= 0:
            return None
        return f"{prefix}{num:,.2f}".replace(",", "")

    sections: list[str] = []
    usd = _fmt(prices.get("usd"), "$")
    usd_foil = _fmt(prices.get("usd_foil"), "$")
    usd_etched = _fmt(prices.get("usd_etched"), "$")
    if usd:
        sections.append(f"Normal {usd}")
    if usd_foil:
        sections.append(f"Foil {usd_foil}")
    if usd_etched:
        sections.append(f"Etched {usd_etched}")

    if not sections:
        eur = _fmt(prices.get("eur"), "EUR ")
        eur_foil = _fmt(prices.get("eur_foil"), "EUR ")
        if eur:
            sections.append(f"Normal {eur}")
        if eur_foil:
            sections.append(f"Foil {eur_foil}")

    if not sections:
        tix = _fmt(prices.get("tix"), "TIX ")
        if tix:
            sections.append(f"MTGO {tix}")

    if not sections:
        return None

    if len(sections) == 1:
        return sections[0]
    if len(sections) == 2:
        return " / ".join(sections)
    return f"{sections[0]} / {sections[1]} (+{len(sections) - 2} more)"

