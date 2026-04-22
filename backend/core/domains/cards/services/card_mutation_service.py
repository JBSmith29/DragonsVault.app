"""Card mutation services: bulk moves, deletes, and printing updates."""

from __future__ import annotations

from typing import Any

from flask import flash, jsonify, redirect, request, url_for
from sqlalchemy import func

from extensions import db
from models import Card, Folder
from core.domains.cards.services import scryfall_cache as sc
from core.domains.cards.services.scryfall_cache import (
    cache_ready,
    ensure_cache_loaded,
    find_by_set_cn,
    metadata_from_print,
    prints_for_oracle,
    set_name_for_code,
    unique_oracle_by_name,
)
from core.domains.users.services.audit import record_audit_event
from shared.auth import ensure_folder_access
from shared.database import get_or_404, safe_commit as _safe_commit
from shared.mtg import _lookup_print_data
from shared.validation import (
    ValidationError,
    log_validation_error,
    parse_positive_int,
    parse_positive_int_list,
)


def _ensure_cache_ready() -> bool:
    return cache_ready() or ensure_cache_loaded()


def _image_from_print(print_obj: dict | None) -> dict:
    if not print_obj:
        return {"small": None, "normal": None, "large": None}
    imgs = sc.image_for_print(print_obj) or {}
    faces = print_obj.get("card_faces") or []
    if not imgs.get("small") and faces:
        face_imgs = (faces[0] or {}).get("image_uris") or {}
        imgs.setdefault("small", face_imgs.get("small"))
        imgs.setdefault("normal", face_imgs.get("normal"))
        imgs.setdefault("large", face_imgs.get("large"))
    return {
        "small": imgs.get("small"),
        "normal": imgs.get("normal"),
        "large": imgs.get("large"),
    }


def _clone_metadata_for_card(card: Card) -> dict[str, Any]:
    if card.type_line and card.rarity and card.color_identity and card.color_identity_mask is not None:
        return {}
    try:
        clone_print = _lookup_print_data(card.set_code, card.collector_number, card.name, card.oracle_id)
    except Exception:
        clone_print = None
    if not clone_print:
        return {}
    return metadata_from_print(clone_print) or {}


