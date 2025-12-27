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
from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError

from extensions import db
from models import (
    EdhrecCommanderCard,
    EdhrecCommanderTag,
    EdhrecMetadata,
    EdhrecTagCommander,
)
from services import scryfall_cache as sc
from services.deck_tags import resolve_deck_tag_from_slug
from services.edhrec_client import edhrec_index, edhrec_service_enabled, slugify_commander

_LOG = logging.getLogger(__name__)

_NEXT_DATA_RE = re.compile(r'__NEXT_DATA__" type="application/json">(.*?)</script>', re.DOTALL)

_REQUEST_INTERVAL_SECONDS = max(1.0, float(os.getenv("EDHREC_INGEST_INTERVAL", "1.0")))
_MAX_SYNERGY_CARDS = int(os.getenv("EDHREC_INGEST_MAX_CARDS", "200"))
_DEFAULT_SOURCE_VERSION = os.getenv("EDHREC_SOURCE_VERSION")
_MISSING_TTL_DAYS = int(os.getenv("EDHREC_MISSING_TTL_DAYS", "30"))


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


_USE_INDEX_SLUGS = _bool_env("EDHREC_USE_INDEX_SLUGS", True)
_INDEX_ONLY = _bool_env("EDHREC_INDEX_ONLY", False)


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
            EdhrecCommanderTag.__table__,
            EdhrecTagCommander.__table__,
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
        if slugify_commander(name) != slugify_commander(sample_name):
            continue
        mapping[oracle_id] = {"name": name, "slug": slug}
    return mapping


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
        candidate = resolve_deck_tag_from_slug(str(slug or ""))
        if not candidate and label:
            candidate = resolve_deck_tag_from_slug(str(label))
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
    for idx, item in enumerate(items[:_MAX_SYNERGY_CARDS], start=1):
        ranked.append(
            {
                "card_oracle_id": item["card_oracle_id"],
                "synergy_rank": idx,
                "synergy_score": item.get("synergy_score"),
            }
        )
    return ranked


def _set_metadata(key: str, value: str) -> None:
    if not key:
        return
    db.session.merge(EdhrecMetadata(key=key, value=value))


def _source_version_label() -> str:
    if _DEFAULT_SOURCE_VERSION:
        return _DEFAULT_SOURCE_VERSION
    now = datetime.now(timezone.utc)
    return f"edhrec-{now.year}-{now.month:02d}"


def run_monthly_edhrec_ingestion(limit: int | None = None, *, full_refresh: bool = True) -> dict:
    """
    Run the EDHREC commander ingestion job.

    full_refresh=True refreshes all commanders.
    full_refresh=False only ingests commanders missing cached data.
    """
    _ensure_schema()
    targets = _load_commander_targets()
    if not targets:
        return {
            "commanders_processed": 0,
            "cards_inserted": 0,
            "tags_inserted": 0,
            "errors": 1,
        }

    if not full_refresh:
        existing_ids = {
            row[0]
            for row in db.session.query(EdhrecCommanderCard.commander_oracle_id).distinct().all()
            if row and row[0]
        }
        targets = [target for target in targets if target.oracle_id not in existing_ids]

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
    errors = 0
    last_request_at = 0.0
    missing_slugs = _prune_missing_slugs(_load_missing_slugs())

    for idx, target in enumerate(targets, start=1):
        last_request_at = _rate_limit(last_request_at)
        slug_source = target.slug_name or target.name
        slug = (target.slug_override or "").strip() or slugify_commander(slug_source)
        if not slug:
            errors += 1
            _LOG.warning("EDHREC slug missing for %s", target.name)
            continue
        missing_info = missing_slugs.get(slug)
        if missing_info:
            continue
        url = f"https://edhrec.com/commanders/{slug}"
        payload, raw_json, error = _fetch_commander_json(session, url)
        if error:
            if error == "Commander page not found.":
                missing_slugs[slug] = {
                    "name": target.name,
                    "oracle_id": target.oracle_id,
                    "last_seen": _now_iso(),
                }
            errors += 1
            _LOG.warning("EDHREC fetch failed for %s: %s", target.name, error)
            continue
        if not payload or not raw_json:
            errors += 1
            _LOG.warning("EDHREC payload missing for %s", target.name)
            continue

        views = _extract_cardviews(payload)
        synergy_rows = _map_synergy_cards(views)
        tags = _normalize_tag_candidates(raw_json)

        try:
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
            db.session.commit()
        except SQLAlchemyError as exc:
            db.session.rollback()
            errors += 1
            _LOG.warning("EDHREC cache write failed for %s: %s", target.name, exc)
            continue

        commanders_processed += 1
        cards_inserted += len(synergy_rows)
        tags_inserted += len(tags)

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
        "errors": errors,
    }


__all__ = ["run_monthly_edhrec_ingestion"]
