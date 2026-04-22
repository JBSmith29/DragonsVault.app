"""Proxy deck creation flows."""

from __future__ import annotations

import hashlib
import time
from typing import Iterable

from flask import current_app, flash, jsonify, redirect, request, session, url_for
from flask_login import current_user
from sqlalchemy.exc import IntegrityError

from extensions import db
from models import Card, Folder
from core.domains.cards.services.scryfall_cache import (
    cache_ready,
    ensure_cache_loaded,
    find_by_set_cn,
    metadata_from_print,
    unique_oracle_by_name,
)
from core.domains.decks.services.deck_tags import sync_folder_deck_tag_map
from core.domains.decks.services.commander_utils import split_commander_names
from core.domains.decks.services.proxy_decks import fetch_proxy_deck, resolve_proxy_cards
from shared.folders import folder_name_exists, generate_unique_folder_name


def _ensure_cache_ready() -> bool:
    return cache_ready() or ensure_cache_loaded()


def _create_proxy_deck_from_lines(
    deck_name: str | None,
    owner: str | None,
    commander_name: str | None,
    deck_lines: Iterable[str],
) -> tuple[Folder | None, list[str], list[str]]:
    """
    Create a proxy deck folder populated with resolved cards.

    Returns (folder, warnings, info_messages). Folder is None if no cards were resolved.
    """
    deck_lines = list(deck_lines or [])
    line_count = len(deck_lines)
    resolved_cards, resolve_errors = resolve_proxy_cards(deck_lines)
    if not resolved_cards:
        fallback_reason = "Deck parser did not resolve any recognizable cards."
        reason = resolve_errors[0] if resolve_errors else fallback_reason
        current_app.logger.warning(
            "Proxy deck creation aborted before insert: %s",
            reason,
            extra={
                "deck_name": (deck_name or "").strip() or None,
                "owner": (owner or "").strip() or None,
                "commander_hint": (commander_name or "").strip() or None,
                "line_count": line_count,
                "line_sample": deck_lines[:5],
                "warnings": resolve_errors,
            },
        )
        if not resolve_errors:
            resolve_errors = [fallback_reason]
        return None, resolve_errors, []

    info_messages: list[str] = []
    base_name = (deck_name or "").strip() or "Proxy Deck"
    final_name = base_name
    owner_user_id = current_user.id if current_user.is_authenticated else None
    if folder_name_exists(final_name, owner_user_id=owner_user_id):
        final_name = generate_unique_folder_name(final_name, owner_user_id=owner_user_id)
        if final_name != base_name:
            info_messages.append(f'Deck name in use. Created as "{final_name}".')

    folder = Folder(
        name=final_name,
        owner=owner.strip() if owner else None,
        owner_user_id=owner_user_id,
        is_proxy=True,
    )
    folder.set_primary_role(Folder.CATEGORY_DECK)

    commander_warnings: list[str] = []
    commander_clean = (commander_name or "").strip()
    if commander_clean:
        parts = split_commander_names(commander_clean) or [commander_clean]
        folder.commander_name = " // ".join(parts)
        oracle_ids: list[str] = []
        for part in parts:
            try:
                oracle_id = unique_oracle_by_name(part)
            except Exception as exc:
                commander_warnings.append(f"Commander lookup failed for {part}: {exc}")
                oracle_id = None
            if oracle_id:
                oracle_ids.append(oracle_id)
        folder.commander_oracle_id = ",".join(oracle_ids) if oracle_ids else None

    db.session.add(folder)
    try:
        db.session.flush()
    except IntegrityError:
        db.session.rollback()
        final_name = generate_unique_folder_name(final_name, owner_user_id=owner_user_id)
        folder.name = final_name
        db.session.add(folder)
        db.session.flush()
        sync_folder_deck_tag_map(folder)
        info_messages.append(f'Deck name in use. Created as "{final_name}".')

    aggregated: dict[tuple[str | None, str, str, str], dict] = {}
    for card in resolved_cards:
        key = (card.oracle_id, card.set_code.upper(), str(card.collector_number), card.lang.lower())
        entry = aggregated.get(key)
        if entry:
            entry["quantity"] += card.quantity
        else:
            aggregated[key] = {
                "name": card.name,
                "oracle_id": card.oracle_id,
                "set_code": card.set_code.upper(),
                "collector_number": str(card.collector_number),
                "lang": card.lang.lower(),
                "quantity": card.quantity,
            }

    for entry in aggregated.values():
        metadata = {}
        print_row = None
        if _ensure_cache_ready():
            try:
                print_row = find_by_set_cn(entry["set_code"], entry["collector_number"], entry["name"])
            except Exception:
                print_row = None
        if print_row:
            metadata = metadata_from_print(print_row)
        db.session.add(
            Card(
                name=entry["name"],
                set_code=entry["set_code"],
                collector_number=entry["collector_number"],
                folder_id=folder.id,
                oracle_id=entry["oracle_id"],
                lang=entry["lang"],
                is_foil=False,
                quantity=max(int(entry["quantity"]), 1),
                type_line=metadata.get("type_line"),
                rarity=metadata.get("rarity"),
                oracle_text=metadata.get("oracle_text"),
                mana_value=metadata.get("mana_value"),
                colors=metadata.get("colors"),
                color_identity=metadata.get("color_identity"),
                color_identity_mask=metadata.get("color_identity_mask"),
                layout=metadata.get("layout"),
                faces_json=metadata.get("faces_json"),
            )
        )

    warnings = resolve_errors + commander_warnings
    return folder, warnings, info_messages