def bulk_move_cards():
    """Move multiple cards to another folder."""
    json_payload = request.get_json(silent=True) or {}
    wants_json = request.is_json or bool(json_payload) or "application/json" in (request.headers.get("Accept") or "")

    redirect_target = (
        request.form.get("redirect_to")
        or json_payload.get("redirect_to")
        or request.referrer
        or url_for("views.list_cards")
    )
    if redirect_target and not redirect_target.startswith("/"):
        redirect_target = url_for("views.list_cards")

    def _gather_raw_ids() -> list[str]:
        raw: list[str] = []

        def _extend(value):
            if value is None:
                return
            if isinstance(value, (list, tuple, set)):
                for item in value:
                    _extend(item)
            else:
                raw.append(str(value))

        _extend(json_payload.get("card_ids") or json_payload.get("cardIds"))
        if not raw:
            _extend(request.form.getlist("card_ids"))
            _extend(request.form.getlist("card_ids[]"))
        if not raw:
            single = request.form.get("card_ids")
            if single:
                raw.append(single)
        return raw

    try:
        card_ids = parse_positive_int_list(_gather_raw_ids(), field="card id(s)")
    except ValidationError as exc:
        log_validation_error(exc, context="bulk_move_cards")
        message = "Invalid card id(s) supplied."
        if wants_json:
            return jsonify({"success": False, "message": message}), 400
        flash(message, "warning")
        return redirect(redirect_target)
    if not card_ids:
        if wants_json:
            return jsonify({"success": False, "message": "Select at least one card to move."}), 400
        flash("Select at least one card to move.", "warning")
        return redirect(redirect_target)

    target_raw = (
        json_payload.get("target_folder_id")
        or json_payload.get("targetFolderId")
        or request.form.get("target_folder_id")
    )
    try:
        target_id = parse_positive_int(target_raw, field="target folder id")
    except ValidationError as exc:
        log_validation_error(exc, context="bulk_move_cards")
        if wants_json:
            return jsonify({"success": False, "message": "Choose a destination folder."}), 400
        flash("Choose a destination folder.", "warning")
        return redirect(redirect_target)

    target_folder = db.session.get(Folder, target_id)
    if not target_folder:
        if wants_json:
            return jsonify({"success": False, "message": "Destination folder was not found."}), 404
        flash("Destination folder was not found.", "danger")
        return redirect(redirect_target)

    ensure_folder_access(target_folder, write=True)

    cards = Card.query.filter(Card.id.in_(card_ids)).all()
    if not cards:
        if wants_json:
            return jsonify({"success": False, "message": "No matching cards were found."}), 404
        flash("No matching cards were found.", "warning")
        return redirect(redirect_target)

    single_qty = None
    raw_qty = json_payload.get("quantity") or request.form.get("quantity")
    if len(card_ids) == 1 and raw_qty is not None:
        try:
            single_qty = max(int(raw_qty), 1)
        except (TypeError, ValueError):
            single_qty = None

    moved = 0
    merged = 0
    skipped = 0
    for card in cards:
        ensure_folder_access(card.folder, write=True)
        if card.folder_id == target_folder.id:
            skipped += 1
            continue

        move_qty = single_qty if single_qty is not None else card.quantity or 0
        if move_qty <= 0:
            skipped += 1
            continue
        if move_qty > (card.quantity or 0):
            move_qty = card.quantity or move_qty

        existing = (
            Card.query.filter(
                Card.folder_id == target_folder.id,
                Card.name == card.name,
                Card.set_code == card.set_code,
                Card.collector_number == card.collector_number,
                Card.lang == card.lang,
                Card.is_foil == card.is_foil,
            )
            .order_by(Card.id.asc())
            .first()
        )

        remaining = (card.quantity or 0) - move_qty
        if remaining <= 0:
            if existing:
                existing.quantity = (existing.quantity or 0) + move_qty
                merged += move_qty
                db.session.delete(card)
            else:
                card.folder_id = target_folder.id
                moved += move_qty
        else:
            card.quantity = remaining
            if existing:
                existing.quantity = (existing.quantity or 0) + move_qty
                merged += move_qty
            else:
                clone_metadata = _clone_metadata_for_card(card)
                clone_type_line = card.type_line or clone_metadata.get("type_line")
                clone_rarity = card.rarity or clone_metadata.get("rarity")
                clone_oracle_text = card.oracle_text or clone_metadata.get("oracle_text")
                clone_mana_value = card.mana_value if card.mana_value is not None else clone_metadata.get("mana_value")
                clone_colors = card.colors or clone_metadata.get("colors")
                clone_color_identity = card.color_identity or clone_metadata.get("color_identity")
                clone_color_identity_mask = card.color_identity_mask
                if clone_color_identity_mask is None:
                    clone_color_identity_mask = clone_metadata.get("color_identity_mask")
                clone = Card(
                    name=card.name,
                    set_code=card.set_code,
                    collector_number=card.collector_number,
                    folder_id=target_folder.id,
                    quantity=move_qty,
                    oracle_id=card.oracle_id,
                    lang=card.lang,
                    is_foil=card.is_foil,
                    type_line=clone_type_line,
                    rarity=clone_rarity,
                    oracle_text=clone_oracle_text,
                    mana_value=clone_mana_value,
                    colors=clone_colors,
                    color_identity=clone_color_identity,
                    color_identity_mask=clone_color_identity_mask,
                    layout=card.layout,
                    faces_json=card.faces_json,
                )
                db.session.add(clone)
                moved += move_qty

    total_changed = (moved or 0) + (merged or 0)
    if total_changed:
        _safe_commit()
        record_audit_event(
            "cards_bulk_move",
            {"target_folder": target_folder.id, "moved_qty": moved, "merged_qty": merged, "card_ids": card_ids[:50]},
        )
        folder_name = target_folder.name or f"Folder {target_folder.id}"
        message = f"Moved {total_changed} card{'s' if total_changed != 1 else ''} to {folder_name}."
        if wants_json:
            return jsonify({"success": True, "message": message, "moved": moved, "merged": merged})
        flash(message, "success")
    else:
        info_msg = "Selected cards are already in that folder."
        if wants_json:
            return jsonify({"success": False, "message": info_msg, "skipped": skipped}), 200
        flash(info_msg, "info")

    return redirect(redirect_target)


