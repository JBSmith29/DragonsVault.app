"""Mana base analysis API."""

from __future__ import annotations

from flask import jsonify
from flask_login import login_required

from models import Folder
from core.domains.decks.services.mana_base_analysis_service import analyze_mana_base
from core.routes.api import api_bp
from shared.auth import ensure_folder_access
from shared.database import get_or_404


__all__ = ["api_folder_mana_base"]


@api_bp.get("/folders/<int:folder_id>/mana-base")
@login_required
def api_folder_mana_base(folder_id: int):
    folder = get_or_404(Folder, folder_id)
    ensure_folder_access(folder, write=False, allow_shared=True)
    report = analyze_mana_base(folder)
    return jsonify({"data": report.to_dict()})
