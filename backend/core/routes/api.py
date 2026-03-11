"""JSON API blueprint to decouple frontend views from backend logic."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required
from sqlalchemy import func, or_

from extensions import db
from core.domains.cards.services.scryfall_cache import find_by_set_cn, normalize_color_identity, prints_for_oracle
from models import Card, Folder, FolderShare, UserFriend
from shared.auth import ensure_folder_access
from core.shared.database import get_or_404

api_bp = Blueprint("api", __name__, url_prefix="/api")


def _serialize_folder(folder: Folder, counts: Dict[str, int] | None = None) -> Dict[str, Any]:
    """Transform a Folder into a JSON-friendly dict."""
    counts = counts or {}
    return {
        "id": folder.id,
        "name": folder.name,
        "category": folder.category,
        "deck_tag": folder.deck_tag,
        "commander_name": folder.commander_name,
        "is_proxy": bool(folder.is_proxy),
        "is_public": bool(folder.is_public),
        "owner_user_id": folder.owner_user_id,
        "updated_at": folder.updated_at.isoformat() if isinstance(folder.updated_at, datetime) else None,
        "counts": {
            "unique": int(counts.get("unique") or 0),
            "total": int(counts.get("total") or 0),
        },
    }


def _print_for_card(
    card: Card,
    print_cache: Dict[tuple[str, str, str], Dict[str, Any]] | None = None,
    oracle_cache: Dict[str, Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    print_cache = print_cache if print_cache is not None else {}
    oracle_cache = oracle_cache if oracle_cache is not None else {}
    set_key = (card.set_code or "").strip().lower()
    cn_key = str(card.collector_number or "").strip().lower()
    name_key = (card.name or "").strip().lower()
    cache_key = (set_key, cn_key, name_key)
    if cache_key in print_cache:
        return print_cache[cache_key]

    payload: Dict[str, Any] = {}
    try:
        found = find_by_set_cn(card.set_code, card.collector_number, card.name)
    except Exception:
        found = None
    if isinstance(found, dict):
        payload = found
    elif card.oracle_id:
        oid = str(card.oracle_id).strip().lower()
        if oid in oracle_cache:
            payload = oracle_cache.get(oid) or {}
        else:
            try:
                prints = prints_for_oracle(card.oracle_id) or []
            except Exception:
                prints = []
            if prints:
                payload = next((p for p in prints if not p.get("digital")), prints[0]) or {}
            oracle_cache[oid] = payload

    print_cache[cache_key] = payload
    return payload


def _serialize_card(
    card: Card,
    print_cache: Dict[tuple[str, str, str], Dict[str, Any]] | None = None,
    oracle_cache: Dict[str, Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    """Serialize a card row for API responses."""
    pr = _print_for_card(card, print_cache=print_cache, oracle_cache=oracle_cache)
    type_line = (card.type_line or "").strip() or str(pr.get("type_line") or "").strip()
    if not type_line:
        faces = (pr or {}).get("card_faces") or []
        if faces:
            type_line = str((faces[0] or {}).get("type_line") or "").strip()

    rarity = (card.rarity or "").strip().lower() or str(pr.get("rarity") or "").strip().lower()
    raw_identity = (
        card.color_identity
        or card.colors
        or pr.get("color_identity")
        or pr.get("colors")
        or []
    )
    letters, derived_mask = normalize_color_identity(raw_identity)
    color_identity_mask = card.color_identity_mask
    if color_identity_mask is None:
        color_identity_mask = derived_mask

    return {
        "id": card.id,
        "name": card.name,
        "set_code": card.set_code,
        "collector_number": card.collector_number,
        "lang": card.lang,
        "quantity": card.quantity,
        "is_foil": bool(card.is_foil),
        "folder_id": card.folder_id,
        "oracle_id": card.oracle_id,
        "type_line": type_line or None,
        "rarity": rarity or None,
        "color_identity": letters or None,
        "color_identity_mask": color_identity_mask,
    }


def _counts_for_folder_ids(folder_ids: list[int]) -> Dict[int, Dict[str, int]]:
    """Precompute unique/quantity counts for folders to avoid per-row queries."""
    if not folder_ids:
        return {}
    rows = (
        db.session.query(
            Card.folder_id,
            func.count(Card.id).label("unique"),
            func.coalesce(func.sum(Card.quantity), 0).label("total"),
        )
        .filter(Card.folder_id.in_(folder_ids))
        .group_by(Card.folder_id)
        .all()
    )
    return {row.folder_id: {"unique": int(row.unique or 0), "total": int(row.total or 0)} for row in rows}


@api_bp.get("/me")
@login_required
def api_me():
    """Return the authenticated user's basic profile."""
    return jsonify(
        {
            "data": {
                "id": current_user.id,
                "username": getattr(current_user, "username", None),
                "email": getattr(current_user, "email", None),
                "is_admin": bool(getattr(current_user, "is_admin", False)),
            }
        }
    )


@api_bp.get("/folders")
@login_required
def api_folders():
    """List folders the current user can access (owner, shared, or public)."""
    friend_ids = [
        row[0]
        for row in db.session.query(UserFriend.friend_user_id)
        .filter(UserFriend.user_id == current_user.id)
        .all()
    ]
    access_filters = [
        Folder.owner_user_id == current_user.id,
        Folder.is_public.is_(True),
        Folder.shares.any(FolderShare.shared_user_id == current_user.id),
    ]
    if friend_ids:
        access_filters.append(Folder.owner_user_id.in_(friend_ids))
    accessible_folders = (
        Folder.query.filter(
            or_(*access_filters)
        )
        .order_by(func.lower(Folder.name))
        .all()
    )
    folder_ids = [f.id for f in accessible_folders if f.id is not None]
    counts_map = _counts_for_folder_ids(folder_ids)
    data = [_serialize_folder(f, counts_map.get(f.id, {})) for f in accessible_folders]
    return jsonify({"data": data})


@api_bp.get("/folders/<int:folder_id>")
@login_required
def api_folder_detail(folder_id: int):
    """Return metadata for a single folder."""
    folder = get_or_404(Folder, folder_id)
    ensure_folder_access(folder, write=False, allow_shared=True)
    counts = _counts_for_folder_ids([folder.id]).get(folder.id, {})
    return jsonify({"data": _serialize_folder(folder, counts)})


@api_bp.get("/folders/<int:folder_id>/cards")
@login_required
def api_folder_cards(folder_id: int):
    """Return paginated cards for a folder."""
    folder = get_or_404(Folder, folder_id)
    ensure_folder_access(folder, write=False, allow_shared=True)

    try:
        limit = int(request.args.get("limit", 200))
    except (TypeError, ValueError):
        limit = 200
    try:
        offset = int(request.args.get("offset", 0))
    except (TypeError, ValueError):
        offset = 0

    limit = max(1, min(limit, 500))
    offset = max(0, offset)

    base_query = Card.query.filter(Card.folder_id == folder.id).order_by(func.lower(Card.name), Card.id)
    total = base_query.count()
    cards = base_query.offset(offset).limit(limit).all()

    print_cache: Dict[tuple[str, str, str], Dict[str, Any]] = {}
    oracle_cache: Dict[str, Dict[str, Any]] = {}

    return jsonify(
        {
            "data": [_serialize_card(card, print_cache=print_cache, oracle_cache=oracle_cache) for card in cards],
            "pagination": {"total": total, "limit": limit, "offset": offset},
        }
    )


__all__ = ["api_bp"]
