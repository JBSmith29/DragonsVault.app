"""Commander assignment endpoints for deck folders."""

from __future__ import annotations

from typing import List, Optional, Sequence

from flask import abort, flash, jsonify, redirect, request, url_for
from flask_login import current_user

from extensions import cache
from models import Card, Folder
from core.domains.cards.services.scryfall_cache import find_by_set_cn
from core.domains.decks.services.commander_utils import (
    CommanderSlot,
    MAX_COMMANDERS,
    merge_slots,
    slots_from_blobs,
    slots_from_payload,
)
from shared.auth import ensure_folder_access
from shared.database import get_or_404, safe_commit as _safe_commit
from shared.mtg import _commander_candidates_for_folder
from shared.validation import ValidationError, log_validation_error, parse_positive_int


def _clear_deck_metadata_wizard_cache() -> None:
    user_id = getattr(current_user, "id", None)
    if not user_id:
        return
    try:
        cache.delete(f"deck_wizard:{user_id}")
    except Exception:
        pass


def _commander_slots(folder: Folder) -> List[CommanderSlot]:
    return slots_from_blobs(folder.commander_name, folder.commander_oracle_id)


def _slot_from_values(name: Optional[str], oracle_id: Optional[str]) -> CommanderSlot | None:
    cleaned_name = (name or "").strip()
    cleaned_id = (oracle_id or "").strip()
    if not cleaned_name and not cleaned_id:
        return None
    return CommanderSlot(name=cleaned_name or None, oracle_id=cleaned_id or None)


def _apply_commander_update(
    folder: Folder,
    new_slots: Sequence[CommanderSlot],
    *,
    mode: str = "replace",
) -> tuple[bool, Optional[str]]:
    normalized_mode = "append" if (mode or "").strip().lower() == "append" else "replace"
    existing_slots = _commander_slots(folder)
    active_existing = [slot for slot in existing_slots if slot.name or slot.oracle_id]
    if normalized_mode == "append" and len(active_existing) >= MAX_COMMANDERS:
        return False, f"Up to {MAX_COMMANDERS} commanders can be assigned to a deck."
    name_blob, oracle_blob, normalized = merge_slots(
        existing_slots,
        new_slots,
        mode=normalized_mode,
        limit=MAX_COMMANDERS,
    )
    if not normalized:
        return False, "Commander details are missing."
    folder.commander_name = name_blob
    folder.commander_oracle_id = oracle_blob
    return True, None


def api_folder_commander_candidates(folder_id: int):
    folder = get_or_404(Folder, folder_id)
    ensure_folder_access(folder, write=False)
    if folder.is_collection:
        message = "Commander can only be set for deck folders."
        return jsonify({"ok": False, "error": message}), 400

    candidates = _commander_candidates_for_folder(folder_id)
    return jsonify(
        {
            "ok": True,
            "folder": {
                "id": folder.id,
                "name": folder.name,
                "commander_name": folder.commander_name,
            },
            "candidates": candidates,
        }
    )


def set_folder_commander(folder_id: int):
    folder = get_or_404(Folder, folder_id)
    ensure_folder_access(folder, write=True)
    if folder.is_collection:
        flash("Commander can only be set for deck folders.", "warning")
        return redirect(url_for("views.folder_detail", folder_id=folder_id))

    oid = (request.form.get("oracle_id") or "").strip()
    name = (request.form.get("name") or "").strip() or None
    mode = (request.form.get("mode") or "replace").strip().lower()
    slot = _slot_from_values(name, oid)
    if not slot:
        flash("Missing commander name.", "danger")
        return redirect(url_for("views.folder_detail", folder_id=folder_id))

    updated, error = _apply_commander_update(folder, [slot], mode=mode)
    if not updated:
        flash(error or "Unable to update commander.", "danger")
        return redirect(url_for("views.folder_detail", folder_id=folder_id))

    _safe_commit()
    final_label = folder.commander_name or slot.name or "Commander"
    flash(f'Set commander for "{folder.name}" to {final_label}.', "success")
    return redirect(request.referrer or url_for("views.folder_detail", folder_id=folder_id))


