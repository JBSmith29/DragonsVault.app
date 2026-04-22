"""
Shared helpers for Scryfall-normalized import, export, and manual-import flows.
Other services may read normalized data, but MUST NOT normalize Scryfall input elsewhere.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any

from flask import Response, current_app, jsonify, request
from flask_login import current_user
from sqlalchemy import func
from werkzeug.utils import secure_filename

from models import Folder
from core.domains.cards.services.scryfall_cache import ensure_cache_loaded, search_prints
from core.domains.decks.viewmodels.folder_vm import FolderExportVM

ALLOWED_IMPORT_EXTS = {".csv", ".xlsx", ".xls", ".xlsm"}
MAX_IMPORT_BYTES = int(os.getenv("IMPORT_MAX_BYTES", 10 * 1024 * 1024))  # 10MB default
IMPORT_UPLOAD_RETENTION_HOURS = int(os.getenv("IMPORT_UPLOAD_RETENTION_HOURS", 24))

_MANUAL_LINE_RE = re.compile(r"^\s*(\d+)\s*[xX]?\s+(.*)$")


def _store_import_notification(level: str, message: str, session_obj) -> None:
    session_obj["last_import_notification"] = {"level": level, "message": message}


def _normalize_quantity_mode(raw: str | None) -> str:
    value = (raw or "new_only").strip().lower()
    if value in {"absolute", "replace", "overwrite"}:
        return "absolute"
    if value in {"delta", "add", "increment"}:
        return "new_only"
    if value in {"new_only", "new", "add_new"}:
        return "new_only"
    if value in {"purge", "clear", "reset"}:
        return "purge"
    return "new_only"


def normalize_set_code(value: str | None) -> str:
    """Normalize a Scryfall set code for imports."""
    return (value or "").strip().lower()


def normalize_collector_number(value: Any) -> str:
    """Normalize a collector number for Scryfall lookups."""
    return str(value).strip()


def _parse_manual_card_list(raw: str) -> list[dict]:
    entries: list[dict] = []
    for line in (raw or "").splitlines():
        text = (line or "").strip()
        if not text:
            continue
        qty = 1
        name = text
        match = _MANUAL_LINE_RE.match(text)
        if match:
            try:
                qty = max(int(match.group(1)), 1)
            except (TypeError, ValueError):
                qty = 1
            name = match.group(2).strip()
        if not name:
            continue
        entries.append({"name": name, "quantity": qty})
    return entries


def _printing_options_for_name(name: str, limit: int = 12) -> list[dict]:
    ensure_cache_loaded()
    prints, _ = search_prints(name_q=name, limit=limit)
    options: list[dict] = []
    for pr in prints or []:
        set_code = (pr.get("set") or "").upper()
        cn = pr.get("collector_number") or ""
        lang = (pr.get("lang") or "en").upper()
        value = f"{set_code}::{cn}::{lang}"
        finishes = pr.get("finishes") or []
        options.append(
            {
                "value": value,
                "set_code": set_code,
                "collector_number": cn,
                "lang": lang,
                "set_name": pr.get("set_name") or "",
                "rarity": (pr.get("rarity") or "").title(),
                "released_at": pr.get("released_at") or "",
                "finishes": finishes or ["nonfoil"],
                "name": pr.get("name") or name,
            }
        )
    if not options:
        options.append(
            {
                "value": f"UNK::{name}::EN",
                "set_code": "UNK",
                "collector_number": name,
                "lang": "EN",
                "set_name": "Custom",
                "rarity": "",
                "released_at": "",
                "finishes": ["nonfoil", "foil"],
                "name": name,
            }
        )
    return options


def _uploads_dir() -> Path:
    path = Path(current_app.instance_path) / "uploads"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _prune_old_uploads(max_age_hours: int) -> None:
    if max_age_hours <= 0:
        return
    uploads_root = _uploads_dir()
    cutoff = time.time() - (max_age_hours * 3600)
    try:
        for entry in uploads_root.iterdir():
            try:
                if not entry.is_file():
                    continue
                if entry.stat().st_mtime < cutoff:
                    entry.unlink(missing_ok=True)
            except Exception:
                continue
    except Exception:
        current_app.logger.debug("Unable to prune old import uploads.", exc_info=True)


def _validate_upload_path(filepath: str) -> str:
    """Ensure the path exists and lives under the uploads directory."""
    uploads_root = _uploads_dir().resolve()
    resolved = Path(filepath).resolve()
    if not resolved.exists():
        raise ValueError("Uploaded file not found. Please re-upload.")
    try:
        if uploads_root not in resolved.parents and resolved != uploads_root:
            raise ValueError("Invalid upload path.")
    except Exception:
        raise ValueError("Invalid upload path.")
    return str(resolved)


def _save_upload_if_present(file) -> str | None:
    """Return saved path or None if no file provided."""
    if not file or not getattr(file, "filename", ""):
        return None
    _prune_old_uploads(IMPORT_UPLOAD_RETENTION_HOURS)
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_IMPORT_EXTS:
        raise ValueError("Unsupported file type. Please upload a CSV or Excel file (.csv, .xlsx, .xls, .xlsm).")
    fname = secure_filename(file.filename) or f"import{ext}"
    ts = int(time.time())
    dest_path = _uploads_dir() / f"{ts}-{fname}"

    if getattr(file, "content_length", None) and int(file.content_length or 0) > MAX_IMPORT_BYTES:
        raise ValueError(
            f"File is too large. Limit: {MAX_IMPORT_BYTES // (1024 * 1024)} MB."
        )

    file.save(dest_path)
    try:
        if dest_path.stat().st_size > MAX_IMPORT_BYTES:
            dest_path.unlink(missing_ok=True)
            raise ValueError(
                f"File is too large. Limit: {MAX_IMPORT_BYTES // (1024 * 1024)} MB."
            )
    except ValueError:
        raise
    except Exception:
        try:
            dest_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise ValueError("Unable to save upload. Please try again.")

    return str(dest_path)


def _export_context(session_obj) -> dict:
    """Builds folder and format metadata for the import/export UI."""
    user_id = None
    if current_user.is_authenticated:
        try:
            user_id = int(current_user.get_id())
        except (TypeError, ValueError, Exception):
            raw_id = session_obj.get("_user_id")
            try:
                user_id = int(raw_id) if raw_id is not None else None
            except (TypeError, ValueError):
                user_id = None

    folder_category_labels = {
        Folder.CATEGORY_DECK: "Deck",
        Folder.CATEGORY_COLLECTION: "Collection",
    }
    if user_id is None:
        user_folders = []
    else:
        user_folders = (
            Folder.query.filter(Folder.owner_user_id == user_id)
            .order_by(func.lower(Folder.name))
            .all()
        )
    folder_export_options = [
        FolderExportVM(
            id=folder.id,
            name=folder.name,
            category=folder.category or Folder.CATEGORY_DECK,
            category_label=folder_category_labels.get(folder.category or Folder.CATEGORY_DECK, "Deck"),
            is_proxy=bool(folder.is_proxy),
            is_public=bool(folder.is_public),
            is_deck=bool(folder.is_deck),
            is_collection=bool(folder.is_collection),
        )
        for folder in user_folders
    ]
    export_format_options = [
        {
            "value": "",
            "label": "Standard CSV",
            "description": "Includes folder name, quantity, set code, collector number, language, and foil status.",
        },
        {
            "value": "manavault",
            "label": "ManaVault",
            "description": "Matches ManaVault's CSV headers (Count, Name, Edition, Collector Number, Language, Finish).",
        },
        {
            "value": "manabox",
            "label": "ManaBox",
            "description": "Generates the ManaBox binder layout with count, name, edition, number, and finish.",
        },
        {
            "value": "dragonshield",
            "label": "Dragon Shield",
            "description": "Exports Dragon Shield-style rows with quantity, printing, condition, and language.",
        },
    ]
    return {
        "folder_export_options": folder_export_options,
        "export_format_options": export_format_options,
    }


def _base_import_context(session_obj, *, quantity_mode: str, notification=None, **extra) -> dict:
    context = {
        "quantity_mode": quantity_mode,
        "notification": notification,
        "deck_category": Folder.CATEGORY_DECK,
        "collection_category": Folder.CATEGORY_COLLECTION,
        "disable_hx": True,
        "field_errors": {},
        "form_errors": [],
        **_export_context(session_obj),
    }
    context.update(extra)
    return context


def _json_error(message: str, *, field_errors: dict[str, str] | None = None, status: int = 400) -> Response:
    payload = {"ok": False, "error": message}
    if field_errors:
        payload["field_errors"] = field_errors
        payload["errors"] = list(field_errors.values())
    resp = jsonify(payload)
    resp.status_code = status
    return resp


def _wants_json_response() -> bool:
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return True
    if request.is_json:
        return True
    accept = request.accept_mimetypes
    return accept["application/json"] > accept["text/html"]


__all__ = [
    "ALLOWED_IMPORT_EXTS",
    "IMPORT_UPLOAD_RETENTION_HOURS",
    "MAX_IMPORT_BYTES",
    "_base_import_context",
    "_export_context",
    "_json_error",
    "_normalize_quantity_mode",
    "_parse_manual_card_list",
    "_printing_options_for_name",
    "_prune_old_uploads",
    "_save_upload_if_present",
    "_store_import_notification",
    "_uploads_dir",
    "_validate_upload_path",
    "_wants_json_response",
    "normalize_collector_number",
    "normalize_set_code",
]
