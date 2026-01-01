"""Canonical deck tag vocabulary (DB-backed) and folder tag mapping."""

from __future__ import annotations

from collections import OrderedDict
import re
import unicodedata
from typing import Iterable

from sqlalchemy import func, select
from extensions import db
from models import DeckTag, DeckTagMap, Folder
from services.request_cache import request_cache_clear, request_cached

_SLUG_RE = re.compile(r"[^A-Za-z0-9]+")
_WHITESPACE_RE = re.compile(r"\s+")

_CATEGORY_ORDER = [
    "Core Archetypes",
    "Mechanics and Resources",
    "Tribal Themes",
    "Special Card Synergies",
    "Play Patterns and Win Conditions",
    "Advanced / Experimental Mechanics",
    "Keywords and Combat",
    "Flavor and Miscellaneous",
]

_SPECIAL_LABELS = {
    "cedh": "cEDH",
}


def _slugify(label: str) -> str:
    text = unicodedata.normalize("NFKD", label or "")
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    text = text.replace("//", " ")
    text = text.replace("/", " ")
    text = text.replace("&", " and ")
    text = text.replace("@", " at ")
    text = text.replace("+", " plus ")
    text = text.replace("'", "")
    text = _SLUG_RE.sub("-", text)
    text = re.sub(r"-{2,}", "-", text)
    return text.strip("-")


def normalize_tag_label(label: str) -> str:
    cleaned = _WHITESPACE_RE.sub(" ", (label or "").strip())
    if not cleaned:
        return ""
    return cleaned


def _format_new_label(label: str) -> str:
    cleaned = normalize_tag_label(label)
    if not cleaned:
        return ""
    if any(ch.isupper() for ch in cleaned if ch.isalpha()):
        return cleaned
    parts = cleaned.split(" ")
    formatted = []
    for part in parts:
        key = part.casefold()
        if key in _SPECIAL_LABELS:
            formatted.append(_SPECIAL_LABELS[key])
        else:
            formatted.append(part.capitalize())
    return " ".join(formatted)


def _fallback_category(source: str | None) -> str | None:
    if not source:
        return None
    if source == "edhrec":
        return "EDHREC"
    if source == "user":
        return "User Tags"
    return None


def _clear_cache() -> None:
    request_cache_clear("deck_tags")


def _load_tag_rows() -> list[dict]:
    def _query() -> list[dict]:
        rows = db.session.execute(
            select(
                DeckTag.name,
                DeckTag.slug,
                DeckTag.source,
                DeckTag.edhrec_category,
            ).order_by(func.lower(DeckTag.name))
        ).all()
        return [dict(row._mapping) for row in rows]

    return request_cached(("deck_tags", "rows"), _query)


def _tag_maps() -> tuple[list[dict], dict[str, dict], dict[str, dict]]:
    def _build() -> tuple[list[dict], dict[str, dict], dict[str, dict]]:
        rows = _load_tag_rows()
        by_name: dict[str, dict] = {}
        by_slug: dict[str, dict] = {}
        for row in rows:
            name = (row.get("name") or "").strip()
            slug = (row.get("slug") or "").strip()
            if name:
                by_name[name.casefold()] = row
            if slug:
                by_slug[slug.casefold()] = row
        return rows, by_name, by_slug

    return request_cached(("deck_tags", "maps"), _build)


def get_all_deck_tags() -> list[str]:
    return [row["name"] for row in _load_tag_rows()]


def get_deck_tag_name_set() -> set[str]:
    return {row["name"] for row in _load_tag_rows() if row.get("name")}


def get_deck_tag_groups() -> OrderedDict[str, list[str]]:
    rows = _load_tag_rows()
    groups: dict[str, list[str]] = {}
    for row in rows:
        name = (row.get("name") or "").strip()
        if not name:
            continue
        category = (row.get("edhrec_category") or "").strip()
        if not category:
            category = _fallback_category(row.get("source")) or "Other"
        groups.setdefault(category, []).append(name)

    for tags in groups.values():
        tags.sort(key=str.casefold)

    ordered: OrderedDict[str, list[str]] = OrderedDict()
    for category in _CATEGORY_ORDER:
        if category in groups:
            ordered[category] = groups.pop(category)

    for category in ("EDHREC", "User Tags", "Other"):
        if category in groups:
            ordered[category] = groups.pop(category)

    for category in sorted(groups.keys(), key=str.casefold):
        ordered[category] = groups[category]

    return ordered


