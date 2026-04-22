"""Facet and option helpers for collection browsing."""

from __future__ import annotations

from typing import Iterable

from sqlalchemy import func, or_

from extensions import db
from models import Card, Folder
from core.domains.cards.services.scryfall_cache import cache_epoch, cache_ready, ensure_cache_loaded, set_name_for_code
from shared.cache.runtime_cache import cache_fetch as _cache_fetch, user_cache_key as _user_cache_key
from shared.mtg import _lookup_print_data

RARITY_CHOICE_ORDER: list[tuple[str, str]] = [
    ("common", "Common"),
    ("uncommon", "Uncommon"),
    ("rare", "Rare"),
    ("mythic", "Mythic"),
    ("mythic rare", "Mythic Rare"),
    ("special", "Special"),
    ("bonus", "Bonus"),
    ("masterpiece", "Masterpiece"),
    ("timeshifted", "Timeshifted"),
    ("basic", "Basic"),
]


def _ensure_cache_ready() -> bool:
    return cache_ready() or ensure_cache_loaded()


def card_browser_facets():
    user_key = _user_cache_key()

    def _build():
        sets = [value for (value,) in db.session.query(Card.set_code).distinct().order_by(Card.set_code.asc()).all() if value]
        langs = [value for (value,) in db.session.query(Card.lang).distinct().order_by(Card.lang.asc()).all() if value]
        folders = db.session.query(Folder.id, Folder.name).order_by(Folder.name.asc()).all()
        return sets, langs, folders

    return _cache_fetch(f"facets:{user_key}", 300, _build)


def collection_rarity_options() -> list[dict[str, str]]:
    user_key = _user_cache_key()

    def _build() -> list[dict[str, str]]:
        rows = (
            db.session.query(func.lower(Card.rarity))
            .filter(Card.rarity.isnot(None), Card.rarity != "")
            .distinct()
            .order_by(func.lower(Card.rarity))
            .all()
        )
        present: set[str] = set()
        for (value,) in rows:
            if not value:
                continue
            clean = value.strip().lower()
            if clean:
                present.add(clean)

        missing_rows = (
            db.session.query(
                Card.set_code,
                Card.collector_number,
                Card.name,
                Card.oracle_id,
            )
            .filter(or_(Card.rarity.is_(None), Card.rarity == ""))
            .all()
        )
        seen_missing: set[tuple[str, str, str, str]] = set()
        for set_code, collector_number, name, oracle_id in missing_rows:
            key = (
                str(set_code or "").strip().lower(),
                str(collector_number or "").strip().lower(),
                str(name or "").strip().lower(),
                str(oracle_id or "").strip().lower(),
            )
            if key in seen_missing:
                continue
            seen_missing.add(key)
            try:
                print_data = _lookup_print_data(set_code, collector_number, name, oracle_id)
            except Exception:
                print_data = None
            rarity_value = str((print_data or {}).get("rarity") or "").strip().lower()
            if rarity_value:
                present.add(rarity_value)

        options: list[dict[str, str]] = []
        seen: set[str] = set()
        for value, label in RARITY_CHOICE_ORDER:
            clean = value.strip().lower()
            if not clean or clean in seen:
                continue
            options.append({"value": clean, "label": label})
            seen.add(clean)
            present.discard(clean)

        for extra in sorted(present):
            if extra in seen:
                continue
            label = extra.replace("_", " ").replace("-", " ").title()
            options.append({"value": extra, "label": label})
            seen.add(extra)

        return options

    return _cache_fetch(f"rarity_options:{user_key}:{cache_epoch()}", 300, _build)


def set_options_with_names(codes: Iterable[str]) -> list[dict[str, str]]:
    _ensure_cache_ready()
    options: list[dict[str, str]] = []
    seen: set[str] = set()
    for code in codes or []:
        normalized = (code or "").strip().lower()
        if not normalized or normalized in seen:
            continue
        label = normalized.upper()
        set_name = None
        try:
            set_name = set_name_for_code(normalized)
        except Exception:
            set_name = None
        if set_name:
            label = f"{label} ({set_name})"
        options.append({"code": normalized, "label": label})
        seen.add(normalized)
    return options


__all__ = [
    "card_browser_facets",
    "collection_rarity_options",
    "set_options_with_names",
]
