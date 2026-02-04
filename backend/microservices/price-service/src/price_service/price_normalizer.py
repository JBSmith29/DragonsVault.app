from __future__ import annotations

from datetime import date
from typing import Any, Iterable, Tuple


def _rank(value: str | None, preferred: Tuple[str, ...]) -> int:
    if not value:
        return len(preferred)
    value = value.strip().lower()
    try:
        return preferred.index(value)
    except ValueError:
        return len(preferred)


def _entry_date_ordinal(entry: dict[str, Any]) -> int:
    raw = (entry.get("date") or "").strip()
    if not raw:
        return 0
    try:
        return date.fromisoformat(raw).toordinal()
    except ValueError:
        return 0


def _finish_from_entry(entry: dict[str, Any]) -> str:
    card_type = (entry.get("cardType") or "").lower()
    list_type = (entry.get("listType") or "").lower()
    provider = (entry.get("provider") or "").lower()
    blob = " ".join([card_type, list_type, provider])
    if "etched" in blob:
        return "etched"
    if "foil" in blob:
        return "foil"
    if "mtgo" in blob or "tix" in blob or "online" in blob:
        return "mtgo"
    return "normal"


def _currency_key(entry: dict[str, Any], finish: str) -> str | None:
    currency = (entry.get("currency") or "").strip().upper()
    if currency in {"USD", "US"}:
        if finish == "foil":
            return "usd_foil"
        if finish == "etched":
            return "usd_etched"
        return "usd"
    if currency == "EUR":
        if finish in {"foil", "etched"}:
            return "eur_foil"
        return "eur"
    if currency in {"TIX", "MTGO"}:
        return "tix"
    return None


def normalize_prices(
    entries: Iterable[dict[str, Any]],
    provider_preference: Tuple[str, ...],
    list_type_preference: Tuple[str, ...],
) -> tuple[dict[str, Any], str | None]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    max_date_ordinal = 0
    max_date_value: str | None = None

    for entry in entries or []:
        try:
            price_value = float(entry.get("price"))
        except (TypeError, ValueError):
            continue
        if price_value <= 0:
            continue

        finish = _finish_from_entry(entry)
        key = _currency_key(entry, finish)
        if not key:
            continue

        grouped.setdefault(key, []).append(entry | {"_price_value": price_value})

        entry_date = _entry_date_ordinal(entry)
        if entry_date > max_date_ordinal:
            max_date_ordinal = entry_date
            max_date_value = (entry.get("date") or "").strip() or None

    normalized: dict[str, Any] = {}

    for key, bucket in grouped.items():
        def sort_key(item: dict[str, Any]) -> tuple[int, int, int]:
            return (
                _rank(item.get("provider"), provider_preference),
                _rank(item.get("listType"), list_type_preference),
                -_entry_date_ordinal(item),
            )

        best = min(bucket, key=sort_key)
        normalized[key] = round(float(best.get("_price_value")), 2)

    return normalized, max_date_value
