"""Folder-category updates used by import flows."""

from __future__ import annotations

from flask import Response, jsonify, request
from flask_login import current_user

from models import Folder
from shared.database import safe_commit as _safe_commit


def api_update_folder_categories() -> Response:
    """Update folder categories for the current user (used post-import)."""
    payload = request.get_json(silent=True) or {}
    entries = payload.get("folders") or []
    if not isinstance(entries, list):
        return jsonify({"ok": False, "error": "Invalid payload"}), 400

    allowed = {Folder.CATEGORY_DECK, Folder.CATEGORY_COLLECTION}
    updated = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        fid = entry.get("id")
        cat = (entry.get("category") or "").strip().lower()
        if not fid or cat not in allowed:
            continue
        try:
            fid_int = int(fid)
        except (TypeError, ValueError):
            continue
        folder = Folder.query.filter(
            Folder.id == fid_int,
            Folder.owner_user_id == current_user.id,
        ).first()
        if not folder:
            continue
        if folder.category != cat:
            folder.set_primary_role(cat)
            updated += 1
    _safe_commit()
    return jsonify({"ok": True, "updated": updated})


__all__ = ["api_update_folder_categories"]
