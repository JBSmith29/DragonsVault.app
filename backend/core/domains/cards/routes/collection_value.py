"""HTTP endpoints for the collection-value dashboard."""

from __future__ import annotations

from flask import jsonify, request
from flask_login import current_user, login_required

from extensions import db
from core.domains.cards.services import collection_value_service
from core.routes.api import api_bp


__all__ = [
    "api_collection_value",
    "api_collection_value_capture",
    "api_collection_value_history",
    "api_collection_value_trend",
]


def _parse_int(arg: str | None, default: int | None = None) -> int | None:
    if arg is None or arg == "":
        return default
    try:
        return int(arg)
    except (TypeError, ValueError):
        return default


def _parse_folder_id() -> int | None:
    raw = request.args.get("folder_id")
    if raw in (None, "", "all"):
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


@api_bp.get("/collection/value")
@login_required
def api_collection_value():
    """Return a live valuation for the current user's collection."""
    currency = (request.args.get("currency") or "usd").strip().lower()
    folder_id = _parse_folder_id()
    top_n = _parse_int(request.args.get("top_n"), default=10) or 0
    try:
        report = collection_value_service.compute_valuation(
            user_id=current_user.id,
            folder_id=folder_id,
            currency=currency,
            top_n=top_n,
        )
    except ValueError as exc:
        return jsonify({"error": "invalid_currency", "detail": str(exc)}), 400
    return jsonify({"data": report.to_dict()})


@api_bp.post("/collection/value/snapshots")
@login_required
def api_collection_value_capture():
    """Persist a new snapshot and return its serialized form."""
    payload = request.get_json(silent=True) or {}
    currency = str(payload.get("currency") or "usd").strip().lower()
    folder_id = payload.get("folder_id")
    if folder_id in ("all", "", None):
        folder_id = None
    try:
        folder_id = int(folder_id) if folder_id is not None else None
    except (TypeError, ValueError):
        return jsonify({"error": "invalid_folder"}), 400

    try:
        snapshot = collection_value_service.capture_snapshot(
            user_id=current_user.id,
            folder_id=folder_id,
            currency=currency,
            source=str(payload.get("source") or "manual"),
            top_n=int(payload.get("top_n") or 20),
        )
    except ValueError as exc:
        return jsonify({"error": "invalid_currency", "detail": str(exc)}), 400

    db.session.commit()
    return jsonify({"data": collection_value_service._snapshot_to_dict(snapshot)}), 201


@api_bp.get("/collection/value/history")
@login_required
def api_collection_value_history():
    """Return historical snapshot rows for chart rendering."""
    currency = (request.args.get("currency") or "usd").strip().lower()
    folder_id = _parse_folder_id()
    days = _parse_int(request.args.get("days"), default=None)
    limit = _parse_int(request.args.get("limit"), default=None)
    try:
        rows = collection_value_service.history(
            user_id=current_user.id,
            folder_id=folder_id,
            currency=currency,
            days=days,
            limit=limit,
        )
    except ValueError as exc:
        return jsonify({"error": "invalid_currency", "detail": str(exc)}), 400
    return jsonify({"data": rows})


@api_bp.get("/collection/value/trend")
@login_required
def api_collection_value_trend():
    """Return a delta summary comparing the current valuation to a past point."""
    currency = (request.args.get("currency") or "usd").strip().lower()
    folder_id = _parse_folder_id()
    days = _parse_int(request.args.get("days"), default=30) or 30
    try:
        return jsonify(
            {
                "data": collection_value_service.compare_periods(
                    user_id=current_user.id,
                    folder_id=folder_id,
                    currency=currency,
                    days=days,
                )
            }
        )
    except ValueError as exc:
        return jsonify({"error": "invalid_currency", "detail": str(exc)}), 400
