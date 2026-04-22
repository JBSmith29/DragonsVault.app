"""Create a deck by moving owned cards out of collection folders."""

from __future__ import annotations

import re
from typing import Any

from flask import render_template, request
from flask_login import current_user
from sqlalchemy import func

from extensions import db
from models import Card, Folder, FolderRole
from core.domains.cards.services.scryfall_cache import metadata_from_print, unique_oracle_by_name
from core.domains.decks.services.deck_tags import get_deck_tag_groups
from shared.folders import generate_unique_folder_name
from shared.mtg import _lookup_print_data


def _parse_collection_lines(raw_text: str) -> tuple[list[dict], list[str]]:
    entries: list[dict] = []
    errors: list[str] = []
    if not raw_text:
        return entries, ["Enter at least one card line."]
    pattern = re.compile(r"^\s*(\d+)\s*(?:x)?\s+(.+?)(?:\s*\(([^)]+)\))?(?:\s+(\S+))?\s*$")
    for idx, line in enumerate(raw_text.splitlines(), start=1):
        stripped = (line or "").strip()
        if not stripped:
            continue
        match = pattern.match(stripped)
        if not match:
            errors.append(
                f"Line {idx}: Could not parse '{stripped}'. Expected formats like '1 Card Name (SET) 123' or '2 Card Name'."
            )
            continue
        qty = max(int(match.group(1) or 0), 0)
        name = match.group(2).strip()
        set_code = (match.group(3) or "").strip().lower()
        collector_number = (match.group(4) or "").strip()
        if qty <= 0:
            errors.append(f"Line {idx}: Quantity must be positive.")
            continue
        entries.append(
            {
                "index": idx,
                "qty": qty,
                "name": name,
                "set_code": set_code,
                "collector_number": collector_number,
            }
        )
    return entries, errors


