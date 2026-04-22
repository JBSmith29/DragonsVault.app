"""EDHREC cache query helpers."""

from __future__ import annotations

import logging
from typing import Iterable

from sqlalchemy import func

from models import (
    EdhrecCommanderCard,
    EdhrecCommanderCategoryCard,
    EdhrecCommanderTag,
    EdhrecCommanderTagCard,
    EdhrecCommanderTagCategoryCard,
    EdhrecCommanderTypeDistribution,
    EdhrecTagCommander,
)
from core.domains.cards.services import scryfall_cache as sc
from core.domains.decks.services.commander_utils import primary_commander_oracle_id
from shared.cache.request_cache import request_cached

_LOG = logging.getLogger(__name__)


def synergy_percent(score: float | None) -> float | None:
    if score is None:
        return None
    return round(score * 100.0, 1)


def inclusion_percent(value: float | None) -> float | None:
    if value is None:
        return None
    clamped = min(max(float(value), 0.0), 100.0)
    return round(clamped, 1)


def normalize_tags(tags: Iterable[str] | None) -> list[str]:
    if not tags:
        return []
    seen: set[str] = set()
    ordered: list[str] = []
    for tag in tags:
        label = (tag or "").strip()
        if not label:
            continue
        key = label.casefold()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(label)
    return ordered


def get_commander_tags(commander_oracle_id: str, *, ensure_tables_fn) -> list[str]:
    ensure_tables_fn()
    commander_oracle_id = primary_commander_oracle_id(commander_oracle_id) or ""
    if not commander_oracle_id:
        return []
    rows = (
        EdhrecCommanderTag.query.filter_by(commander_oracle_id=commander_oracle_id)
        .order_by(EdhrecCommanderTag.tag.asc())
        .all()
    )
    return [row.tag for row in rows if row.tag]


def get_commander_type_distribution(
    commander_oracle_id: str,
    *,
    tag: str | None = None,
    ensure_tables_fn,
) -> list[tuple[str, int]]:
    ensure_tables_fn()
    commander_oracle_id = primary_commander_oracle_id(commander_oracle_id) or ""
    if not commander_oracle_id:
        return []
    tag_label = (tag or "").strip()
    if tag_label:
        query = EdhrecCommanderTypeDistribution.query.filter_by(
            commander_oracle_id=commander_oracle_id,
            tag=tag_label,
        )
    else:
        query = EdhrecCommanderTypeDistribution.query.filter_by(
            commander_oracle_id=commander_oracle_id,
            tag="",
        )
    rows = query.order_by(EdhrecCommanderTypeDistribution.card_type.asc()).all()
    return [(row.card_type, int(row.count or 0)) for row in rows if row.card_type]


def get_tag_commanders(tag: str, *, ensure_tables_fn) -> list[str]:
    ensure_tables_fn()
    label = (tag or "").strip()
    if not label:
        return []
    rows = (
        EdhrecTagCommander.query.filter_by(tag=label)
        .order_by(EdhrecTagCommander.commander_oracle_id.asc())
        .all()
    )
    return [row.commander_oracle_id for row in rows if row.commander_oracle_id]


def get_commander_category_groups(
    commander_oracle_id: str,
    *,
    tag: str | None = None,
    limit: int | None,
    ensure_tables_fn,
) -> list[dict]:
    ensure_tables_fn()
    commander_oracle_id = primary_commander_oracle_id(commander_oracle_id) or ""
    if not commander_oracle_id:
        return []
    tag_label = (tag or "").strip()
    if tag_label:
        query = EdhrecCommanderTagCategoryCard.query.filter_by(
            commander_oracle_id=commander_oracle_id,
            tag=tag_label,
        )
        rows = (
            query.order_by(
                func.coalesce(EdhrecCommanderTagCategoryCard.category_rank, 9999).asc(),
                EdhrecCommanderTagCategoryCard.category.asc(),
                func.coalesce(EdhrecCommanderTagCategoryCard.synergy_rank, 999999).asc(),
                func.coalesce(EdhrecCommanderTagCategoryCard.synergy_score, 0).desc(),
            ).all()
        )
    else:
        query = EdhrecCommanderCategoryCard.query.filter_by(commander_oracle_id=commander_oracle_id)
        rows = (
            query.order_by(
                func.coalesce(EdhrecCommanderCategoryCard.category_rank, 9999).asc(),
                EdhrecCommanderCategoryCard.category.asc(),
                func.coalesce(EdhrecCommanderCategoryCard.synergy_rank, 999999).asc(),
                func.coalesce(EdhrecCommanderCategoryCard.synergy_score, 0).desc(),
            ).all()
        )

    grouped: dict[str, list[dict]] = {}
    ordered: list[str] = []
    for row in rows:
        category = (row.category or "").strip()
        oracle_id = (row.card_oracle_id or "").strip()
        if not category or not oracle_id:
            continue
        if category not in grouped:
            grouped[category] = []
            ordered.append(category)
        cards = grouped[category]
        if limit is not None and len(cards) >= limit:
            continue
        cards.append(
            {
                "oracle_id": oracle_id,
                "synergy_score": float(row.synergy_score) if row.synergy_score is not None else None,
                "synergy_percent": synergy_percent(float(row.synergy_score)) if row.synergy_score is not None else None,
                "inclusion_percent": inclusion_percent(row.inclusion_percent),
                "synergy_rank": int(row.synergy_rank or 0) if row.synergy_rank is not None else None,
            }
        )

    return [{"label": category, "cards": grouped.get(category) or [], "count": len(grouped.get(category) or [])} for category in ordered]


