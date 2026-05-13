"""Deck comparison API."""

from __future__ import annotations

from flask import jsonify, request
from flask_login import login_required
from sqlalchemy.orm import selectinload

from extensions import db
from models import Folder
from core.domains.decks.services.deck_compare_service import compare_folders
from core.routes.api import api_bp
from shared.auth import ensure_folder_access
from shared.database import get_or_404


__all__ = ["api_deck_compare"]


def _load_folder(folder_id: int) -> Folder:
    folder = get_or_404(Folder, folder_id)
    ensure_folder_access(folder, write=False, allow_shared=True)
    # Refetch with eager-loaded cards to avoid lazy queries.
    return (
        db.session.query(Folder)
        .options(selectinload(Folder.cards))
        .filter(Folder.id == folder_id)
        .one()
    )


@api_bp.get("/decks/compare")
@login_required
def api_deck_compare():
    """Compare two folders passed via ``left`` and ``right`` query params."""
    try:
        left_id = int(request.args.get("left") or 0)
        right_id = int(request.args.get("right") or 0)
    except (TypeError, ValueError):
        return jsonify({"error": "invalid_ids"}), 400
    if left_id <= 0 or right_id <= 0:
        return jsonify({"error": "invalid_ids"}), 400
    if left_id == right_id:
        return jsonify({"error": "same_deck"}), 400

    left = _load_folder(left_id)
    right = _load_folder(right_id)
    result = compare_folders(left, right)
    return jsonify({"data": result.to_dict()})
