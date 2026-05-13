"""API endpoints for the deck legality checker.

Routes
------
``GET /api/legality/formats``
    Returns the list of formats DragonsVault knows how to validate.

``GET /api/folders/<folder_id>/legality``
    Evaluates a deck against a single format. ``?format=commander`` selects
    the format; it defaults to ``commander``. The caller must have read
    access to the folder (ownership, share, or friend).

``GET /api/folders/<folder_id>/legality/all``
    Evaluates the deck against every supported format in one response. Useful
    for dashboards that show a matrix of verdicts.
"""

from __future__ import annotations

from flask import jsonify, request
from flask_login import login_required

from models import Folder
from core.domains.decks.services.legality_service import (
    SUPPORTED_FORMATS,
    available_formats,
    evaluate_folder_legality,
)
from core.routes.api import api_bp
from shared.auth import ensure_folder_access
from shared.database import get_or_404


__all__ = [
    "api_legality_formats",
    "api_folder_legality",
    "api_folder_legality_all",
]


@api_bp.get("/legality/formats")
@login_required
def api_legality_formats():
    """Enumerate the formats supported by the legality checker."""
    return jsonify({"data": available_formats()})


@api_bp.get("/folders/<int:folder_id>/legality")
@login_required
def api_folder_legality(folder_id: int):
    """Evaluate a single folder against a single format."""
    folder = get_or_404(Folder, folder_id)
    ensure_folder_access(folder, write=False, allow_shared=True)

    format_key = (request.args.get("format") or "commander").strip().lower()
    try:
        report = evaluate_folder_legality(folder, format_key)
    except ValueError as exc:
        return jsonify({"error": "unsupported_format", "detail": str(exc)}), 400
    return jsonify({"data": report.to_dict()})


@api_bp.get("/folders/<int:folder_id>/legality/all")
@login_required
def api_folder_legality_all(folder_id: int):
    """Evaluate a single folder against every supported format."""
    folder = get_or_404(Folder, folder_id)
    ensure_folder_access(folder, write=False, allow_shared=True)

    reports = []
    for fmt in SUPPORTED_FORMATS:
        reports.append(evaluate_folder_legality(folder, fmt.key).to_dict())
    return jsonify({"data": reports})