def get_commander_tag_synergy_groups(
    commander_oracle_id: str,
    tags: Iterable[str] | None = None,
    *,
    limit: int | None,
    ensure_tables_fn,
) -> list[dict]:
    ensure_tables_fn()
    commander_oracle_id = primary_commander_oracle_id(commander_oracle_id) or ""
    if not commander_oracle_id:
        return []
    requested_tags = normalize_tags(tags)
    query = EdhrecCommanderTagCard.query.filter_by(commander_oracle_id=commander_oracle_id)
    if requested_tags:
        query = query.filter(EdhrecCommanderTagCard.tag.in_(requested_tags))
    rows = (
        query.order_by(
            EdhrecCommanderTagCard.tag.asc(),
            func.coalesce(EdhrecCommanderTagCard.synergy_score, 0).desc(),
            func.coalesce(EdhrecCommanderTagCard.synergy_rank, 999999).asc(),
        ).all()
    )
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        tag = (row.tag or "").strip()
        oracle_id = (row.card_oracle_id or "").strip()
        if not tag or not oracle_id:
            continue
        grouped.setdefault(tag, []).append(
            {
                "oracle_id": oracle_id,
                "synergy_score": float(row.synergy_score or 0.0),
                "synergy_percent": synergy_percent(float(row.synergy_score or 0.0)),
                "inclusion_percent": inclusion_percent(row.inclusion_percent),
                "synergy_rank": int(row.synergy_rank or 0) if row.synergy_rank is not None else None,
            }
        )
    ordered_tags = requested_tags or sorted(grouped.keys())
    groups: list[dict] = []
    for tag in ordered_tags:
        cards = grouped.get(tag, [])
        if limit is not None:
            cards = cards[:limit]
        groups.append({"label": tag, "cards": cards, "count": len(cards)})
    return groups


def _oracle_name_for_id(oracle_id: str, cache: dict[str, str | None]) -> str | None:
    if not oracle_id:
        return None
    if oracle_id in cache:
        return cache[oracle_id]
    try:
        prints = sc.prints_for_oracle(oracle_id) or []
    except Exception:
        cache[oracle_id] = None
        return None
    name = prints[0].get("name") if prints else None
    cache[oracle_id] = name
    return name


