"""Monthly EDHREC ingestion for commander synergy and tag data."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

import requests
from sqlalchemy import func, inspect, text
from sqlalchemy.exc import SQLAlchemyError

from extensions import db
from models import (
    EdhrecCommanderCard,
    EdhrecCommanderCategoryCard,
    EdhrecCommanderTag,
    EdhrecCommanderTagCard,
    EdhrecCommanderTagCategoryCard,
    EdhrecCommanderTypeDistribution,
    EdhrecMetadata,
    EdhrecTagCommander,
    Folder,
)
from services import scryfall_cache as sc
from services.commander_utils import primary_commander_name, primary_commander_oracle_id
from services.deck_tags import ensure_deck_tag, normalize_tag_label, resolve_deck_tag_from_slug
from services.edhrec_client import edhrec_index, edhrec_service_enabled, slugify_commander, slugify_theme

_LOG = logging.getLogger(__name__)

_NEXT_DATA_RE = re.compile(r'__NEXT_DATA__" type="application/json">(.*?)</script>', re.DOTALL)

_REQUEST_INTERVAL_SECONDS = max(1.0, float(os.getenv("EDHREC_INGEST_INTERVAL", "1.0")))
def _parse_max_cards_env(name: str, default: int | None) -> int | None:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = str(raw).strip().lower()
    if value in {"0", "none", "null", "all", "unlimited"}:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else None


_MAX_SYNERGY_CARDS = _parse_max_cards_env("EDHREC_INGEST_MAX_CARDS", None)
_DEFAULT_SOURCE_VERSION = os.getenv("EDHREC_SOURCE_VERSION")
_MISSING_TTL_DAYS = int(os.getenv("EDHREC_MISSING_TTL_DAYS", "30"))
_TOP_COMMANDER_LIMIT = int(os.getenv("EDHREC_TOP_COMMANDER_LIMIT", "500"))
_TOP_COMMANDER_TAG_LIMIT = int(os.getenv("EDHREC_TOP_COMMANDER_TAG_LIMIT", "5"))


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


_USE_INDEX_SLUGS = _bool_env("EDHREC_USE_INDEX_SLUGS", True)
_INDEX_ONLY = _bool_env("EDHREC_INDEX_ONLY", False)
_INCLUDE_TOP_COMMANDERS = _bool_env("EDHREC_INCLUDE_TOP_COMMANDERS", True)


_DFC_LAYOUTS = {"modal_dfc", "transform", "flip", "meld"}


@dataclass(frozen=True)
class CommanderTarget:
    oracle_id: str
    name: str
    slug_name: str
    slug_override: str | None = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_schema() -> None:
    db.metadata.create_all(
        db.engine,
        tables=[
            EdhrecCommanderCard.__table__,
            EdhrecCommanderCategoryCard.__table__,
            EdhrecCommanderTag.__table__,
            EdhrecCommanderTagCard.__table__,
            EdhrecCommanderTagCategoryCard.__table__,
            EdhrecTagCommander.__table__,
            EdhrecCommanderTypeDistribution.__table__,
            EdhrecMetadata.__table__,
        ],
    )
    inspector = inspect(db.engine)
    try:
        columns = {col["name"] for col in inspector.get_columns("edhrec_commander_cards")}
        if "synergy_rank" not in columns:
            db.session.execute(text("ALTER TABLE edhrec_commander_cards ADD COLUMN synergy_rank INTEGER"))
            db.session.commit()
    except Exception as exc:
        db.session.rollback()
        _LOG.warning("EDHREC schema update skipped: %s", exc)


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


def _slug_name_for_print(card: dict) -> str:
    layout = (card.get("layout") or "").lower()
    if layout in _DFC_LAYOUTS:
        front = _front_face_name(card)
        if front:
            return front
    return (card.get("name") or "").strip()


def _normalize_deck_tag(value: str | None) -> str | None:
    if not value:
        return None
    candidate = resolve_deck_tag_from_slug(str(value))
    if candidate:
        return candidate
    cleaned = normalize_tag_label(str(value))
    return cleaned or None


def _normalize_requested_tags(tags: Iterable[str] | None) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for tag in tags or []:
        candidate = _normalize_deck_tag(tag)
        if not candidate:
            continue
        key = candidate.casefold()
        if key in seen:
            continue
        seen.add(key)
        tag_row = ensure_deck_tag(candidate, source="user")
        normalized.append(tag_row.name if tag_row else candidate)
    return normalized


def _commander_target_from_oracle(
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
            slug_name = _slug_name_for_print(sample) or name
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


def _load_commander_targets() -> list[CommanderTarget]:
    if not sc.ensure_cache_loaded():
        return []
    index_slugs = _load_edhrec_index_slugs() if _USE_INDEX_SLUGS else {}
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
        slug_name = _slug_name_for_print(sample) or name
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

    if _INDEX_ONLY and index_slugs:
        targets = {
            oid: target for oid, target in targets.items() if oid in index_slugs
        }
    return sorted(targets.values(), key=lambda item: item.name.lower())


def _collect_folder_tags(folder: Folder) -> set[str]:
    tags: set[str] = set()
    normalized = _normalize_deck_tag(folder.deck_tag)
    if normalized:
        tags.add(normalized)
    return tags


def _load_active_targets() -> tuple[list[CommanderTarget], dict[str, set[str]]]:
    cache_ready = False
    try:
        cache_ready = sc.ensure_cache_loaded()
    except Exception as exc:
        _LOG.warning("Scryfall cache unavailable for EDHREC deck targets: %s", exc)

    index_slugs = _load_edhrec_index_slugs() if _USE_INDEX_SLUGS else {}
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
            target = _commander_target_from_oracle(
                commander_oracle_id,
                commander_name,
                index_slugs=index_slugs,
                cache_ready=cache_ready,
            )
            if target:
                targets[commander_oracle_id] = target

    return sorted(targets.values(), key=lambda item: item.name.lower()), tag_map


def _load_edhrec_index_slugs() -> dict[str, dict[str, str]]:
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
        sample_name = (_slug_name_for_print(sample) or sample.get("name") or "").strip()
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


def _load_top_index_targets(limit: int) -> list[CommanderTarget]:
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
        target = _commander_target_from_oracle(
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


def _load_missing_slugs() -> dict[str, dict[str, str]]:
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


def _missing_oracle_ids(missing: dict[str, dict[str, str]]) -> set[str]:
    oracle_ids: set[str] = set()
    for info in missing.values():
        if not isinstance(info, dict):
            continue
        oracle_id = (info.get("oracle_id") or "").strip()
        if oracle_id:
            oracle_ids.add(oracle_id)
    return oracle_ids


def _clear_missing_for_oracle(missing: dict[str, dict[str, str]], oracle_id: str) -> None:
    if not oracle_id:
        return
    to_remove = [slug for slug, info in missing.items() if isinstance(info, dict) and info.get("oracle_id") == oracle_id]
    for slug in to_remove:
        missing.pop(slug, None)


def _slug_candidates_for_target(target: CommanderTarget) -> list[str]:
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


def _prune_missing_slugs(missing: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
    if _MISSING_TTL_DAYS <= 0:
        return missing
    cutoff = datetime.now(timezone.utc).timestamp() - (_MISSING_TTL_DAYS * 86400)
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


def _rate_limit(last_request_at: float) -> float:
    now = time.monotonic()
    wait_for = _REQUEST_INTERVAL_SECONDS - (now - last_request_at)
    if wait_for > 0:
        time.sleep(wait_for)
    return time.monotonic()


def _fetch_commander_json(session: requests.Session, url: str) -> tuple[dict | None, dict | None, str | None]:
    try:
        response = session.get(url, timeout=30)
    except Exception as exc:
        return None, None, f"Request failed: {exc}"
    if response.status_code == 404:
        return None, None, "Commander page not found."
    if response.status_code == 429:
        return None, None, "Rate limited by EDHREC."
    if response.status_code >= 400:
        return None, None, f"EDHREC HTTP {response.status_code}."
    html = response.text
    match = _NEXT_DATA_RE.search(html)
    if not match:
        return None, None, "Missing embedded JSON payload."
    try:
        raw = json.loads(match.group(1))
    except Exception as exc:
        return None, None, f"Invalid JSON payload: {exc}"
    data = raw.get("props", {}).get("pageProps", {}).get("data", {})
    if isinstance(data, dict):
        container = data.get("container")
        if isinstance(container, dict):
            payload = container.get("json_dict") if isinstance(container.get("json_dict"), dict) else container
        else:
            payload = data
    else:
        payload = None
    if not isinstance(payload, dict):
        return None, raw, "Embedded payload missing."  # raw still useful for tags
    return payload, raw, None


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_header(header: Any) -> str | None:
    if isinstance(header, str):
        cleaned = header.strip()
        return cleaned or None
    if isinstance(header, dict):
        for key in ("title", "label", "text"):
            value = header.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _extract_cardviews(payload: dict) -> list[dict]:
    cardlists = payload.get("cardlists") or []
    if not isinstance(cardlists, list):
        return []
    candidates = [entry for entry in cardlists if isinstance(entry, dict)]
    synergy_lists = [
        entry for entry in candidates
        if isinstance(entry.get("header"), str) and "synergy" in entry.get("header").lower()
    ]
    selected = synergy_lists or candidates
    views: list[dict] = []
    for entry in selected:
        for raw in entry.get("cardviews") or []:
            if not isinstance(raw, dict):
                continue
            name = raw.get("name")
            if not name:
                continue
            views.append(
                {
                    "name": name,
                    "rank": raw.get("rank"),
                    "synergy": _safe_float(raw.get("synergy")),
                }
            )
    return views


def _extract_cardlists(payload: dict) -> list[dict]:
    cardlists = payload.get("cardlists") or []
    if not isinstance(cardlists, list):
        return []
    lists: list[dict] = []
    for idx, entry in enumerate(cardlists, start=1):
        if not isinstance(entry, dict):
            continue
        header = _normalize_header(entry.get("header"))
        if not header:
            continue
        raw_views = entry.get("cardviews") or []
        if not isinstance(raw_views, list):
            continue
        views: list[dict] = []
        for raw in raw_views:
            if not isinstance(raw, dict):
                continue
            name = raw.get("name")
            if not name:
                continue
            views.append(
                {
                    "name": name,
                    "rank": raw.get("rank"),
                    "synergy": _safe_float(raw.get("synergy")),
                }
            )
        if views:
            lists.append(
                {
                    "category": header,
                    "category_rank": idx,
                    "views": views,
                }
            )
    return lists


def _normalize_tag_candidates(raw: dict) -> list[str]:
    candidates: list[list[dict]] = []

    def walk(obj: Any) -> None:
        if isinstance(obj, dict):
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            if obj and all(isinstance(item, dict) for item in obj):
                if any("cardviews" in item or "header" in item for item in obj):
                    return
                if all("slug" in item for item in obj):
                    candidates.append(obj)
            for item in obj:
                walk(item)

    walk(raw)

    if not candidates:
        return []

    candidates.sort(key=len, reverse=True)
    raw_list = candidates[0]
    tags: list[str] = []
    for entry in raw_list:
        slug = entry.get("slug")
        label = entry.get("label") or entry.get("name")
        if not isinstance(slug, str) and not isinstance(label, str):
            continue
        candidate = None
        if isinstance(slug, str) and slug.strip():
            candidate = resolve_deck_tag_from_slug(slug)
        if not candidate and isinstance(label, str) and label.strip():
            candidate = resolve_deck_tag_from_slug(label)
        if not candidate:
            candidate = normalize_tag_label(label or slug or "")
        if candidate:
            tags.append(candidate)
    deduped: list[str] = []
    seen = set()
    for tag in tags:
        key = tag.casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(tag)
    return deduped


_TYPE_LABEL_MAP = {
    "creature": "Creature",
    "creatures": "Creature",
    "instant": "Instant",
    "instants": "Instant",
    "sorcery": "Sorcery",
    "sorceries": "Sorcery",
    "artifact": "Artifact",
    "artifacts": "Artifact",
    "enchantment": "Enchantment",
    "enchantments": "Enchantment",
    "planeswalker": "Planeswalker",
    "planeswalkers": "Planeswalker",
    "land": "Land",
    "lands": "Land",
    "battle": "Battle",
    "battles": "Battle",
    "other": "Other",
}


def _normalize_type_label(label: str | None) -> str | None:
    if not label:
        return None
    cleaned = str(label).strip()
    if not cleaned:
        return None
    key = cleaned.casefold()
    if key in _TYPE_LABEL_MAP:
        return _TYPE_LABEL_MAP[key]
    if "creature" in key:
        return "Creature"
    if "instant" in key:
        return "Instant"
    if "sorcery" in key:
        return "Sorcery"
    if "artifact" in key:
        return "Artifact"
    if "enchantment" in key:
        return "Enchantment"
    if "planeswalker" in key:
        return "Planeswalker"
    if "land" in key:
        return "Land"
    if "battle" in key:
        return "Battle"
    return "Other"


def _extract_type_distribution(payload: dict | None) -> list[dict]:
    if not isinstance(payload, dict):
        return []
    panels = None
    if isinstance(payload.get("panels"), dict):
        panels = payload.get("panels")
    elif isinstance(payload.get("container"), dict) and isinstance(payload["container"].get("panels"), dict):
        panels = payload["container"].get("panels")
    else:
        container = payload.get("props", {}).get("pageProps", {}).get("data", {}).get("container")
        if isinstance(container, dict):
            panels = container.get("panels")
    if not isinstance(panels, dict):
        return []
    piechart = panels.get("piechart")
    if not isinstance(piechart, dict):
        return []
    content = piechart.get("content")
    if not isinstance(content, list):
        return []
    counts: dict[str, int] = {}
    for item in content:
        if not isinstance(item, dict):
            continue
        label = item.get("label")
        value = item.get("value")
        if label is None or value is None:
            continue
        try:
            numeric_value = int(round(float(value)))
        except (TypeError, ValueError):
            continue
        card_type = _normalize_type_label(label)
        if not card_type:
            continue
        counts[card_type] = counts.get(card_type, 0) + numeric_value
    if not counts:
        return []
    return [{"card_type": key, "count": int(value)} for key, value in counts.items() if value]


def _upsert_edhrec_tags(tags: Iterable[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for tag in tags or []:
        cleaned = normalize_tag_label(tag)
        if not cleaned:
            continue
        key = cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        tag_row = ensure_deck_tag(cleaned, source="edhrec")
        normalized.append(tag_row.name if tag_row else cleaned)
    return normalized


def _upsert_index_theme_tags() -> int:
    if not edhrec_service_enabled():
        return 0
    try:
        index = edhrec_index(include_commanders=False, include_themes=True)
    except Exception as exc:
        _LOG.warning("EDHREC index theme lookup failed: %s", exc)
        return 0
    themes = index.get("themes") or []
    seen: set[str] = set()
    inserted = 0
    for entry in themes:
        if isinstance(entry, dict):
            name = entry.get("name") or entry.get("label")
        else:
            name = entry
        cleaned = normalize_tag_label(name or "")
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
            _LOG.warning("EDHREC index theme upsert failed: %s", exc)
            return 0
    return inserted


def _merge_tags(primary: Iterable[str], secondary: Iterable[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for tag in list(primary) + list(secondary):
        label = (tag or "").strip()
        if not label:
            continue
        key = label.casefold()
        if key in seen:
            continue
        seen.add(key)
        merged.append(label)
    return merged


def _map_synergy_cards(views: Iterable[dict]) -> list[dict]:
    card_map: dict[str, dict] = {}
    for view in views:
        name = view.get("name")
        if not isinstance(name, str):
            continue
        oracle_id = sc.unique_oracle_by_name(name)
        if not oracle_id:
            continue
        score = view.get("synergy")
        rank = view.get("rank")
        existing = card_map.get(oracle_id)
        if existing is None or (score or 0) > (existing.get("synergy_score") or 0):
            card_map[oracle_id] = {
                "card_oracle_id": oracle_id,
                "synergy_score": score,
                "rank_hint": rank,
            }

    items = list(card_map.values())
    if any(item.get("synergy_score") is not None for item in items):
        items.sort(key=lambda item: (-(item.get("synergy_score") or 0.0), item.get("rank_hint") or 0))
    else:
        items.sort(key=lambda item: (item.get("rank_hint") or 0, item.get("card_oracle_id") or ""))

    ranked: list[dict] = []
    limited = items if _MAX_SYNERGY_CARDS is None else items[:_MAX_SYNERGY_CARDS]
    for idx, item in enumerate(limited, start=1):
        ranked.append(
            {
                "card_oracle_id": item["card_oracle_id"],
                "synergy_rank": idx,
                "synergy_score": item.get("synergy_score"),
            }
        )
    return ranked


def _map_category_cards(cardlists: Iterable[dict]) -> list[dict]:
    rows: list[dict] = []
    for entry in cardlists:
        category = entry.get("category")
        if not category:
            continue
        category_rank = int(entry.get("category_rank") or 0) or None
        views = entry.get("views") or []
        card_map: dict[str, dict] = {}
        for view in views:
            name = view.get("name")
            if not isinstance(name, str):
                continue
            oracle_id = sc.unique_oracle_by_name(name)
            if not oracle_id:
                continue
            score = view.get("synergy")
            rank = view.get("rank")
            existing = card_map.get(oracle_id)
            if existing is None or (score or 0) > (existing.get("synergy_score") or 0):
                card_map[oracle_id] = {
                    "card_oracle_id": oracle_id,
                    "synergy_score": score,
                    "rank_hint": rank,
                }
            elif score == existing.get("synergy_score") and rank is not None:
                if existing.get("rank_hint") is None or rank < existing["rank_hint"]:
                    existing["rank_hint"] = rank

        items = list(card_map.values())
        if any(item.get("synergy_score") is not None for item in items):
            items.sort(
                key=lambda item: (-(item.get("synergy_score") or 0.0), item.get("rank_hint") or 0)
            )
        else:
            items.sort(key=lambda item: (item.get("rank_hint") or 0, item.get("card_oracle_id") or ""))

        limited = items if _MAX_SYNERGY_CARDS is None else items[:_MAX_SYNERGY_CARDS]
        for idx, item in enumerate(limited, start=1):
            rows.append(
                {
                    "category": category,
                    "category_rank": category_rank,
                    "card_oracle_id": item["card_oracle_id"],
                    "synergy_rank": idx,
                    "synergy_score": item.get("synergy_score"),
                }
            )
    return rows


def _set_metadata(key: str, value: str) -> None:
    if not key:
        return
    db.session.merge(EdhrecMetadata(key=key, value=value))


def _source_version_label() -> str:
    if _DEFAULT_SOURCE_VERSION:
        return _DEFAULT_SOURCE_VERSION
    now = datetime.now(timezone.utc)
    return f"edhrec-{now.year}-{now.month:02d}"


def run_monthly_edhrec_ingestion(
    limit: int | None = None,
    *,
    full_refresh: bool = True,
    scope: str = "all",
) -> dict:
    """
    Run the EDHREC commander ingestion job.

    full_refresh=True refreshes all commanders.
    full_refresh=False only ingests commanders missing cached data.
    scope="delta" limits ingestion to commanders/tags used by current decks.
    """
    _ensure_schema()
    scope_key = (scope or "all").strip().lower()
    index_tags_inserted = _upsert_index_theme_tags()
    if scope_key in {"themes", "tags", "index"}:
        return {
            "commanders_processed": 0,
            "cards_inserted": 0,
            "tags_inserted": 0,
            "tag_cards_inserted": 0,
            "index_tags_inserted": index_tags_inserted,
            "errors": 0,
        }
    tag_map: dict[str, set[str]] = {}
    retry_missing = False
    if scope_key in {"delta", "active", "deck", "current"}:
        targets, tag_map = _load_active_targets()
    elif scope_key in {"missing", "failed"}:
        retry_missing = True
        targets = _load_commander_targets()
    else:
        targets = _load_commander_targets()
    top_limits: dict[str, int] = {}
    if scope_key not in {"missing", "failed"} and _INCLUDE_TOP_COMMANDERS and _TOP_COMMANDER_LIMIT > 0:
        top_targets = _load_top_index_targets(_TOP_COMMANDER_LIMIT)
        if _TOP_COMMANDER_TAG_LIMIT > 0:
            top_limits = {target.oracle_id: _TOP_COMMANDER_TAG_LIMIT for target in top_targets}
        if top_targets:
            merged_targets = {target.oracle_id: target for target in targets}
            for target in top_targets:
                merged_targets.setdefault(target.oracle_id, target)
            targets = list(merged_targets.values())
    if _TOP_COMMANDER_TAG_LIMIT > 0:
        for target in targets:
            top_limits.setdefault(target.oracle_id, _TOP_COMMANDER_TAG_LIMIT)
    if not targets:
        return {
            "commanders_processed": 0,
            "cards_inserted": 0,
            "tags_inserted": 0,
            "errors": 1,
            "index_tags_inserted": index_tags_inserted,
        }

    existing_ids: set[str] = set()
    existing_tag_pairs: set[tuple[str, str]] = set()
    existing_category_ids: set[str] = set()
    existing_tag_category_pairs: set[tuple[str, str]] = set()
    existing_top_counts: dict[str, int] = {}
    if not full_refresh:
        existing_ids = {
            row[0]
            for row in db.session.query(EdhrecCommanderCard.commander_oracle_id).distinct().all()
            if row and row[0]
        }
        existing_category_ids = {
            row[0]
            for row in db.session.query(EdhrecCommanderCategoryCard.commander_oracle_id).distinct().all()
            if row and row[0]
        }
        if tag_map:
            existing_tag_pairs = {
                (row[0], row[1])
                for row in db.session.query(
                    EdhrecCommanderTagCard.commander_oracle_id,
                    EdhrecCommanderTagCard.tag,
                )
                .distinct()
                .all()
                if row and row[0] and row[1]
            }
            existing_tag_category_pairs = {
                (row[0], row[1])
                for row in db.session.query(
                    EdhrecCommanderTagCategoryCard.commander_oracle_id,
                    EdhrecCommanderTagCategoryCard.tag,
                )
                .distinct()
                .all()
                if row and row[0] and row[1]
            }
        if top_limits:
            top_ids = list(top_limits.keys())
            rows = (
                db.session.query(
                    EdhrecCommanderTagCard.commander_oracle_id,
                    func.count(func.distinct(EdhrecCommanderTagCard.tag)),
                )
                .filter(EdhrecCommanderTagCard.commander_oracle_id.in_(top_ids))
                .group_by(EdhrecCommanderTagCard.commander_oracle_id)
                .all()
            )
            existing_top_counts = {
                row[0]: int(row[1] or 0)
                for row in rows
                if row and row[0]
            }
        filtered: list[CommanderTarget] = []
        for target in targets:
            needs_commander = (
                target.oracle_id not in existing_ids
                or target.oracle_id not in existing_category_ids
            )
            tag_set = tag_map.get(target.oracle_id, set())
            needs_tags = any(
                (target.oracle_id, tag) not in existing_tag_pairs
                or (target.oracle_id, tag) not in existing_tag_category_pairs
                for tag in tag_set
            )
            top_limit = top_limits.get(target.oracle_id, 0)
            needs_top_tags = bool(top_limit) and (existing_top_counts.get(target.oracle_id, 0) < top_limit)
            if needs_commander or needs_tags or needs_top_tags:
                filtered.append(target)
        targets = filtered

    if limit is not None and limit > 0:
        targets = targets[:limit]

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "DragonsVault/6 (+https://dragonsvault.app)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
    )

    commanders_processed = 0
    cards_inserted = 0
    tags_inserted = 0
    tag_cards_inserted = 0
    errors = 0
    last_request_at = 0.0
    missing_slugs = _prune_missing_slugs(_load_missing_slugs())
    if scope_key in {"missing", "failed"}:
        missing_oracle_ids = _missing_oracle_ids(missing_slugs)
        if not missing_oracle_ids:
            return {
                "commanders_processed": 0,
                "cards_inserted": 0,
                "tags_inserted": 0,
                "tag_cards_inserted": 0,
                "errors": 0,
            }
        targets = [target for target in targets if target.oracle_id in missing_oracle_ids]

    for idx, target in enumerate(targets, start=1):
        slug_candidates = _slug_candidates_for_target(target)
        if not slug_candidates:
            errors += 1
            _LOG.warning("EDHREC slug missing for %s", target.name)
            continue
        if not retry_missing:
            candidates_to_try = [slug for slug in slug_candidates if slug not in missing_slugs]
            if not candidates_to_try:
                continue
        else:
            candidates_to_try = slug_candidates
        tags_for_commander = tag_map.get(target.oracle_id, set())
        top_tag_limit = top_limits.get(target.oracle_id, 0)
        needs_commander = full_refresh or target.oracle_id not in existing_ids
        needs_top_tags = bool(top_tag_limit) and (
            full_refresh or existing_top_counts.get(target.oracle_id, 0) < top_tag_limit
        )

        payload = raw_json = None
        synergy_rows: list[dict] = []
        category_rows: list[dict] = []
        tags: list[str] = []
        slug_used = ""
        if needs_commander or needs_top_tags:
            fetch_error = None
            for slug in candidates_to_try:
                last_request_at = _rate_limit(last_request_at)
                url = f"https://edhrec.com/commanders/{slug}"
                payload, raw_json, fetch_error = _fetch_commander_json(session, url)
                if fetch_error == "Commander page not found.":
                    missing_slugs[slug] = {
                        "name": target.name,
                        "oracle_id": target.oracle_id,
                        "last_seen": _now_iso(),
                    }
                    continue
                if fetch_error:
                    break
                slug_used = slug
                break
            if fetch_error and not slug_used:
                errors += 1
                _LOG.warning("EDHREC fetch failed for %s: %s", target.name, fetch_error)
                continue
            if not payload or not raw_json:
                errors += 1
                _LOG.warning("EDHREC payload missing for %s", target.name)
                continue

            _clear_missing_for_oracle(missing_slugs, target.oracle_id)
            views = _extract_cardviews(payload)
            synergy_rows = _map_synergy_cards(views)
            cardlists = _extract_cardlists(payload)
            category_rows = _map_category_cards(cardlists)
            tags = _upsert_edhrec_tags(_normalize_tag_candidates(raw_json))

        top_tags: list[str] = []
        if needs_top_tags and tags:
            top_tags = tags[:top_tag_limit]

        tags_to_fetch = _merge_tags(tags_for_commander, top_tags)
        if not full_refresh:
            tags_to_fetch = [
                tag
                for tag in tags_to_fetch
                if (target.oracle_id, tag) not in existing_tag_pairs
            ]

        tag_card_rows: dict[str, list[dict]] = {}
        tag_category_rows: dict[str, list[dict]] = {}
        tag_type_rows: dict[str, list[dict]] = {}
        tag_cards_added = 0
        for tag in tags_to_fetch:
            tag_slug = slugify_theme(tag)
            if not tag_slug:
                continue
            last_request_at = _rate_limit(last_request_at)
            tag_base = slug_used or candidates_to_try[0]
            tag_url = f"https://edhrec.com/commanders/{tag_base}/{tag_slug}"
            tag_payload, tag_raw_json, tag_error = _fetch_commander_json(session, tag_url)
            if tag_error:
                if tag_error == "Commander page not found.":
                    _LOG.info("EDHREC tag page not found for %s (%s).", target.name, tag)
                    continue
                errors += 1
                _LOG.warning("EDHREC tag fetch failed for %s (%s): %s", target.name, tag, tag_error)
                continue
            if not tag_payload:
                errors += 1
                _LOG.warning("EDHREC tag payload missing for %s (%s).", target.name, tag)
                continue
            tag_views = _extract_cardviews(tag_payload)
            tag_rows = _map_synergy_cards(tag_views)
            tag_cardlists = _extract_cardlists(tag_payload)
            tag_category = _map_category_cards(tag_cardlists)
            tag_type_dist = _extract_type_distribution(tag_payload or tag_raw_json)
            if tag_rows:
                tag_card_rows[tag] = tag_rows
                tag_cards_added += len(tag_rows)
            if tag_category:
                tag_category_rows[tag] = tag_category
            if tag_type_dist:
                tag_type_rows[tag] = tag_type_dist

        commander_type_rows = _extract_type_distribution(payload or raw_json)
        try:
            if needs_commander:
                EdhrecCommanderCard.query.filter_by(
                    commander_oracle_id=target.oracle_id
                ).delete(synchronize_session=False)
                if synergy_rows:
                    db.session.bulk_insert_mappings(
                        EdhrecCommanderCard,
                        [
                            {
                                "commander_oracle_id": target.oracle_id,
                                **row,
                            }
                            for row in synergy_rows
                        ],
                    )

                EdhrecCommanderTag.query.filter_by(
                    commander_oracle_id=target.oracle_id
                ).delete(synchronize_session=False)
                if tags:
                    db.session.bulk_insert_mappings(
                        EdhrecCommanderTag,
                        [
                            {"commander_oracle_id": target.oracle_id, "tag": tag}
                            for tag in tags
                        ],
                    )

                if commander_type_rows:
                    EdhrecCommanderTypeDistribution.query.filter_by(
                        commander_oracle_id=target.oracle_id,
                        tag="",
                    ).delete(synchronize_session=False)
                    db.session.bulk_insert_mappings(
                        EdhrecCommanderTypeDistribution,
                        [
                            {
                                "commander_oracle_id": target.oracle_id,
                                "tag": "",
                                **row,
                            }
                            for row in commander_type_rows
                        ],
                    )

            if category_rows:
                EdhrecCommanderCategoryCard.query.filter_by(
                    commander_oracle_id=target.oracle_id
                ).delete(synchronize_session=False)
                db.session.bulk_insert_mappings(
                    EdhrecCommanderCategoryCard,
                    [
                        {
                            "commander_oracle_id": target.oracle_id,
                            **row,
                        }
                        for row in category_rows
                    ],
                )

            for tag, rows in tag_card_rows.items():
                EdhrecCommanderTagCard.query.filter_by(
                    commander_oracle_id=target.oracle_id,
                    tag=tag,
                ).delete(synchronize_session=False)
                db.session.bulk_insert_mappings(
                    EdhrecCommanderTagCard,
                    [
                        {
                            "commander_oracle_id": target.oracle_id,
                            "tag": tag,
                            **row,
                        }
                        for row in rows
                    ],
                )
            for tag, rows in tag_category_rows.items():
                EdhrecCommanderTagCategoryCard.query.filter_by(
                    commander_oracle_id=target.oracle_id,
                    tag=tag,
                ).delete(synchronize_session=False)
                db.session.bulk_insert_mappings(
                    EdhrecCommanderTagCategoryCard,
                    [
                        {
                            "commander_oracle_id": target.oracle_id,
                            "tag": tag,
                            **row,
                        }
                        for row in rows
                    ],
                )
            for tag, rows in tag_type_rows.items():
                EdhrecCommanderTypeDistribution.query.filter_by(
                    commander_oracle_id=target.oracle_id,
                    tag=tag,
                ).delete(synchronize_session=False)
                db.session.bulk_insert_mappings(
                    EdhrecCommanderTypeDistribution,
                    [
                        {
                            "commander_oracle_id": target.oracle_id,
                            "tag": tag,
                            **row,
                        }
                        for row in rows
                    ],
                )
            db.session.commit()
        except SQLAlchemyError as exc:
            db.session.rollback()
            errors += 1
            _LOG.warning("EDHREC cache write failed for %s: %s", target.name, exc)
            continue

        commanders_processed += 1
        cards_inserted += len(synergy_rows)
        tags_inserted += len(tags)
        tag_cards_inserted += tag_cards_added

        if idx == len(targets) or idx % 50 == 0:
            _LOG.info("EDHREC ingestion progress: %s/%s commanders.", idx, len(targets))

    try:
        EdhrecTagCommander.query.delete(synchronize_session=False)
        rows = db.session.query(EdhrecCommanderTag.tag, EdhrecCommanderTag.commander_oracle_id).all()
        if rows:
            db.session.bulk_insert_mappings(
                EdhrecTagCommander,
                [{"tag": tag, "commander_oracle_id": oracle_id} for tag, oracle_id in rows],
            )
        _set_metadata("last_updated", _now_iso())
        _set_metadata("source_version", _source_version_label())
        _set_metadata("missing_slugs", json.dumps(missing_slugs))
        db.session.commit()
    except SQLAlchemyError as exc:
        db.session.rollback()
        errors += 1
        _LOG.warning("EDHREC tag mapping rebuild failed: %s", exc)

    return {
        "commanders_processed": commanders_processed,
        "cards_inserted": cards_inserted,
        "tags_inserted": tags_inserted,
        "tag_cards_inserted": tag_cards_inserted,
        "index_tags_inserted": index_tags_inserted,
        "errors": errors,
    }


def ingest_commander_tag_data(
    commander_oracle_id: str,
    commander_name: str | None,
    tags: Iterable[str] | None,
    *,
    force_refresh: bool = True,
) -> dict:
    _ensure_schema()
    oracle_id = (commander_oracle_id or "").strip()
    if not oracle_id:
        return {"status": "error", "message": "Commander is missing."}

    cache_ready = False
    try:
        cache_ready = sc.ensure_cache_loaded()
    except Exception as exc:
        _LOG.warning("Scryfall cache unavailable for EDHREC fetch: %s", exc)

    index_slugs = _load_edhrec_index_slugs() if _USE_INDEX_SLUGS else {}
    target = _commander_target_from_oracle(
        oracle_id,
        commander_name,
        index_slugs=index_slugs,
        cache_ready=cache_ready,
    )
    if not target:
        return {"status": "error", "message": "Commander not found in cache."}

    requested_tags = _normalize_requested_tags(tags)
    if not force_refresh:
        commander_ready = (
            EdhrecCommanderCategoryCard.query.filter_by(
                commander_oracle_id=target.oracle_id
            ).first()
            is not None
        )
        tags_ready = True
        for tag in requested_tags:
            exists = (
                EdhrecCommanderTagCategoryCard.query.filter_by(
                    commander_oracle_id=target.oracle_id,
                    tag=tag,
                ).first()
                is not None
            )
            if not exists:
                tags_ready = False
                break
        if commander_ready and tags_ready:
            return {"status": "ok", "message": "EDHREC data already cached."}

    slug_candidates = _slug_candidates_for_target(target)
    if not slug_candidates:
        return {"status": "error", "message": "Unable to derive EDHREC slug."}

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "DragonsVault/6 (+https://dragonsvault.app)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
    )

    last_request_at = 0.0
    payload = raw_json = None
    slug_used = ""
    fetch_error = None
    for slug in slug_candidates:
        last_request_at = _rate_limit(last_request_at)
        url = f"https://edhrec.com/commanders/{slug}"
        payload, raw_json, fetch_error = _fetch_commander_json(session, url)
        if fetch_error == "Commander page not found.":
            continue
        if fetch_error:
            break
        slug_used = slug
        break

    if fetch_error and not slug_used:
        _LOG.warning("EDHREC fetch failed for %s: %s", target.name, fetch_error)
        return {"status": "error", "message": fetch_error}
    if not payload or not raw_json:
        _LOG.warning("EDHREC payload missing for %s", target.name)
        return {"status": "error", "message": "EDHREC payload missing."}

    views = _extract_cardviews(payload)
    synergy_rows = _map_synergy_cards(views)
    cardlists = _extract_cardlists(payload)
    category_rows = _map_category_cards(cardlists)
    edhrec_tags = _upsert_edhrec_tags(_normalize_tag_candidates(raw_json))

    tag_card_rows: dict[str, list[dict]] = {}
    tag_category_rows: dict[str, list[dict]] = {}
    tag_type_rows: dict[str, list[dict]] = {}
    tag_cards_added = 0
    for tag in requested_tags:
        tag_slug = slugify_theme(tag)
        if not tag_slug:
            continue
        last_request_at = _rate_limit(last_request_at)
        tag_base = slug_used or slug_candidates[0]
        tag_url = f"https://edhrec.com/commanders/{tag_base}/{tag_slug}"
        tag_payload, tag_raw_json, tag_error = _fetch_commander_json(session, tag_url)
        if tag_error:
            if tag_error == "Commander page not found.":
                _LOG.info("EDHREC tag page not found for %s (%s).", target.name, tag)
            else:
                _LOG.warning("EDHREC tag fetch failed for %s (%s): %s", target.name, tag, tag_error)
            continue
        if not tag_payload:
            _LOG.warning("EDHREC tag payload missing for %s (%s).", target.name, tag)
            continue
        tag_views = _extract_cardviews(tag_payload)
        tag_rows = _map_synergy_cards(tag_views)
        tag_cardlists = _extract_cardlists(tag_payload)
        tag_category = _map_category_cards(tag_cardlists)
        tag_type_dist = _extract_type_distribution(tag_payload or tag_raw_json)
        if tag_rows:
            tag_card_rows[tag] = tag_rows
            tag_cards_added += len(tag_rows)
        if tag_category:
            tag_category_rows[tag] = tag_category
        if tag_type_dist:
            tag_type_rows[tag] = tag_type_dist

    commander_type_rows = _extract_type_distribution(payload or raw_json)
    try:
        with db.session.no_autoflush:
            EdhrecCommanderCard.query.filter_by(
                commander_oracle_id=target.oracle_id
            ).delete(synchronize_session=False)
            if synergy_rows:
                db.session.bulk_insert_mappings(
                    EdhrecCommanderCard,
                    [
                        {
                            "commander_oracle_id": target.oracle_id,
                            **row,
                        }
                        for row in synergy_rows
                    ],
                )

            EdhrecCommanderCategoryCard.query.filter_by(
                commander_oracle_id=target.oracle_id
            ).delete(synchronize_session=False)
            if category_rows:
                db.session.bulk_insert_mappings(
                    EdhrecCommanderCategoryCard,
                    [
                        {
                            "commander_oracle_id": target.oracle_id,
                            **row,
                        }
                        for row in category_rows
                    ],
                )

            EdhrecCommanderTag.query.filter_by(
                commander_oracle_id=target.oracle_id
            ).delete(synchronize_session=False)
            if edhrec_tags:
                db.session.bulk_insert_mappings(
                    EdhrecCommanderTag,
                    [
                        {"commander_oracle_id": target.oracle_id, "tag": tag}
                        for tag in edhrec_tags
                    ],
                )

            if commander_type_rows:
                EdhrecCommanderTypeDistribution.query.filter_by(
                    commander_oracle_id=target.oracle_id,
                    tag="",
                ).delete(synchronize_session=False)
                db.session.bulk_insert_mappings(
                    EdhrecCommanderTypeDistribution,
                    [
                        {
                            "commander_oracle_id": target.oracle_id,
                            "tag": "",
                            **row,
                        }
                        for row in commander_type_rows
                    ],
                )

            for tag, rows in tag_card_rows.items():
                EdhrecCommanderTagCard.query.filter_by(
                    commander_oracle_id=target.oracle_id,
                    tag=tag,
                ).delete(synchronize_session=False)
                db.session.bulk_insert_mappings(
                    EdhrecCommanderTagCard,
                    [
                        {
                            "commander_oracle_id": target.oracle_id,
                            "tag": tag,
                            **row,
                        }
                        for row in rows
                    ],
                )
            for tag, rows in tag_category_rows.items():
                EdhrecCommanderTagCategoryCard.query.filter_by(
                    commander_oracle_id=target.oracle_id,
                    tag=tag,
                ).delete(synchronize_session=False)
                db.session.bulk_insert_mappings(
                    EdhrecCommanderTagCategoryCard,
                    [
                        {
                            "commander_oracle_id": target.oracle_id,
                            "tag": tag,
                            **row,
                        }
                        for row in rows
                    ],
                )

            for tag, rows in tag_type_rows.items():
                EdhrecCommanderTypeDistribution.query.filter_by(
                    commander_oracle_id=target.oracle_id,
                    tag=tag,
                ).delete(synchronize_session=False)
                db.session.bulk_insert_mappings(
                    EdhrecCommanderTypeDistribution,
                    [
                        {
                            "commander_oracle_id": target.oracle_id,
                            "tag": tag,
                            **row,
                        }
                        for row in rows
                    ],
                )

            EdhrecTagCommander.query.filter_by(
                commander_oracle_id=target.oracle_id
            ).delete(synchronize_session=False)
            if edhrec_tags:
                db.session.bulk_insert_mappings(
                    EdhrecTagCommander,
                    [
                        {"tag": tag, "commander_oracle_id": target.oracle_id}
                        for tag in edhrec_tags
                    ],
                )

            _set_metadata("last_updated", _now_iso())
            _set_metadata("source_version", _source_version_label())
            db.session.commit()
    except SQLAlchemyError as exc:
        db.session.rollback()
        _LOG.warning("EDHREC cache write failed for %s: %s", target.name, exc)
        return {"status": "error", "message": "Database error while saving EDHREC data."}

    return {
        "status": "ok",
        "message": f"EDHREC data refreshed for {target.name}.",
        "cards_inserted": len(synergy_rows),
        "tags_inserted": len(edhrec_tags),
        "tag_cards_inserted": tag_cards_added,
    }


__all__ = ["run_monthly_edhrec_ingestion", "ingest_commander_tag_data"]
