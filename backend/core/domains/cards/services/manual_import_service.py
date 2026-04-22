"""Manual pasted-list import service."""

from __future__ import annotations

from flask import flash, redirect, request, url_for
from flask_login import current_user
from sqlalchemy import func

from extensions import db
from models import Card, Folder
from shared.service_result import ServiceResult
from shared.mtg import _move_folder_choices
from core.domains.cards.services.import_shared_service import (
    _parse_manual_card_list,
    _printing_options_for_name,
)
from core.domains.cards.services.scryfall_cache import ensure_cache_loaded, find_by_set_cn, find_by_set_cn_loose, metadata_from_print
from shared.validation import ValidationError, log_validation_error, parse_optional_positive_int, parse_positive_int_list


def manual_import(*, session_obj) -> ServiceResult:
    """Manual import wizard for pasted decklists."""
    folder_options = _move_folder_choices()
    folder_lookup = {str(option.id): option.name for option in folder_options}
    card_list = request.form.get("card_list") or session_obj.pop("manual_import_seed", "") or ""
    parsed_entries: list[dict] = []
    step = "input"
    entry_errors: list[str] = []

    default_folder_id_raw = (request.form.get("default_folder_id") or "").strip()
    try:
        default_folder_id_val = parse_optional_positive_int(default_folder_id_raw, field="default folder id")
    except ValidationError as exc:
        log_validation_error(exc, context="manual_import")
        flash("Invalid default folder selection.", "warning")
        return ServiceResult(response=redirect(url_for("views.manual_import")))
    default_folder_id = str(default_folder_id_val) if default_folder_id_val is not None else ""
    default_folder_name = (request.form.get("default_folder_name") or "").strip()
    default_folder_category = (request.form.get("default_folder_category") or Folder.CATEGORY_DECK).strip().lower()
    if default_folder_category not in {Folder.CATEGORY_DECK, Folder.CATEGORY_COLLECTION}:
        default_folder_category = Folder.CATEGORY_DECK

    default_folder_label = "None (choose per card)"
    if default_folder_id and default_folder_id in folder_lookup:
        default_folder_label = folder_lookup.get(default_folder_id) or default_folder_label
    elif default_folder_name:
        default_folder_label = f'Create "{default_folder_name}"'

    def resolve_target_folder(folder_id_value: str | None, folder_name_value: str | None) -> Folder:
        folder: Folder | None = None
        if folder_id_value:
            try:
                folder_id_val = parse_optional_positive_int(folder_id_value, field="folder id")
            except ValidationError as exc:
                log_validation_error(exc, context="manual_import")
                raise
            if folder_id_val is not None:
                folder = Folder.query.filter(
                    Folder.id == folder_id_val,
                    Folder.owner_user_id == current_user.id,
                ).first()
        if folder:
            return folder
        fallback_name = (folder_name_value or default_folder_name or "Manual Import").strip()
        if not fallback_name:
            fallback_name = "Manual Import"
        folder = (
            Folder.query.filter(
                func.lower(Folder.name) == fallback_name.lower(),
                Folder.owner_user_id == current_user.id,
            ).first()
        )
        if not folder:
            folder = Folder(
                name=fallback_name,
                owner_user_id=current_user.id,
            )
            folder.set_primary_role(default_folder_category)
            db.session.add(folder)
            db.session.flush()
        return folder

    if request.method == "POST":
        action = (request.form.get("action") or "").strip().lower()
        if action == "parse":
            raw_entries = _parse_manual_card_list(card_list)
            if not raw_entries:
                flash("Please enter at least one card (e.g., '3 Sol Ring').", "warning")
            else:
                parsed_entries = []
                idx_counter = 0
                prefill_folder_name = default_folder_name
                if not prefill_folder_name and not default_folder_id:
                    prefill_folder_name = "Manual Import"
                for raw_entry in raw_entries:
                    qty = max(raw_entry["quantity"], 1)
                    for _ in range(qty):
                        options = _printing_options_for_name(raw_entry["name"])
                        parsed_entries.append(
                            {
                                "index": idx_counter,
                                "name": raw_entry["name"],
                                "quantity": 1,
                                "options": options,
                                "prefill_folder_id": default_folder_id,
                                "prefill_folder_name": prefill_folder_name or "",
                            }
                        )
                        idx_counter += 1
                step = "review"
        elif action == "quick_upload":
            raw_entries = _parse_manual_card_list(card_list)
            if not raw_entries:
                flash("Please enter at least one card (e.g., '3 Sol Ring').", "warning")
            else:
                try:
                    folder = resolve_target_folder(default_folder_id, default_folder_name)
                except ValidationError as exc:
                    log_validation_error(exc, context="manual_import")
                    flash("Invalid folder selection.", "warning")
                    return ServiceResult(response=redirect(url_for("views.manual_import")))

                merged: dict[str, int] = {}
                for entry in raw_entries:
                    name = entry.get("name") or ""
                    qty = int(entry.get("quantity") or 0) or 1
                    if not name:
                        continue
                    merged[name] = merged.get(name, 0) + max(qty, 1)

                created = 0
                for name, qty in merged.items():
                    card = Card(
                        name=name,
                        set_code="",
                        collector_number="",
                        lang="EN",
                        folder_id=folder.id,
                        quantity=qty,
                        is_foil=False,
                    )
                    db.session.add(card)
                    created += 1

                if created:
                    db.session.commit()
                    total_qty = sum(merged.values())
                    flash(
                        f"Quick uploaded {total_qty} card{'s' if total_qty != 1 else ''} into \"{folder.name}\". "
                        "Edit printings and details later from that folder.",
                        "success",
                    )
                    return ServiceResult(response=redirect(url_for("views.list_cards")))
                db.session.rollback()
                flash("Unable to quick upload the provided entries.", "warning")
        elif action == "import":
            entry_ids_raw = (request.form.get("entry_ids") or "").strip()
            try:
                entry_ids = [
                    str(value)
                    for value in parse_positive_int_list(entry_ids_raw.split(","), field="entry id(s)", min_value=0)
                ]
            except ValidationError as exc:
                log_validation_error(exc, context="manual_import")
                flash("Invalid entry selection.", "warning")
                return ServiceResult(response=redirect(url_for("views.manual_import")))
            if not entry_ids:
                flash("No entries were selected for import.", "warning")
            else:
                created = 0
                ensure_cache_loaded()
                for entry_id in entry_ids:
                    name = (request.form.get(f"entry-{entry_id}-name") or "").strip()
                    if not name:
                        entry_errors.append(f"Entry {entry_id}: missing card name.")
                        continue
                    qty_raw = request.form.get(f"entry-{entry_id}-quantity")
                    try:
                        quantity = max(int(qty_raw or 1), 1)
                    except (TypeError, ValueError):
                        quantity = 1

                    printing_value = (request.form.get(f"entry-{entry_id}-printing") or "").strip()
                    set_code = collector_number = lang = None
                    if printing_value and "::" in printing_value:
                        pieces = printing_value.split("::")
                        if len(pieces) >= 3:
                            set_code, collector_number, lang = pieces[:3]
                    set_code = (set_code or "").upper()
                    lang = (lang or "EN").upper()

                    finish = (request.form.get(f"entry-{entry_id}-finish") or "nonfoil").lower()
                    is_foil = finish == "foil"

                    folder_id_raw = (request.form.get(f"entry-{entry_id}-folder_id") or "").strip()
                    folder_name = (request.form.get(f"entry-{entry_id}-folder_name") or "").strip()

                    try:
                        folder = resolve_target_folder(folder_id_raw, folder_name)
                    except ValidationError:
                        entry_errors.append(f"Entry {entry_id}: invalid folder selection.")
                        continue

                    scryfall_data = None
                    if set_code and collector_number:
                        scryfall_data = find_by_set_cn(set_code, collector_number, name)
                        if not scryfall_data:
                            scryfall_data = find_by_set_cn_loose(set_code, collector_number, name)

                    metadata = metadata_from_print(scryfall_data) if scryfall_data else {}
                    card_kwargs = {
                        "name": (scryfall_data or {}).get("name") or name,
                        "set_code": (scryfall_data or {}).get("set") or set_code or "",
                        "collector_number": (scryfall_data or {}).get("collector_number") or collector_number or "",
                        "lang": (scryfall_data or {}).get("lang") or lang or "EN",
                        "folder_id": folder.id,
                        "quantity": quantity,
                        "is_foil": is_foil,
                        "rarity": metadata.get("rarity") or (scryfall_data or {}).get("rarity"),
                        "oracle_id": (scryfall_data or {}).get("oracle_id"),
                        "type_line": metadata.get("type_line"),
                        "oracle_text": metadata.get("oracle_text"),
                        "mana_value": metadata.get("mana_value"),
                        "colors": metadata.get("colors"),
                        "color_identity": metadata.get("color_identity"),
                        "color_identity_mask": metadata.get("color_identity_mask"),
                        "layout": metadata.get("layout"),
                        "faces_json": metadata.get("faces_json"),
                    }

                    new_card = Card(**card_kwargs)
                    db.session.add(new_card)
                    created += 1

                if created:
                    db.session.commit()
                    flash(f"Added {created} card{'s' if created != 1 else ''} via manual import.", "success")
                    return ServiceResult(response=redirect(url_for("views.list_cards")))
                db.session.rollback()
                if not entry_errors:
                    flash("Unable to import the provided entries.", "warning")

    return ServiceResult(
        template="cards/manual_import.html",
        context={
            "folder_options": folder_options,
            "folder_lookup": folder_lookup,
            "card_list": card_list,
            "entries": parsed_entries,
            "step": step,
            "entry_errors": entry_errors,
            "default_folder_id": default_folder_id,
            "default_folder_name": default_folder_name,
            "default_folder_category": default_folder_category,
            "default_folder_label": default_folder_label,
            "deck_category": Folder.CATEGORY_DECK,
            "collection_category": Folder.CATEGORY_COLLECTION,
        },
    )


__all__ = ["manual_import"]
