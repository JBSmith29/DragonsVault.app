"""JSON API blueprint to decouple frontend views from backend logic."""

from __future__ import annotations

from typing import Any, Dict

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required
from sqlalchemy import func, or_

from extensions import db
from models import Card, Folder, FolderShare, UserFriend
from shared.api import serialize_card, serialize_folder
from shared.auth import ensure_folder_access
from core.shared.database import get_or_404

api_bp = Blueprint("api", __name__, url_prefix="/api")

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
    """List folders the current user can access."""
    friend_ids = [
        row[0]
        for row in db.session.query(UserFriend.friend_user_id)
        .filter(UserFriend.user_id == current_user.id)
        .all()
    ]
    access_filters = [
        Folder.owner_user_id == current_user.id,
        Folder.owner_user_id.is_(None),
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
    data = [serialize_folder(folder, counts_map.get(folder.id, {})) for folder in accessible_folders]
    return jsonify({"data": data})


@api_bp.get("/folders/<int:folder_id>")
@login_required
def api_folder_detail(folder_id: int):
    """Return metadata for a single folder."""
    folder = get_or_404(Folder, folder_id)
    ensure_folder_access(folder, write=False, allow_shared=True)
    counts = _counts_for_folder_ids([folder.id]).get(folder.id, {})
    return jsonify({"data": serialize_folder(folder, counts)})


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
            "data": [serialize_card(card, print_cache=print_cache, oracle_cache=oracle_cache) for card in cards],
            "pagination": {"total": total, "limit": limit, "offset": offset},
        }
    )


__all__ = ["api_bp"]