def create_proxy_deck():
    deck_name = (request.form.get("deck_name") or "").strip()
    owner = (request.form.get("deck_owner") or "").strip() or None
    commander_input = (request.form.get("deck_commander") or "").strip()
    decklist_text = request.form.get("decklist") or ""
    deck_url = (request.form.get("deck_url") or "").strip()
    expects_json = request.headers.get("X-Requested-With", "").lower() == "xmlhttprequest"
    if not expects_json:
        best = request.accept_mimetypes.best
        expects_json = best == "application/json"

    fetched_errors: list[str] = []
    if deck_url:
        fetched_name = fetched_owner = fetched_commander = None
        fetched_lines: list[str] = []
        errors: list[str] = []

        fetched_name, fetched_owner, fetched_commander, fetched_lines, errors = fetch_proxy_deck(deck_url)
        fetched_errors.extend(errors)
        if fetched_lines:
            decklist_text = decklist_text or "\n".join(fetched_lines)
        if not deck_name and fetched_name:
            deck_name = fetched_name
        if not owner and fetched_owner:
            owner = fetched_owner
        if not commander_input and fetched_commander:
            commander_input = fetched_commander

    deck_lines = [line for line in (decklist_text.splitlines() if decklist_text else []) if line.strip()]
    if not deck_lines:
        detail = "No cards were found in the submitted decklist."
        current_app.logger.warning(
            "Proxy deck creation blocked: empty deck submission.",
            extra={
                "deck_name": deck_name or None,
                "owner": owner,
                "deck_url": deck_url or None,
                "fetched_errors": fetched_errors,
            },
        )
        if expects_json:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": detail,
                        "warnings": fetched_errors[:10],
                    }
                ),
                400,
            )
        flash(detail, "warning")
        for msg in fetched_errors:
            flash(msg, "warning")
        return redirect(request.referrer or url_for("views.decks_overview"))

    signature_source = "\n".join(deck_lines).strip().lower()
    if deck_url:
        signature_source = f"url:{deck_url.strip().lower()}\n{signature_source}"
    if deck_name:
        signature_source = f"name:{deck_name.strip().lower()}\n{signature_source}"
    if owner:
        signature_source = f"owner:{owner.strip().lower()}\n{signature_source}"
    if commander_input:
        signature_source = f"commander:{commander_input.strip().lower()}\n{signature_source}"
    signature = hashlib.sha256(signature_source.encode("utf-8")).hexdigest()
    last_signature = session.get("proxy_deck_signature")
    last_ts = session.get("proxy_deck_signature_ts")
    last_id = session.get("proxy_deck_signature_id")
    if last_signature == signature and isinstance(last_ts, (int, float)):
        if (time.time() - float(last_ts)) < 15:
            existing_folder = db.session.get(Folder, last_id) if last_id else None
            if existing_folder and existing_folder.is_proxy:
                redirect_url = url_for("views.folder_detail", folder_id=existing_folder.id)
                if expects_json:
                    return jsonify({"ok": True, "folder_id": existing_folder.id, "redirect": redirect_url}), 200
                flash('Proxy deck already created. Redirecting to the existing deck.', "info")
                return redirect(redirect_url)

    folder, creation_warnings, info_messages = _create_proxy_deck_from_lines(
        deck_name,
        owner,
        commander_input,
        deck_lines,
    )
    if not folder:
        combined = fetched_errors + creation_warnings
        detail = combined[0] if combined else "No cards were found in the submitted decklist."
        current_app.logger.warning(
            "Proxy deck creation failed after parsing.",
            extra={
                "deck_name": deck_name or None,
                "owner": owner,
                "deck_url": deck_url or None,
                "commander_hint": commander_input or None,
                "line_count": len(deck_lines),
                "warning_sample": combined[:5],
            },
        )
        if expects_json:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": detail,
                        "warnings": combined[:10],
                    }
                ),
                400,
            )
        flash(f"Unable to create proxy deck: {detail}", "danger")
        for msg in combined:
            flash(msg, "warning")
        return redirect(request.referrer or url_for("views.decks_overview"))

    db.session.commit()
    redirect_url = url_for("views.folder_detail", folder_id=folder.id)
    session["proxy_deck_signature"] = signature
    session["proxy_deck_signature_ts"] = time.time()
    session["proxy_deck_signature_id"] = folder.id

    combined_warnings = fetched_errors + creation_warnings
    if expects_json:
        return (
            jsonify(
                {
                    "ok": True,
                    "folder_id": folder.id,
                    "redirect": redirect_url,
                    "warnings": combined_warnings[:10],
                    "info": info_messages[:5],
                }
            ),
            200,
        )

    for msg in info_messages:
        flash(msg, "info")

    if combined_warnings:
        for msg in combined_warnings[:5]:
            flash(msg, "warning")
        if len(combined_warnings) > 5:
            flash(f"{len(combined_warnings) - 5} additional warnings suppressed.", "warning")

    flash(f'Created proxy deck "{folder.name}".', "success")
    return redirect(redirect_url)