def clear_folder_commander(folder_id: int):
    folder = get_or_404(Folder, folder_id)
    ensure_folder_access(folder, write=True)
    if folder.is_collection:
        flash("Commander can only be cleared on deck folders.", "warning")
        return redirect(url_for("views.folder_detail", folder_id=folder_id))

    folder.commander_oracle_id = None
    folder.commander_name = None
    _safe_commit()
    _clear_deck_metadata_wizard_cache()
    flash(f'Cleared commander for "{folder.name}".', "success")
    return redirect(request.referrer or url_for("views.folder_detail", folder_id=folder_id))


def set_commander(folder_id):
    folder = get_or_404(Folder, folder_id)
    ensure_folder_access(folder, write=True)
    if folder.is_collection:
        message = "Commander can only be set for deck folders."
        if request.is_json:
            return jsonify({"ok": False, "error": message}), 400
        flash(message, "warning")
        return redirect(url_for("views.folder_detail", folder_id=folder_id))

    data = request.get_json(silent=True) or {}
    card_id = data.get("card_id") or request.form.get("card_id")
    payload_mode = data.get("mode") or request.form.get("mode")
    mode = (payload_mode or "replace").strip().lower()
    commanders_payload = data.get("commanders") if isinstance(data.get("commanders"), list) else None

    resolved_name = data.get("name") or request.form.get("name")
    resolved_oracle_id = data.get("oracle_id") or request.form.get("oracle_id")

    if card_id:
        try:
            card_id_val = parse_positive_int(card_id, field="card id")
        except ValidationError as exc:
            log_validation_error(exc, context="set_commander")
            message = "Invalid card id."
            if request.is_json:
                return jsonify({"ok": False, "error": message}), 400
            flash(message, "warning")
            return redirect(url_for("views.folder_detail", folder_id=folder_id))
        card = Card.query.filter_by(id=card_id_val, folder_id=folder.id).first()
        if not card:
            if request.is_json:
                return jsonify({"ok": False, "error": "Card not found in this deck"}), 404
            abort(404)
        resolved_name = card.name
        if not resolved_oracle_id:
            resolved_oracle_id = card.oracle_id
            if not resolved_oracle_id:
                try:
                    found = find_by_set_cn(card.set_code, card.collector_number, card.name)
                    if found:
                        resolved_oracle_id = found.get("oracle_id")
                except Exception:
                    pass

    slots: List[CommanderSlot] = []
    if commanders_payload is not None:
        slots = slots_from_payload(commanders_payload)
    else:
        slot = _slot_from_values(resolved_name, resolved_oracle_id)
        if slot:
            slots = [slot]

    if not slots:
        error_message = "Missing commander details."
        if request.is_json:
            return jsonify({"ok": False, "error": error_message}), 400
        flash(error_message, "danger")
        return redirect(request.referrer or url_for("views.folder_detail", folder_id=folder_id))

    updated, error = _apply_commander_update(folder, slots, mode=mode)
    if not updated:
        if request.is_json:
            return jsonify({"ok": False, "error": error or "Unable to update commander."}), 400
        flash(error or "Unable to update commander.", "danger")
        return redirect(request.referrer or url_for("views.folder_detail", folder_id=folder_id))

    _safe_commit()
    _clear_deck_metadata_wizard_cache()

    if request.is_json:
        return jsonify({"ok": True, "name": folder.commander_name})
    final_label = folder.commander_name or resolved_name or "Commander"
    flash(f"Commander set to {final_label}", "success")
    return redirect(request.referrer or url_for("views.folder_detail", folder_id=folder_id))


def clear_commander(folder_id):
    folder = get_or_404(Folder, folder_id)
    ensure_folder_access(folder, write=True)
    if folder.is_collection:
        message = "Commander can only be cleared on deck folders."
        if request.is_json:
            return jsonify({"ok": False, "error": message}), 400
        flash(message, "warning")
        return redirect(url_for("views.folder_detail", folder_id=folder_id))

    folder.commander_name = None
    folder.commander_oracle_id = None
    _safe_commit()
    _clear_deck_metadata_wizard_cache()
    if request.is_json:
        return jsonify({"ok": True})
    flash("Commander cleared.", "info")
    return redirect(request.referrer or url_for("views.folder_detail", folder_id=folder_id))


__all__ = [
    "api_folder_commander_candidates",
    "clear_commander",
    "clear_folder_commander",
    "set_commander",
    "set_folder_commander",
]