def get_deck_tag_category(tag: str | None) -> str | None:
    label = normalize_tag_label(tag or "")
    if not label:
        return None
    _, by_name, by_slug = _tag_maps()
    row = by_name.get(label.casefold())
    if row is None:
        row = by_slug.get(_slugify(label))
    if row is None:
        return None
    category = (row.get("edhrec_category") or "").strip()
    return category or _fallback_category(row.get("source"))


def resolve_deck_tag_from_slug(value: str | None) -> str | None:
    cleaned = normalize_tag_label(value or "")
    if not cleaned:
        return None
    _, by_name, by_slug = _tag_maps()
    row = by_name.get(cleaned.casefold())
    if row is None:
        row = by_slug.get(_slugify(cleaned))
    if row is None:
        raw_slug = (value or "").strip().lower()
        row = by_slug.get(raw_slug)
    if row is not None:
        return row.get("name")
    return cleaned


def is_valid_deck_tag(tag: str | None) -> bool:
    cleaned = normalize_tag_label(tag or "")
    if not cleaned:
        return False
    _, by_name, by_slug = _tag_maps()
    return cleaned.casefold() in by_name or _slugify(cleaned) in by_slug


def ensure_deck_tag(
    label: str | None,
    *,
    source: str = "user",
    edhrec_category: str | None = None,
) -> DeckTag | None:
    cleaned = normalize_tag_label(label or "")
    if not cleaned:
        return None
    _, by_name, by_slug = _tag_maps()
    existing = by_name.get(cleaned.casefold())
    if existing is None:
        existing = by_slug.get(_slugify(cleaned))
    if existing is not None:
        tag = DeckTag.query.filter(func.lower(DeckTag.name) == (existing["name"].casefold())).first()
        if tag and edhrec_category and not tag.edhrec_category and tag.source != "user":
            tag.edhrec_category = edhrec_category
        if tag:
            _clear_cache()
        return tag

    formatted = _format_new_label(cleaned)
    tag = DeckTag(
        name=formatted,
        slug=_slugify(formatted),
        source=source or "user",
        edhrec_category=edhrec_category,
    )
    db.session.add(tag)
    _clear_cache()
    return tag


def set_folder_deck_tag(
    folder: Folder,
    tag_label: str,
    *,
    source: str = "user",
    locked: bool = True,
    confidence: float | None = None,
) -> DeckTag | None:
    tag = ensure_deck_tag(tag_label, source=source)
    if not tag:
        return None
    if tag.id is None:
        db.session.flush()
    DeckTagMap.query.filter_by(folder_id=folder.id).delete(synchronize_session=False)
    entry = DeckTagMap(
        folder_id=folder.id,
        deck_tag_id=tag.id,
        confidence=confidence,
        source=source,
        locked=locked,
    )
    db.session.add(entry)
    folder.deck_tag = tag.name
    return tag


def clear_folder_deck_tags(folder: Folder) -> None:
    DeckTagMap.query.filter_by(folder_id=folder.id).delete(synchronize_session=False)
    folder.deck_tag = None


def sync_folder_deck_tag_map(folder: Folder) -> None:
    if not folder.deck_tag:
        DeckTagMap.query.filter_by(folder_id=folder.id).delete(synchronize_session=False)
        return
    tag = ensure_deck_tag(folder.deck_tag, source="user")
    if not tag:
        return
    if tag.id is None:
        db.session.flush()
    existing = DeckTagMap.query.filter_by(folder_id=folder.id, deck_tag_id=tag.id).first()
    if existing:
        return
    DeckTagMap.query.filter_by(folder_id=folder.id).delete(synchronize_session=False)
    db.session.add(
        DeckTagMap(
            folder_id=folder.id,
            deck_tag_id=tag.id,
            source="user",
            locked=True,
        )
    )


__all__ = [
    "clear_folder_deck_tags",
    "ensure_deck_tag",
    "get_all_deck_tags",
    "get_deck_tag_category",
    "get_deck_tag_groups",
    "get_deck_tag_name_set",
    "is_valid_deck_tag",
    "normalize_tag_label",
    "resolve_deck_tag_from_slug",
    "set_folder_deck_tag",
    "sync_folder_deck_tag_map",
]
