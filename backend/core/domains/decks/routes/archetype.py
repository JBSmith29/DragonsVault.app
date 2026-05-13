"""Deck archetype classification API."""

from __future__ import annotations

from flask import jsonify
from flask_login import login_required

from models import Folder
from core.domains.decks.services.deck_archetype_service import classify_deck
from core.routes.api import api_bp
from shared.auth import ensure_folder_access
from shared.database import get_or_404


__all__ = ["api_folder_archetype"]


@api_bp.get("/folders/<int:folder_id>/archetype")
@login_required
def api_folder_archetype(folder_id: int):
    folder = get_or_404(Folder, folder_id)
    ensure_folder_access(folder, write=False, allow_shared=True)
    report = classify_deck(folder)
    return jsonify({"data": report.to_dict()})