def create_proxy_deck_bulk():
    raw_urls = (request.form.get("deck_urls") or "").strip()
    if not raw_urls:
        flash("Please provide at least one MTGGoldfish deck URL.", "warning")
        return redirect(request.referrer or url_for("views.decks_overview"))

    urls = [line.strip() for line in raw_urls.splitlines() if line.strip()]
    if not urls:
        flash("Please provide at least one MTGGoldfish deck URL.", "warning")
        return redirect(request.referrer or url_for("views.decks_overview"))

    imported: list[Folder] = []
    warning_messages: list[str] = []
    info_messages: list[str] = []
    failure_messages: list[str] = []

    for url in urls:
        fetched_name, fetched_owner, fetched_commander, fetched_lines, fetch_errors = fetch_proxy_deck(url)
        if not fetched_lines:
            message = fetch_errors[0] if fetch_errors else "No decklist data returned."
            failure_messages.append(f"{url}: {message}")
            continue

        folder, creation_warnings, creation_info = _create_proxy_deck_from_lines(
            fetched_name,
            fetched_owner,
            fetched_commander,
            fetched_lines,
        )
        if not folder:
            combined = fetch_errors + creation_warnings
            message = combined[0] if combined else "Unable to import deck."
            failure_messages.append(f"{url}: {message}")
            continue

        imported.append(folder)
        info_messages.extend(creation_info)

        combined_warnings = fetch_errors + creation_warnings
        for msg in combined_warnings:
            warning_messages.append(f"{folder.name}: {msg}")

    if imported:
        db.session.commit()
        flash(
            f'Imported {len(imported)} proxy deck{"s" if len(imported) != 1 else ""}.',
            "success",
        )
        for msg in info_messages:
            flash(msg, "info")
    else:
        db.session.rollback()

    for msg in warning_messages[:5]:
        flash(msg, "warning")
    if len(warning_messages) > 5:
        flash(f"{len(warning_messages) - 5} additional warnings suppressed.", "warning")

    for msg in failure_messages[:5]:
        flash(msg, "danger")
    if len(failure_messages) > 5:
        flash(f"{len(failure_messages) - 5} additional errors suppressed.", "danger")

    if not imported and not failure_messages:
        flash("No decks were imported.", "warning")

    return redirect(url_for("views.decks_overview"))


def api_fetch_proxy_deck():
    payload = request.get_json(silent=True) or {}
    deck_url = (payload.get("deck_url") or request.form.get("deck_url") or "").strip()
    if not deck_url:
        return jsonify({"ok": False, "error": "No deck URL provided."}), 400

    name, owner, commander, lines, errors = fetch_proxy_deck(deck_url)
    response = {
        "ok": True,
        "deck_name": name,
        "owner": owner,
        "commander": commander,
        "decklist": "\n".join(lines) if lines else "",
        "warnings": errors,
    }
    if not lines:
        response["ok"] = False
        response["error"] = errors[0] if errors else "Unable to read decklist from MTGGoldfish."
        status = 400
    else:
        status = 200
    return jsonify(response), status


__all__ = [
    "api_fetch_proxy_deck",
    "create_proxy_deck",
    "create_proxy_deck_bulk",
]