def bulk_delete_cards(folder_id: int):
    """Delete one or more cards from a folder (deck or collection)."""
    folder = get_or_404(Folder, folder_id)
    ensure_folder_access(folder, write=True)

    json_payload = request.get_json(silent=True) or {}
    wants_json = request.is_json or bool(json_payload) or "application/json" in (request.headers.get("Accept") or "")

    redirect_target = (
        request.form.get("redirect_to")
        or request.referrer
        or url_for("views.folder_detail", folder_id=folder_id)
    )

    def _gather_raw_ids() -> list[str]:
        raw: list[str] = []

        def _extend(value):
            if value is None:
                return
            if isinstance(value, (list, tuple, set)):
                for item in value:
                    _extend(item)
            else:
                raw.append(str(value))

        _extend(json_payload.get("card_ids") or json_payload.get("cardIds"))
        if not raw:
            _extend(request.form.getlist("card_ids"))
            _extend(request.form.getlist("card_ids[]"))
        if not raw:
            single = request.form.get("card_id")
            if single:
                raw.append(single)
        return raw

    try:
        card_ids = parse_positive_int_list(_gather_raw_ids(), field="card id(s)")
    except ValidationError as exc:
        log_validation_error(exc, context="bulk_delete_cards")
        message = "Invalid card id(s) supplied."
        if wants_json:
            return jsonify({"success": False, "message": message}), 400
        flash(message, "warning")
        return redirect(redirect_target)
    if not card_ids:
        message = "Select at least one card to delete."
        if wants_json:
            return jsonify({"success": False, "message": message}), 400
        flash(message, "warning")
        return redirect(redirect_target)

    cards = (
        Card.query.filter(Card.id.in_(card_ids), Card.folder_id == folder.id)
        .order_by(Card.id.asc())
        .all()
    )
    if not cards:
        message = "No matching cards were found in this folder."
        if wants_json:
            return jsonify({"success": False, "message": message}), 404
        flash(message, "warning")
        return redirect(redirect_target)

    deleted_qty = 0
    for card in cards:
        deleted_qty += card.quantity or 0
        db.session.delete(card)

    _safe_commit()
    record_audit_event(
        "cards_bulk_delete",
        {"folder": folder.id, "card_ids": card_ids[:50], "qty": deleted_qty},
    )

    message = f"Deleted {len(cards)} card{'s' if len(cards) != 1 else ''} ({deleted_qty} cop{'ies' if deleted_qty != 1 else 'y'})."
    if wants_json:
        return jsonify({"success": True, "message": message, "deleted": len(cards), "deleted_qty": deleted_qty})

    flash(message, "success")
    return redirect(redirect_target)


def api_card_printing_options(card_id: int):
    """Return cached printings for a card so the UI can populate dropdowns."""
    card = get_or_404(Card, card_id)
    ensure_folder_access(card.folder, write=True)
    _ensure_cache_ready()

    oracle_id = card.oracle_id
    if not oracle_id:
        try:
            oracle_id = unique_oracle_by_name(card.name)
        except Exception:
            oracle_id = None

    prints: list[dict] = []
    if oracle_id:
        try:
            prints = list(prints_for_oracle(oracle_id) or [])
        except Exception:
            prints = []
    if not prints:
        try:
            print_row = find_by_set_cn(card.set_code, card.collector_number, card.name)
            if print_row:
                prints = [print_row]
                oracle_id = oracle_id or print_row.get("oracle_id")
        except Exception:
            prints = []

    current_value = f"{(card.set_code or '').upper()}::{card.collector_number or ''}::{(card.lang or 'en').upper()}"
    options: list[dict] = []
    seen_values: set[str] = set()
    for print_row in prints:
        set_code = (print_row.get("set") or "").upper()
        collector_number = str(print_row.get("collector_number") or "")
        lang = (print_row.get("lang") or "en").upper()
        value = f"{set_code}::{collector_number}::{lang}"
        if value in seen_values:
            continue
        seen_values.add(value)
        imgs = _image_from_print(print_row)
        options.append(
            {
                "value": value,
                "set": set_code,
                "set_name": print_row.get("set_name") or (set_name_for_code(set_code.lower()) if set_code else ""),
                "collector_number": collector_number,
                "lang": lang,
                "finishes": print_row.get("finishes") or [],
                "promo_types": print_row.get("promo_types") or [],
                "oracle_id": print_row.get("oracle_id") or oracle_id,
                "image": imgs.get("normal") or imgs.get("large") or imgs.get("small"),
            }
        )

    if not options:
        options.append(
            {
                "value": current_value,
                "set": (card.set_code or "").upper(),
                "set_name": set_name_for_code((card.set_code or "").lower()) if card.set_code else "",
                "collector_number": card.collector_number or "",
                "lang": (card.lang or "en").upper(),
                "finishes": ["foil" if card.is_foil else "nonfoil"],
                "promo_types": [],
                "oracle_id": oracle_id or card.oracle_id,
                "image": None,
            }
        )

    current_finish = "foil" if card.is_foil else "nonfoil"
    current_finishes: list[str] = []
    for option in options:
        if option["value"] == current_value:
            current_finishes = option.get("finishes") or []
            break
    if not current_finishes and options:
        current_finishes = options[0].get("finishes") or []

    return jsonify(
        {
            "options": options,
            "current": current_value,
            "finishes": current_finishes,
            "current_finish": current_finish,
        }
    )


