"""Target discovery and tag parsing helpers for the local EDHREC cache."""

from __future__ import annotations

import logging
from typing import Iterable

from sqlalchemy.exc import SQLAlchemyError

from extensions import db
from models import Folder
from core.domains.cards.services import scryfall_cache as sc
from core.domains.decks.services.commander_utils import primary_commander_name, primary_commander_oracle_id
from core.domains.decks.services.deck_tags import ensure_deck_tag, normalize_tag_label, resolve_deck_tag_from_slug
from core.domains.decks.services.edhrec_client import edhrec_index, slugify_theme

_LOG = logging.getLogger(__name__)


def _dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        label = (value or "").strip()
        if not label:
            continue
        key = label.casefold()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(label)
    return ordered


def _commander_name_from_oracle(oracle_id: str) -> str | None:
    if not oracle_id:
        return None
    try:
        prints = sc.prints_for_oracle(oracle_id) or []
    except Exception:
        return None
    if prints:
        return prints[0].get("name")
    return None


def collect_edhrec_targets() -> dict:
    folders = Folder.query.order_by(Folder.name.asc()).all()
    deck_folders = [folder for folder in folders if not folder.is_collection]
    commander_targets: list[dict] = []
    tag_names: list[str] = []
    with_commander = 0
    with_tag = 0

    cache_ready = False
    for folder in deck_folders:
        tag = (folder.deck_tag or "").strip()
        if tag:
            with_tag += 1
            tag_names.append(tag)

        commander_oracle_id = primary_commander_oracle_id(folder.commander_oracle_id)
        commander_name = primary_commander_name(folder.commander_name)
        if not commander_name and commander_oracle_id:
            try:
                cache_ready = cache_ready or sc.ensure_cache_loaded()
                if cache_ready:
                    commander_name = _commander_name_from_oracle(commander_oracle_id)
            except Exception:
                commander_name = None

        if commander_oracle_id and commander_name:
            with_commander += 1
            commander_targets.append(
                {"oracle_id": commander_oracle_id, "name": commander_name}
            )

    deduped_targets: list[dict] = []
    seen_oracles: set[str] = set()
    for target in commander_targets:
        oracle_id = (target.get("oracle_id") or "").strip()
        if not oracle_id:
            continue
        key = oracle_id.casefold()
        if key in seen_oracles:
            continue
        seen_oracles.add(key)
        deduped_targets.append(target)

    return {
        "deck_total": len(deck_folders),
        "with_commander": with_commander,
        "with_tag": with_tag,
        "commanders": deduped_targets,
        "tags": _dedupe(tag_names),
    }


def collect_edhrec_index_targets(*, include_themes: bool = True) -> dict:
    index = edhrec_index(
        include_commanders=True,
        include_themes=include_themes,
    )
    commanders = index.get("commanders") or []
    tag_names: list[str] = []
    if include_themes:
        for entry in index.get("themes") or []:
            if isinstance(entry, dict):
                name = entry.get("name")
                if isinstance(name, str) and name.strip():
                    tag_names.append(name.strip())

    deduped_tags = _dedupe(tag_names)
    return {
        "source": "edhrec",
        "commanders": commanders,
        "tags": deduped_tags,
        "commanders_total": len(commanders),
        "tags_total": len(deduped_tags),
    }


def extract_commander_tag_entries(payload: dict) -> list[dict]:
    raw_options = payload.get("theme_options") or []
    if not isinstance(raw_options, list):
        return []
    entries: dict[str, dict] = {}
    for entry in raw_options:
        if not isinstance(entry, dict):
            continue
        slug = entry.get("slug") or ""
        label = entry.get("label") or ""
        candidate = resolve_deck_tag_from_slug(str(slug))
        if not candidate:
            candidate = resolve_deck_tag_from_slug(str(label))
        if candidate:
            tag = candidate
            slug_value = str(slug or "").strip().lower() or slugify_theme(tag)
            if tag not in entries:
                entries[tag] = {"tag": tag, "slug": slug_value}
    return list(entries.values())


def extract_commander_tags(payload: dict) -> list[str]:
    return [entry["tag"] for entry in extract_commander_tag_entries(payload)]


def upsert_index_tags(tag_names: Iterable[str]) -> int:
    inserted = 0
    seen: set[str] = set()
    for tag in tag_names or []:
        cleaned = normalize_tag_label(tag)
        if not cleaned:
            continue
        key = cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        tag_row = ensure_deck_tag(cleaned, source="edhrec")
        if tag_row and tag_row.id is None:
            inserted += 1
    if inserted:
        try:
            db.session.commit()
        except SQLAlchemyError as exc:
            db.session.rollback()
            _LOG.warning("Failed to store EDHREC index tags: %s", exc)
            return 0
    return inserted


__all__ = [
    "collect_edhrec_index_targets",
    "collect_edhrec_targets",
    "extract_commander_tag_entries",
    "extract_commander_tags",
    "upsert_index_tags",
]