def deck_from_collection():
    form = {
        "deck_name": (request.form.get("deck_name") or "").strip(),
        "commander": (request.form.get("commander") or "").strip(),
        "deck_tag": (request.form.get("deck_tag") or "").strip(),
        "deck_lines": request.form.get("deck_lines") or "",
    }

    def _fmt_entry(entry: dict) -> str:
        card = entry.get("card")
        set_code = entry.get("set_code") or (card.set_code if card else None) or "?"
        collector_number = entry.get("collector_number") or (card.collector_number if card else None) or "?"
        set_part = set_code.upper() if isinstance(set_code, str) else str(set_code)
        return f"{entry['qty']}x {entry['name']} [{set_part} {collector_number}]"

    stage = request.form.get("stage") or "input"
    warnings: list[str] = []
    errors: list[str] = []
    infos: list[str] = []
    conflicts: list[dict] = []
    summary: dict | None = None

    if request.method == "POST":
        entries, parse_errors = _parse_collection_lines(form["deck_lines"])
        if parse_errors:
            errors.extend(parse_errors)
            return render_template(
                "decks/deck_from_collection.html",
                form=form,
                errors=errors,
                warnings=warnings,
                infos=infos,
                conflicts=conflicts,
                summary=summary,
                deck_tag_groups=get_deck_tag_groups(),
                stage="input",
            )
        if not form["deck_name"]:
            errors.append("Deck name is required.")
        if errors:
            return render_template(
                "decks/deck_from_collection.html",
                form=form,
                errors=errors,
                warnings=warnings,
                infos=infos,
                conflicts=conflicts,
                summary=summary,
                deck_tag_groups=get_deck_tag_groups(),
                stage="input",
            )

        resolved_entries: list[dict] = []
        total_requested = sum(entry["qty"] for entry in entries)
        resolved_count = 0
        resolve_needed = False

        for entry in entries:
            needs_choice = not entry["set_code"] or not entry["collector_number"]
            resolve_choice = request.form.get(f"resolve_{entry['index']}")
            base_query = (
                Card.query.join(Folder).join(FolderRole, FolderRole.folder_id == Folder.id)
                .filter(
                    func.lower(Card.name) == entry["name"].strip().lower(),
                    FolderRole.role == FolderRole.ROLE_COLLECTION,
                    Folder.owner_user_id == current_user.id,
                )
            )
            if entry["set_code"]:
                base_query = base_query.filter(func.lower(Card.set_code) == entry["set_code"])
            if entry["collector_number"]:
                base_query = base_query.filter(func.lower(Card.collector_number) == entry["collector_number"].lower())
            candidates = base_query.all()

            if not candidates:
                warnings.append(f"Line {entry['index']}: {_fmt_entry(entry)} not found in your collection.")
                resolved_entries.append({**entry, "card": None})
                continue

            if resolve_choice:
                chosen = next((candidate for candidate in candidates if str(candidate.id) == str(resolve_choice)), None)
                if not chosen:
                    errors.append(f"Line {entry['index']}: Selected printing not found. Please choose again.")
                    resolve_needed = True
                    conflicts.append(
                        {
                            "index": entry["index"],
                            "display": _fmt_entry(entry),
                            "options": [
                                {
                                    "id": candidate.id,
                                    "name": candidate.name,
                                    "set_code": candidate.set_code,
                                    "collector_number": candidate.collector_number,
                                    "quantity": candidate.quantity or 0,
                                    "lang": candidate.lang or "en",
                                    "is_foil": bool(candidate.is_foil),
                                    "folder": candidate.folder.name if candidate.folder else None,
                                }
                                for candidate in candidates
                            ],
                            "selected": resolve_choice,
                        }
                    )
                    continue
                resolved_entries.append({**entry, "card": chosen})
                resolved_count += 1
                continue

            if len(candidates) == 1:
                if needs_choice:
                    resolve_needed = True
                    conflicts.append(
                        {
                            "index": entry["index"],
                            "display": _fmt_entry(entry),
                            "options": [
                                {
                                    "id": candidate.id,
                                    "name": candidate.name,
                                    "set_code": candidate.set_code,
                                    "collector_number": candidate.collector_number,
                                    "quantity": candidate.quantity or 0,
                                    "lang": candidate.lang or "en",
                                    "is_foil": bool(candidate.is_foil),
                                    "folder": candidate.folder.name if candidate.folder else None,
                                }
                                for candidate in candidates
                            ],
                            "selected": None,
                        }
                    )
                else:
                    resolved_entries.append({**entry, "card": candidates[0]})
                    resolved_count += 1
            else:
                resolve_needed = True
                conflicts.append(
                    {
                        "index": entry["index"],
                        "display": _fmt_entry(entry),
                        "options": [
                            {
                                "id": candidate.id,
                                "name": candidate.name,
                                "set_code": candidate.set_code,
                                "collector_number": candidate.collector_number,
                                "quantity": candidate.quantity or 0,
                                "lang": candidate.lang or "en",
                                "is_foil": bool(candidate.is_foil),
                                "folder": candidate.folder.name if candidate.folder else None,
                            }
                            for candidate in candidates
                        ],
                        "selected": None,
                    }
                )

        summary = {
            "requested": len(entries),
            "resolved": resolved_count,
            "total_move": total_requested,
        }

        if resolve_needed or conflicts:
            stage = "resolve"
            return render_template(
                "decks/deck_from_collection.html",
                form=form,
                errors=errors,
                warnings=warnings,
                infos=infos,
                conflicts=conflicts,
                summary=summary,
                deck_tag_groups=get_deck_tag_groups(),
                stage=stage,
            )

        deck_name = generate_unique_folder_name(form["deck_name"], owner_user_id=current_user.id)
        folder = Folder(
            name=deck_name,
            deck_tag=form["deck_tag"] or None,
            owner=current_user.username or current_user.email or None,
            owner_user_id=current_user.id,
            is_proxy=False,
        )
        folder.set_primary_role(Folder.CATEGORY_DECK)
        commander_warnings: list[str] = []
        commander_clean = form["commander"]
        if commander_clean:
            try:
                commander_oid = unique_oracle_by_name(commander_clean)
            except Exception as exc:
                commander_warnings.append(f"Commander lookup failed: {exc}")
                commander_oid = None
            folder.commander_name = commander_clean
            folder.commander_oracle_id = commander_oid
        db.session.add(folder)
        db.session.flush()

        moved_total = 0
        for entry in resolved_entries:
            card = entry.get("card")
            desired_qty = entry["qty"]
            remaining_qty = desired_qty
            moved_from_collection = 0

            if card:
                available_qty = card.quantity or 0
                move_qty = min(desired_qty, available_qty)
                remaining_qty = desired_qty - move_qty
                if move_qty > 0:
                    target = (
                        Card.query.filter(
                            Card.folder_id == folder.id,
                            Card.name == card.name,
                            Card.set_code == card.set_code,
                            Card.collector_number == card.collector_number,
                            Card.lang == card.lang,
                            Card.is_foil == card.is_foil,
                        )
                        .order_by(Card.id.asc())
                        .first()
                    )
                    if target:
                        target.quantity = (target.quantity or 0) + move_qty
                    else:
                        clone_metadata: dict[str, Any] = {}
                        if not (card.type_line and card.rarity and card.color_identity and card.color_identity_mask is not None):
                            try:
                                clone_print = _lookup_print_data(card.set_code, card.collector_number, card.name, card.oracle_id)
                            except Exception:
                                clone_print = None
                            if clone_print:
                                clone_metadata = metadata_from_print(clone_print) or {}

                        clone_type_line = card.type_line or clone_metadata.get("type_line")
                        clone_rarity = card.rarity or clone_metadata.get("rarity")
                        clone_oracle_text = card.oracle_text or clone_metadata.get("oracle_text")
                        clone_mana_value = card.mana_value if card.mana_value is not None else clone_metadata.get("mana_value")
                        clone_colors = card.colors or clone_metadata.get("colors")
                        clone_color_identity = card.color_identity or clone_metadata.get("color_identity")
                        clone_color_identity_mask = card.color_identity_mask
                        if clone_color_identity_mask is None:
                            clone_color_identity_mask = clone_metadata.get("color_identity_mask")
                        db.session.add(
                            Card(
                                name=card.name,
                                set_code=card.set_code,
                                collector_number=card.collector_number,
                                folder_id=folder.id,
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
                        )
                    card.quantity = (card.quantity or 0) - move_qty
                    if card.quantity is not None and card.quantity <= 0:
                        db.session.delete(card)
                    moved_from_collection = move_qty

            if moved_from_collection < desired_qty:
                proxy_qty = remaining_qty if remaining_qty > 0 else 0
                if proxy_qty > 0:
                    warnings.append(
                        f"Line {entry['index']}: Missing {proxy_qty} copies for {_fmt_entry(entry)} "
                        f"(requested {desired_qty}, moved {moved_from_collection} from collection)."
                    )
            moved_total += moved_from_collection

        if commander_warnings:
            warnings.extend(commander_warnings)

        db.session.commit()
        infos.append(f"Created deck '{deck_name}' and moved {moved_total} card(s).")
        form["deck_lines"] = ""
        stage = "done"

    return render_template(
        "decks/deck_from_collection.html",
        form=form,
        errors=errors,
        warnings=warnings,
        infos=infos,
        conflicts=conflicts,
        summary=summary,
        deck_tag_groups=get_deck_tag_groups(),
        stage=stage,
    )


__all__ = [
    "deck_from_collection",
]
