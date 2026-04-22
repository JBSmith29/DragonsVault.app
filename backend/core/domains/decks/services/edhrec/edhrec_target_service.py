"""EDHREC commander target discovery helpers."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import func

from extensions import db
from models import EdhrecMetadata, Folder
from core.domains.cards.services import scryfall_cache as sc
from core.domains.decks.services.commander_utils import primary_commander_name, primary_commander_oracle_id
from core.domains.decks.services.deck_tags import normalize_tag_label, resolve_deck_tag_from_slug
from core.domains.decks.services.edhrec_client import edhrec_index, edhrec_service_enabled, slugify_commander

_LOG = logging.getLogger(__name__)

_DFC_LAYOUTS = {"modal_dfc", "transform", "flip", "meld"}


@dataclass(frozen=True)
class CommanderTarget:
    oracle_id: str
    name: str
    slug_name: str
    slug_override: str | None = None


def _is_commander_print(card: dict) -> bool:
    if not isinstance(card, dict):
        return False
    layout = (card.get("layout") or "").lower()
    if layout == "meld":
        return False
    legality = ((card.get("legalities") or {}).get("commander") or "").lower()
    if legality != "legal":
        return False
    type_line = (card.get("type_line") or "")
    oracle_text = (card.get("oracle_text") or "")
    if layout in _DFC_LAYOUTS:
        faces = card.get("card_faces") or []
        if isinstance(faces, list) and faces:
            front = faces[0] or {}
            type_line = front.get("type_line") or type_line
            oracle_text = front.get("oracle_text") or oracle_text
    type_line_lower = type_line.lower()
    oracle_lower = (oracle_text or "").lower()
    if "can be your commander" in oracle_lower:
        return True
    if "planeswalker" in type_line_lower:
        return False
    if "legendary" in type_line_lower and "creature" in type_line_lower:
        return True
    return False


def _front_face_name(card: dict) -> str | None:
    faces = card.get("card_faces") or []
    if isinstance(faces, list) and faces:
        face = faces[0] or {}
        name = (face.get("name") or "").strip()
        if name:
            return name
    return None


def slug_name_for_print(card: dict) -> str:
    layout = (card.get("layout") or "").lower()
    if layout in _DFC_LAYOUTS:
        front = _front_face_name(card)
        if front:
            return front
    return (card.get("name") or "").strip()


def normalize_deck_tag(value: str | None) -> str | None:
    if not value:
        return None
    candidate = resolve_deck_tag_from_slug(str(value))
    if candidate:
        return candidate
    cleaned = normalize_tag_label(str(value))
    return cleaned or None


def _collect_folder_tags(folder: Folder) -> set[str]:
    tags: set[str] = set()
    normalized = normalize_deck_tag(folder.deck_tag)
    if normalized:
        tags.add(normalized)
    return tags


def commander_target_from_oracle(
    oracle_id: str,
    commander_name: str | None,
    *,
    index_slugs: dict[str, dict[str, str]] | None,
    cache_ready: bool,
) -> CommanderTarget | None:
    name = (commander_name or "").strip()
    slug_name = name
    if cache_ready:
        try:
            prints = sc.prints_for_oracle(oracle_id) or []
        except Exception:
            prints = []
        if prints:
            sample = prints[0]
            if not name:
                name = (sample.get("name") or "").strip()
            slug_name = slug_name_for_print(sample) or name
    if not name:
        return None
    slug_override = None
    if index_slugs:
        entry = index_slugs.get(oracle_id)
        if entry:
            slug_override = entry.get("slug")
    return CommanderTarget(
        oracle_id=oracle_id,
        name=name,
        slug_name=slug_name or name,
        slug_override=slug_override,
    )


def load_edhrec_index_slugs() -> dict[str, dict[str, str]]:
    if not edhrec_service_enabled():
        return {}
    try:
        index = edhrec_index(include_commanders=True, include_themes=False)
    except Exception as exc:
        _LOG.warning("EDHREC index lookup failed: %s", exc)
        return {}
    entries = index.get("commanders") or []
    mapping: dict[str, dict[str, str]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = (entry.get("name") or "").strip()
        slug = (entry.get("slug") or "").strip().lower()
        if not name or not slug:
            continue
        oracle_id = sc.unique_oracle_by_name(name)
        if not oracle_id:
            continue
        prints = sc.prints_for_oracle(oracle_id) or []
        if not prints:
            continue
        sample = prints[0]
        sample_name = (slug_name_for_print(sample) or sample.get("name") or "").strip()
        if not sample_name:
            continue
        sample_slug = slugify_commander(sample_name)
        name_slug = slugify_commander(name)
        if name_slug != sample_slug:
            front_name = ""
            if "//" in name:
                front_name = (name.split("//", 1)[0] or "").strip()
            if not front_name or slugify_commander(front_name) != sample_slug:
                continue
        mapping[oracle_id] = {"name": name, "slug": slug}
    return mapping


def load_commander_targets(*, use_index_slugs: bool = True, index_only: bool = False) -> list[CommanderTarget]:
    if not sc.ensure_cache_loaded():
        return []
    index_slugs = load_edhrec_index_slugs() if use_index_slugs else {}
    targets: dict[str, CommanderTarget] = {}
    oracle_ids = list(getattr(sc, "_by_oracle", {}).keys())
    for oracle_id in oracle_ids:
        prints = sc.prints_for_oracle(oracle_id) or []
        if not prints:
            continue
        sample = prints[0]
        if not _is_commander_print(sample):
            continue
        name = (sample.get("name") or "").strip()
        slug_name = slug_name_for_print(sample) or name
        if not name:
            continue
        slug_override = None
        if index_slugs:
            index_entry = index_slugs.get(oracle_id)
            if index_entry:
                slug_override = index_entry.get("slug")
        targets[oracle_id] = CommanderTarget(
            oracle_id=oracle_id,
            name=name,
            slug_name=slug_name,
            slug_override=slug_override,
        )

    if index_only and index_slugs:
        targets = {oid: target for oid, target in targets.items() if oid in index_slugs}
    return sorted(targets.values(), key=lambda item: item.name.lower())


def load_active_targets(*, use_index_slugs: bool = True) -> tuple[list[CommanderTarget], dict[str, set[str]]]:
    cache_ready = False
    try:
        cache_ready = sc.ensure_cache_loaded()
    except Exception as exc:
        _LOG.warning("Scryfall cache unavailable for EDHREC deck targets: %s", exc)

    index_slugs = load_edhrec_index_slugs() if use_index_slugs else {}
    folders = (
        Folder.query.filter(Folder.category != Folder.CATEGORY_COLLECTION)
        .order_by(func.lower(Folder.name))
        .all()
    )

    targets: dict[str, CommanderTarget] = {}
    tag_map: dict[str, set[str]] = {}

    for folder in folders:
        commander_oracle_id = primary_commander_oracle_id(folder.commander_oracle_id) or ""
        commander_name = primary_commander_name(folder.commander_name)
        if not commander_oracle_id and commander_name and cache_ready:
            commander_oracle_id = sc.unique_oracle_by_name(commander_name) or ""
        if not commander_oracle_id:
            continue

        tag_set = _collect_folder_tags(folder)
        if tag_set:
            tag_map.setdefault(commander_oracle_id, set()).update(tag_set)

        if commander_oracle_id not in targets:
            target = commander_target_from_oracle(
                commander_oracle_id,
                commander_name,
                index_slugs=index_slugs,
                cache_ready=cache_ready,
            )
            if target:
                targets[commander_oracle_id] = target

    return sorted(targets.values(), key=lambda item: item.name.lower()), tag_map


def load_top_index_targets(limit: int) -> list[CommanderTarget]:
    if limit <= 0 or not edhrec_service_enabled():
        return []
    try:
        cache_ready = sc.ensure_cache_loaded()
    except Exception as exc:
        _LOG.warning("Scryfall cache unavailable for EDHREC top targets: %s", exc)
        return []
    if not cache_ready:
        return []
    try:
        index = edhrec_index(include_commanders=True, include_themes=False, limit=limit)
    except Exception as exc:
        _LOG.warning("EDHREC index lookup failed: %s", exc)
        return []
    entries = index.get("commanders") or []
    targets: list[CommanderTarget] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = (entry.get("name") or "").strip()
        slug_override = (entry.get("slug") or "").strip().lower()
        if not name:
            continue
        oracle_id = sc.unique_oracle_by_name(name) or ""
        if not oracle_id or oracle_id in seen:
            continue
        seen.add(oracle_id)
        slug_map = {oracle_id: {"slug": slug_override}} if slug_override else None
        target = commander_target_from_oracle(
            oracle_id,
            name,
            index_slugs=slug_map,
            cache_ready=True,
        )
        if target:
            targets.append(target)
        if len(targets) >= limit:
            break
    return targets


def load_missing_slugs() -> dict[str, dict[str, str]]:
    row = db.session.get(EdhrecMetadata, "missing_slugs")
    if not row or not row.value:
        return {}
    try:
        data = json.loads(row.value)
    except Exception:
        return {}
    if isinstance(data, dict):
        return data
    return {}


def missing_oracle_ids(missing: dict[str, dict[str, str]]) -> set[str]:
    oracle_ids: set[str] = set()
    for info in missing.values():
        if not isinstance(info, dict):
            continue
        oracle_id = (info.get("oracle_id") or "").strip()
        if oracle_id:
            oracle_ids.add(oracle_id)
    return oracle_ids


def clear_missing_for_oracle(missing: dict[str, dict[str, str]], oracle_id: str) -> None:
    if not oracle_id:
        return
    to_remove = [slug for slug, info in missing.items() if isinstance(info, dict) and info.get("oracle_id") == oracle_id]
    for slug in to_remove:
        missing.pop(slug, None)


def slug_candidates_for_target(target: CommanderTarget) -> list[str]:
    slugs: list[str] = []

    def _add(raw: str | None, *, is_slug: bool = False) -> None:
        if not raw:
            return
        slug = raw if is_slug else slugify_commander(raw)
        if slug and slug not in slugs:
            slugs.append(slug)

    _add((target.slug_override or "").strip(), is_slug=True)
    _add(target.slug_name)
    _add(target.name)
    if target.name and "//" in target.name:
        front = (target.name.split("//", 1)[0] or "").strip()
        _add(front)
    return slugs


def prune_missing_slugs(missing: dict[str, dict[str, str]], missing_ttl_days: int) -> dict[str, dict[str, str]]:
    if missing_ttl_days <= 0:
        return missing
    cutoff = datetime.now(timezone.utc).timestamp() - (missing_ttl_days * 86400)
    pruned: dict[str, dict[str, str]] = {}
    for slug, info in missing.items():
        last_seen = info.get("last_seen") if isinstance(info, dict) else None
        try:
            seen_ts = datetime.fromisoformat(last_seen).timestamp() if last_seen else 0
        except Exception:
            seen_ts = 0
        if seen_ts and seen_ts >= cutoff:
            pruned[slug] = info
    return pruned


__all__ = [
    "CommanderTarget",
    "clear_missing_for_oracle",
    "commander_target_from_oracle",
    "load_active_targets",
    "load_commander_targets",
    "load_edhrec_index_slugs",
    "load_missing_slugs",
    "load_top_index_targets",
    "missing_oracle_ids",
    "normalize_deck_tag",
    "prune_missing_slugs",
    "slug_candidates_for_target",
    "slug_name_for_print",
]
