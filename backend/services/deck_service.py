"""
Canonical service for deck stats computation.
Other services may read, but MUST NOT compute or write deck stats.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Iterable

from flask import current_app, has_app_context
from sqlalchemy import event, inspect, func
from sqlalchemy.orm import Session

from extensions import db
from models import Card, DeckStats, FolderRole
from services.symbols_cache import colors_to_icons
from utils.time import utcnow

RE_COST_SYMBOL = re.compile(r"\{([^}]+)\}")
DECK_STATS_VERSION = 1
_DECK_STATS_DIRTY_KEY = "deck_stats_dirty"
_DECK_STATS_RECOMPUTING = "deck_stats_recomputing"
_LISTENERS_REGISTERED = False


def _faces_list(faces_json) -> list[dict]:
    if not faces_json:
        return []
    if isinstance(faces_json, dict):
        faces = faces_json.get("faces") or []
    else:
        faces = faces_json
    return [face for face in faces if isinstance(face, dict)]


def _mana_costs_from_faces(faces_json) -> list[str]:
    costs = [face.get("mana_cost") for face in _faces_list(faces_json) if face.get("mana_cost")]
    return [cost for cost in costs if cost]


def _add_colored_pips(cost_str: str | None, qty: int, counts: dict[str, int]) -> None:
    if not cost_str:
        return
    for sym in RE_COST_SYMBOL.findall(cost_str):
        s_val = sym.upper()
        for ch in ("W", "U", "B", "R", "G"):
            if ch in s_val:
                counts[ch] += qty


def _is_permanent_type(type_line: str | None) -> bool:
    lowered = (type_line or "").lower()
    return any(token in lowered for token in ("land", "artifact", "creature", "enchantment", "planeswalker", "battle"))


def _colors_from_oracle_text_add(text: str | None) -> set[str]:
    out: set[str] = set()
    if not text:
        return out
    upper = text.upper()
    if "ADD" not in upper:
        return out
    for sym in RE_COST_SYMBOL.findall(text):
        symbol = sym.upper()
        for ch in ("W", "U", "B", "R", "G", "C"):
            if ch in symbol:
                out.add(ch)
    if "ANY COLOR" in upper:
        out.update({"W", "U", "B", "R", "G"})
    return out


def _deck_stats_payload(folder_id: int, *, session=None) -> dict[str, Any]:
    sess = session or db.session
    mana_pip_all = {c: 0 for c in ["W", "U", "B", "R", "G"]}
    mana_pip_non_land = {c: 0 for c in ["W", "U", "B", "R", "G"]}
    production_counts = {c: 0 for c in ["W", "U", "B", "R", "G", "C"]}
    curve_bins = {"0": 0, "1": 0, "2": 0, "3": 0, "4": 0, "5": 0, "6": 0, "7+": 0}
    missing_cmc = 0
    total_mana = 0.0
    total_mana_qty = 0

    rows = (
        sess.query(
            Card.quantity,
            Card.type_line,
            Card.mana_value,
            Card.faces_json,
            Card.oracle_text,
        )
        .filter(Card.folder_id == folder_id)
        .all()
    )

    for qty, type_line, mana_value, faces_json, oracle_text in rows:
        qty = int(qty or 0)
        if qty <= 0:
            continue
        is_land = bool(type_line and "Land" in type_line)
        text = oracle_text or ""
        if not text and faces_json:
            face_texts = [face.get("oracle_text") for face in _faces_list(faces_json) if face.get("oracle_text")]
            if face_texts:
                text = " // ".join(face_texts)

        for mana_cost in _mana_costs_from_faces(faces_json):
            _add_colored_pips(mana_cost, qty, mana_pip_all)
            if not is_land:
                _add_colored_pips(mana_cost, qty, mana_pip_non_land)

        if not is_land:
            if mana_value is None:
                missing_cmc += qty
            else:
                try:
                    mv = float(mana_value)
                except Exception:
                    mv = None
                if mv is None:
                    missing_cmc += qty
                else:
                    total_mana += mv * qty
                    total_mana_qty += qty
                    bucket_val = int(round(mv))
                    if bucket_val < 0:
                        bucket_val = 0
                    bucket = str(bucket_val) if bucket_val <= 6 else "7+"
                    curve_bins[bucket] += qty

        colors = _colors_from_oracle_text_add(text)
        if colors and _is_permanent_type(type_line):
            for ch in colors:
                production_counts[ch] += qty

    avg_mana = (total_mana / total_mana_qty) if total_mana_qty else None

    return {
        "avg_mana": avg_mana,
        "curve": {
            "bins": curve_bins,
            "missing": missing_cmc,
            "total": sum(curve_bins.values()),
        },
        "pips": {
            "all": mana_pip_all,
            "non_land": mana_pip_non_land,
            "production": production_counts,
        },
    }


def _deck_stats_source_version(folder_id: int, *, session=None) -> str:
    sess = session or db.session
    row = (
        sess.query(func.max(Card.updated_at), func.count(Card.id))
        .filter(Card.folder_id == folder_id)
        .one()
    )
    max_updated_at, count = row
    ts = max_updated_at.isoformat() if max_updated_at else "none"
    return f"cards:{ts}|{count}"


def _is_deck_folder(folder_id: int, *, session=None) -> bool:
    if not folder_id:
        return False
    sess = session or db.session
    exists = (
        sess.query(FolderRole.folder_id)
        .filter(
            FolderRole.folder_id == folder_id,
            FolderRole.role.in_(FolderRole.DECK_ROLES),
        )
        .first()
    )
    return bool(exists)


def recompute_deck_stats(
    folder_id: int,
    *,
    source_version: str | None = None,
    session=None,
) -> DeckStats | None:
    if not folder_id:
        return None
    sess = session or db.session
    if not _is_deck_folder(folder_id, session=sess):
        sess.query(DeckStats).filter(DeckStats.folder_id == folder_id).delete(synchronize_session=False)
        return None

    payload = _deck_stats_payload(folder_id, session=sess)
    source_version = source_version or _deck_stats_source_version(folder_id, session=sess)
    stats = sess.get(DeckStats, folder_id) or DeckStats(folder_id=folder_id)
    stats.avg_mana = payload.get("avg_mana")
    stats.curve_json = json.dumps(payload.get("curve") or {}, ensure_ascii=True)
    stats.color_pips_json = json.dumps(payload.get("pips") or {}, ensure_ascii=True)
    stats.version = DECK_STATS_VERSION
    stats.source_version = source_version
    stats.last_updated = utcnow()
    sess.add(stats)
    return stats


def _load_json(text: str | None) -> dict:
    if not text:
        return {}
    try:
        data = json.loads(text)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def get_deck_stats(folder_id: int) -> dict[str, Any]:
    stats = db.session.get(DeckStats, folder_id)
    try:
        expected_source = _deck_stats_source_version(folder_id, session=db.session) if stats else None
        if (
            not stats
            or stats.version != DECK_STATS_VERSION
            or stats.source_version != (expected_source or "")
        ):
            stats = recompute_deck_stats(folder_id, source_version=expected_source, session=db.session)
            if stats:
                db.session.commit()
    except Exception:
        db.session.rollback()
        logger = current_app.logger if has_app_context() and current_app else logging.getLogger(__name__)
        logger.error("Deck stats recompute failed.", exc_info=True)
        return {"avg_mana": None, "curve": {}, "pips": {}, "last_updated": None}
    if not stats:
        return {"avg_mana": None, "curve": {}, "pips": {}, "last_updated": None}
    return {
        "avg_mana": stats.avg_mana,
        "curve": _load_json(stats.curve_json),
        "pips": _load_json(stats.color_pips_json),
        "last_updated": stats.last_updated,
    }


def deck_curve_rows(folder_id: int, *, mode: str = "detail") -> list[dict]:
    stats = get_deck_stats(folder_id)
    curve = stats.get("curve") or {}
    bins = dict(curve.get("bins") or {})
    if mode == "drawer":
        max_curve = max(bins.values()) if bins else 0
        rows = []
        for bucket in ["0", "1", "2", "3", "4", "5", "6", "7+"]:
            count = int(bins.get(bucket) or 0)
            if count <= 0:
                continue
            pct = 100.0 * count / max_curve if max_curve else 0.0
            rows.append({"label": bucket, "count": count, "pct": pct})
        return rows

    total_curve = sum(int(val or 0) for val in bins.values()) or 1
    rows = []
    for bucket in ["0", "1", "2", "3", "4", "5", "6", "7+"]:
        count = int(bins.get(bucket) or 0)
        pct = int(round(100.0 * count / total_curve)) if total_curve else 0
        rows.append({"label": bucket, "count": count, "pct": pct})
    return rows


def deck_curve_missing(folder_id: int) -> int:
    stats = get_deck_stats(folder_id)
    curve = stats.get("curve") or {}
    return int(curve.get("missing") or 0)


def deck_mana_pip_dist(folder_id: int, *, mode: str = "detail") -> list:
    stats = get_deck_stats(folder_id)
    pips = stats.get("pips") or {}
    source_key = "all" if mode == "drawer" else "non_land"
    counts = pips.get(source_key) or {}

    def _pip_src(ch: str | None) -> str | None:
        if not ch:
            return None
        arr = colors_to_icons([ch], use_local=True)
        return arr[0] if arr else None

    if mode == "drawer":
        return [
            {
                "color": c,
                "icon": _pip_src(c),
                "count": int(counts.get(c) or 0),
            }
            for c in ["W", "U", "B", "R", "G"]
            if int(counts.get(c) or 0) > 0
        ]

    return [
        (c, _pip_src(c), int(counts.get(c) or 0))
        for c in ["W", "U", "B", "R", "G"]
        if int(counts.get(c) or 0) > 0
    ]


def deck_land_mana_sources(folder_id: int, *, filter_by_identity: bool = True) -> list[tuple[str, str | None, int]]:
    stats = get_deck_stats(folder_id)
    pips = stats.get("pips") or {}
    production = pips.get("production") or {}

    def _pip_src(ch: str | None) -> str | None:
        if not ch:
            return None
        arr = colors_to_icons([ch], use_local=True)
        return arr[0] if arr else None

    allowed = {"W", "U", "B", "R", "G", "C"}
    if filter_by_identity:
        from routes.base import compute_folder_color_identity

        letters, _label = compute_folder_color_identity(folder_id)
        allowed = {ch for ch in (letters or "") if ch in {"W", "U", "B", "R", "G"}}
        allowed.add("C")

    return [
        (c, _pip_src(c if c != "C" else None), int(production.get(c) or 0))
        for c in ["W", "U", "B", "R", "G", "C"]
        if int(production.get(c) or 0) > 0 and c in allowed
    ]


def register_deck_stats_listeners() -> None:
    global _LISTENERS_REGISTERED
    if _LISTENERS_REGISTERED:
        return
    _LISTENERS_REGISTERED = True

    @event.listens_for(db.session, "after_flush")
    def _track_deck_stat_changes(session, _flush_context):
        if session.info.get(_DECK_STATS_RECOMPUTING):
            return
        folder_ids: set[int] = set()
        for obj in session.new.union(session.dirty).union(session.deleted):
            if isinstance(obj, Card):
                if obj.folder_id:
                    folder_ids.add(obj.folder_id)
                try:
                    hist = inspect(obj).attrs.folder_id.history
                    for fid in hist.added or []:
                        if fid:
                            folder_ids.add(fid)
                    for fid in hist.deleted or []:
                        if fid:
                            folder_ids.add(fid)
                except Exception:
                    pass
            elif isinstance(obj, FolderRole):
                if obj.folder_id:
                    folder_ids.add(obj.folder_id)
        if folder_ids:
            bucket = session.info.setdefault(_DECK_STATS_DIRTY_KEY, set())
            bucket.update(folder_ids)

    @event.listens_for(db.session, "after_commit")
    def _rebuild_deck_stats(session):
        folder_ids = session.info.pop(_DECK_STATS_DIRTY_KEY, set())
        if not folder_ids:
            return
        try:
            with Session(db.engine) as isolated_session:
                for folder_id in sorted(folder_ids):
                    recompute_deck_stats(folder_id, session=isolated_session)
                isolated_session.commit()
        except Exception:
            if has_app_context():
                current_app.logger.exception("Failed to recompute deck stats")


def create_proxy_deck():
    from services import card_service

    return card_service.create_proxy_deck()


def create_proxy_deck_bulk():
    from services import card_service

    return card_service.create_proxy_deck_bulk()


def api_fetch_proxy_deck():
    from services import card_service

    return card_service.api_fetch_proxy_deck()


def api_deck_insight(deck_id: int):
    from services import card_service

    return card_service.api_deck_insight(deck_id)


def decks_overview():
    from services import card_service

    return card_service.decks_overview()


def deck_from_collection():
    from services import card_service

    return card_service.deck_from_collection()


def deck_tokens_overview():
    from services import card_service

    return card_service.deck_tokens_overview()


def opening_hand():
    from services import card_service

    return card_service.opening_hand()


def opening_hand_play():
    from services import card_service

    return card_service.opening_hand_play()


def opening_hand_shuffle():
    from services import card_service

    return card_service.opening_hand_shuffle()


def opening_hand_draw():
    from services import card_service

    return card_service.opening_hand_draw()


def opening_hand_token_search():
    from services import card_service

    return card_service.opening_hand_token_search()


__all__ = [
    "api_deck_insight",
    "api_fetch_proxy_deck",
    "create_proxy_deck",
    "create_proxy_deck_bulk",
    "deck_curve_rows",
    "deck_curve_missing",
    "deck_from_collection",
    "deck_land_mana_sources",
    "deck_mana_pip_dist",
    "deck_tokens_overview",
    "decks_overview",
    "get_deck_stats",
    "opening_hand",
    "opening_hand_play",
    "opening_hand_draw",
    "opening_hand_shuffle",
    "opening_hand_token_search",
    "register_deck_stats_listeners",
    "recompute_deck_stats",
]
