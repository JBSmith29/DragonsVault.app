"""Budget alternative suggestions API."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from flask import jsonify, request
from flask_login import current_user, login_required

from models import Folder
from core.domains.decks.services.budget_alternatives_service import (
    DEFAULT_EXPENSIVE_THRESHOLD,
    find_budget_alternatives,
)
from core.routes.api import api_bp
from shared.auth import ensure_folder_access
from shared.database import get_or_404


__all__ = ["api_folder_budget_alternatives"]


@api_bp.get("/folders/<int:folder_id>/budget-alternatives")
@login_required
def api_folder_budget_alternatives(folder_id: int):
    folder = get_or_404(Folder, folder_id)
    ensure_folder_access(folder, write=False, allow_shared=True)

    raw_threshold = request.args.get("threshold")
    try:
        threshold = Decimal(raw_threshold) if raw_threshold else DEFAULT_EXPENSIVE_THRESHOLD
    except (InvalidOperation, TypeError):
        return jsonify({"error": "invalid_threshold"}), 400

    try:
        suggestions_per_card = int(request.args.get("per_card") or 5)
    except (TypeError, ValueError):
        suggestions_per_card = 5

    try:
        report = find_budget_alternatives(
            user_id=current_user.id,
            folder=folder,
            threshold_usd=threshold,
            suggestions_per_card=suggestions_per_card,
        )
    except ValueError as exc:
        return jsonify({"error": "invalid_input", "detail": str(exc)}), 400
    return jsonify({"data": report.to_dict()})