def api_update_card_printing(card_id: int):
    """Change a card's printing (set/collector/lang/finish), merging quantities when needed."""
    card = get_or_404(Card, card_id)
    ensure_folder_access(card.folder, write=True)

    payload = request.get_json(silent=True) or {}
    printing_raw = (payload.get("printing") or payload.get("printing_value") or request.form.get("printing") or "").strip()
    finish_raw = (payload.get("finish") or request.form.get("finish") or "").strip().lower()
    qty_raw = payload.get("quantity") or request.form.get("quantity")

    if not printing_raw or "::" not in printing_raw:
        return jsonify({"success": False, "message": "Choose a printing to update."}), 400

    parts = printing_raw.split("::")
    while len(parts) < 3:
        parts.append("")
    set_code, collector_number, lang = parts[0].strip(), parts[1].strip(), (parts[2] or "en").strip()

    try:
        target_qty = int(qty_raw)
    except (TypeError, ValueError):
        target_qty = 1
    target_qty = max(1, min(target_qty, card.quantity or 1))

    _ensure_cache_ready()
    print_row = None
    oracle_id = card.oracle_id
    if not oracle_id:
        try:
            oracle_id = unique_oracle_by_name(card.name)
        except Exception:
            oracle_id = None

    try:
        if oracle_id:
            for candidate in prints_for_oracle(oracle_id) or []:
                matches_set = (candidate.get("set") or "").lower() == set_code.lower()
                matches_cn = str(candidate.get("collector_number") or "").lower() == str(collector_number).lower()
                matches_lang = (candidate.get("lang") or "en").lower() == lang.lower()
                if matches_set and matches_cn and matches_lang:
                    print_row = candidate
                    break
    except Exception:
        print_row = None

    if print_row is None:
        try:
            print_row = find_by_set_cn(set_code, collector_number, card.name)
        except Exception:
            print_row = None

    metadata = metadata_from_print(print_row) if print_row else {}
    new_name = (print_row or {}).get("name") or card.name
    new_oracle = (print_row or {}).get("oracle_id") or oracle_id or card.oracle_id
    new_type_line = metadata.get("type_line") or card.type_line
    new_rarity = metadata.get("rarity") or card.rarity
    finish_flag = finish_raw or ("foil" if card.is_foil else "nonfoil")
    is_foil = finish_flag in {"foil", "etched", "glossy", "gilded"}

    lang = (lang or "en").lower()
    set_code = (set_code or "").upper()
    collector_number = str(collector_number or "")

    merge_target = (
        Card.query.filter(
            Card.id != card.id,
            Card.folder_id == card.folder_id,
            func.lower(Card.name) == func.lower(new_name or card.name),
            Card.set_code == set_code,
            Card.collector_number == collector_number,
            Card.lang == lang,
            Card.is_foil == is_foil,
        )
        .order_by(Card.id.asc())
        .first()
    )

    remaining = (card.quantity or 0) - target_qty
    if remaining <= 0:
        if merge_target:
            merge_target.quantity = (merge_target.quantity or 0) + target_qty
            db.session.delete(card)
        else:
            card.name = new_name
            card.set_oracle_id(new_oracle)
            card.set_code = set_code
            card.collector_number = collector_number
            card.lang = lang
            card.is_foil = is_foil
            card.type_line = new_type_line
            card.rarity = new_rarity
            card.oracle_text = metadata.get("oracle_text") or card.oracle_text
            card.mana_value = metadata.get("mana_value") if metadata.get("mana_value") is not None else card.mana_value
            card.colors = metadata.get("colors") or card.colors
            card.color_identity = metadata.get("color_identity") or card.color_identity
            card.color_identity_mask = metadata.get("color_identity_mask") or card.color_identity_mask
            card.layout = metadata.get("layout") or card.layout
            if metadata.get("faces_json") is not None:
                card.faces_json = metadata.get("faces_json")
    else:
        card.quantity = remaining
        if merge_target:
            merge_target.quantity = (merge_target.quantity or 0) + target_qty
        else:
            updated = Card(
                name=new_name,
                set_code=set_code,
                collector_number=collector_number,
                folder_id=card.folder_id,
                quantity=target_qty,
                oracle_id=new_oracle,
                lang=lang,
                is_foil=is_foil,
                type_line=new_type_line,
                rarity=new_rarity,
                oracle_text=metadata.get("oracle_text"),
                mana_value=metadata.get("mana_value"),
                colors=metadata.get("colors"),
                color_identity=metadata.get("color_identity"),
                color_identity_mask=metadata.get("color_identity_mask") or card.color_identity_mask,
                layout=metadata.get("layout"),
                faces_json=metadata.get("faces_json"),
            )
            db.session.add(updated)

    _safe_commit()
    record_audit_event(
        "card_update_printing",
        {"card_id": card_id, "target": printing_raw, "qty": target_qty, "finish": finish_flag},
    )
    return jsonify({"success": True, "message": "Printing updated."})


__all__ = [
    "api_card_printing_options",
    "api_update_card_printing",
    "bulk_delete_cards",
    "bulk_move_cards",
]