def _build_commander_synergy(
    commander_oracle_id: str,
    tags: Iterable[str] | None,
    *,
    prefer_tag_specific: bool,
    limit: int | None,
    ensure_tables_fn,
) -> list[dict]:
    ensure_tables_fn()
    commander_oracle_id = primary_commander_oracle_id(commander_oracle_id) or ""
    if not commander_oracle_id:
        return []

    requested_tags = normalize_tags(tags)
    commander_tags = set(get_commander_tags(commander_oracle_id, ensure_tables_fn=ensure_tables_fn))
    tag_matches = [tag for tag in requested_tags if tag in commander_tags]

    name_cache: dict[str, str | None] = {}
    results: list[dict] = []
    rows = []
    if prefer_tag_specific and tag_matches:
        try:
            tag_rows = (
                EdhrecCommanderTagCard.query.filter_by(commander_oracle_id=commander_oracle_id)
                .filter(EdhrecCommanderTagCard.tag.in_(tag_matches))
                .all()
            )
        except Exception as exc:
            _LOG.warning("EDHREC tag cache lookup failed: %s", exc)
            tag_rows = []
        if tag_rows:
            merged: dict[str, dict] = {}
            for row in tag_rows:
                oracle_id = (row.card_oracle_id or "").strip()
                if not oracle_id:
                    continue
                score = float(row.synergy_score or 0.0)
                rank = int(row.synergy_rank or 0) if row.synergy_rank is not None else None
                inclusion = inclusion_percent(row.inclusion_percent)
                current = merged.get(oracle_id)
                if not current or score > current["synergy_score"]:
                    merged[oracle_id] = {
                        "oracle_id": oracle_id,
                        "synergy_score": score,
                        "synergy_rank": rank,
                        "inclusion_percent": inclusion,
                    }
                elif score == current["synergy_score"]:
                    if inclusion is not None and (current.get("inclusion_percent") or 0) < inclusion:
                        current["inclusion_percent"] = inclusion
                    if rank is not None and (current["synergy_rank"] is None or rank < current["synergy_rank"]):
                        current["synergy_rank"] = rank
            rows = list(merged.values())

    if not rows:
        try:
            base_query = (
                EdhrecCommanderCard.query.filter_by(commander_oracle_id=commander_oracle_id)
                .order_by(
                    func.coalesce(EdhrecCommanderCard.synergy_score, 0).desc(),
                    func.coalesce(EdhrecCommanderCard.synergy_rank, 999999).asc(),
                )
            )
            rows = base_query.limit(limit).all() if limit is not None else base_query.all()
        except Exception as exc:
            _LOG.warning("EDHREC cache lookup failed: %s", exc)
            return []

    normalized_rows: list[dict] = []
    for row in rows:
        if isinstance(row, dict):
            normalized_rows.append(row)
        else:
            normalized_rows.append(
                {
                    "card_oracle_id": row.card_oracle_id,
                    "synergy_score": row.synergy_score,
                    "synergy_rank": row.synergy_rank,
                    "inclusion_percent": row.inclusion_percent,
                }
            )

    for row in normalized_rows:
        oracle_id = (row.get("card_oracle_id") or row.get("oracle_id") or "").strip()
        if not oracle_id:
            continue
        name = _oracle_name_for_id(oracle_id, name_cache) or oracle_id
        synergy_score = row.get("synergy_score")
        synergy_rank = row.get("synergy_rank")
        raw_inclusion = row.get("inclusion_percent")
        score_value = float(synergy_score or 0.0)
        results.append(
            {
                "oracle_id": oracle_id,
                "name": name,
                "synergy_score": score_value,
                "synergy_percent": synergy_percent(score_value),
                "inclusion_percent": inclusion_percent(raw_inclusion),
                "synergy_rank": int(synergy_rank or 0) if synergy_rank is not None else None,
                "source": "edhrec",
                "tag_matches": list(tag_matches),
            }
        )
    results.sort(
        key=lambda item: (
            -(item.get("synergy_score") or 0.0),
            item.get("synergy_rank") if item.get("synergy_rank") is not None else 999999,
        )
    )
    return results[:limit] if limit is not None else results


def get_commander_synergy(
    commander_oracle_id: str,
    tags: list[str] | None = None,
    *,
    prefer_tag_specific: bool,
    limit: int | None,
    ensure_tables_fn,
) -> list[dict]:
    key = (
        "edhrec_local_synergy",
        commander_oracle_id,
        tuple(normalize_tags(tags)),
        bool(prefer_tag_specific),
        limit,
    )
    return request_cached(
        key,
        lambda: _build_commander_synergy(
            commander_oracle_id,
            tags,
            prefer_tag_specific=prefer_tag_specific,
            limit=limit,
            ensure_tables_fn=ensure_tables_fn,
        ),
    )


def cache_ready(*, edhrec_cache_snapshot_fn) -> bool:
    snapshot = edhrec_cache_snapshot_fn()
    if snapshot.get("status") != "ok":
        return False
    return bool(snapshot.get("commanders", {}).get("count"))


__all__ = [
    "cache_ready",
    "get_commander_category_groups",
    "get_commander_synergy",
    "get_commander_tag_synergy_groups",
    "get_commander_tags",
    "get_commander_type_distribution",
    "get_tag_commanders",
]
