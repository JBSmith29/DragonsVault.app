"""Folder metadata mutation and lightweight JSON endpoints."""

from __future__ import annotations

import threading

from flask import current_app, flash, jsonify, redirect, request, url_for
from sqlalchemy import func

from extensions import db
from models import Card, Folder
from core.domains.cards.services import scryfall_cache as sc
from core.domains.decks.services.commander_assignment_service import _clear_deck_metadata_wizard_cache
from core.domains.decks.services.commander_utils import primary_commander_name, primary_commander_oracle_id
from core.domains.decks.services.deck_tags import (
    clear_folder_deck_tags,
    get_deck_tag_category,
    resolve_deck_tag_from_slug,
    set_folder_deck_tag,
)
from core.domains.decks.services.edhrec.edhrec_ingestion_service import ingest_commander_tag_data
from shared.auth import ensure_folder_access
from shared.database import get_or_404, safe_commit as _safe_commit
from shared.folders import generate_unique_folder_name


def set_folder_tag(folder_id: int):
    folder = get_or_404(Folder, folder_id)
    ensure_folder_access(folder, write=True)
    if folder.is_collection:
        message = "Tags can only be set for deck folders."
        if request.is_json:
            return jsonify({"ok": False, "error": message}), 400
        flash(message, "warning")
        return redirect(url_for("views.folder_detail", folder_id=folder_id))

    payload = request.get_json(silent=True) or {}
    tag = payload.get("tag") or request.form.get("tag") or ""
    tag = tag.strip()

    if not tag:
        message = "No tag provided."
        if request.is_json:
            return jsonify({"ok": False, "error": message}), 400
        flash(message, "danger")
        return redirect(request.referrer or url_for("views.folder_detail", folder_id=folder_id))

    tag_entry = set_folder_deck_tag(folder, tag, source="user", locked=True)
    if not tag_entry:
        message = "Unable to apply tag."
        if request.is_json:
            return jsonify({"ok": False, "error": message}), 400
        flash(message, "danger")
        return redirect(request.referrer or url_for("views.folder_detail", folder_id=folder_id))

    _safe_commit()
    _clear_deck_metadata_wizard_cache()

    category = get_deck_tag_category(tag_entry.name if tag_entry else tag)
    if request.is_json:
        return jsonify({"ok": True, "tag": tag, "category": category})

    flash(f'Deck tag set to "{tag}".', "success")
    return redirect(request.referrer or url_for("views.folder_detail", folder_id=folder_id))


def clear_folder_tag(folder_id: int):
    folder = get_or_404(Folder, folder_id)
    ensure_folder_access(folder, write=True)
    if folder.is_collection:
        message = "Tags can only be cleared on deck folders."
        if request.is_json:
            return jsonify({"ok": False, "error": message}), 400
        flash(message, "warning")
        return redirect(url_for("views.folder_detail", folder_id=folder_id))

    clear_folder_deck_tags(folder)
    _safe_commit()
    _clear_deck_metadata_wizard_cache()

    if request.is_json:
        return jsonify({"ok": True})

    flash("Deck tag cleared.", "info")
    return redirect(request.referrer or url_for("views.folder_detail", folder_id=folder_id))


def set_folder_owner(folder_id: int):
    folder = get_or_404(Folder, folder_id)
    ensure_folder_access(folder, write=True)
    if folder.is_collection:
        message = "Owner can only be set for deck folders."
        if request.is_json:
            return jsonify({"ok": False, "error": message}), 400
        flash(message, "warning")
        return redirect(url_for("views.folder_detail", folder_id=folder_id))

    payload = request.get_json(silent=True) or {}
    owner_value = payload.get("owner")
    if owner_value is None:
        owner_value = request.form.get("owner")

    owner_value = (owner_value or "").strip()
    folder.owner = owner_value or None
    _safe_commit()

    if request.is_json:
        return jsonify({"ok": True, "owner": folder.owner})

    if owner_value:
        flash(f'Deck owner set to "{owner_value}".', "success")
    else:
        flash("Deck owner cleared.", "info")
    return redirect(request.referrer or url_for("views.folder_detail", folder_id=folder_id))


def set_folder_proxy(folder_id: int):
    folder = get_or_404(Folder, folder_id)
    ensure_folder_access(folder, write=True)
    if folder.is_collection:
        message = "Proxy status can only be changed on deck folders."
        if request.is_json:
            return jsonify({"ok": False, "error": message}), 400
        flash(message, "warning")
        return redirect(url_for("views.folder_detail", folder_id=folder_id))

    payload = request.get_json(silent=True) or {}
    raw_flag = payload.get("is_proxy")
    if raw_flag is None:
        raw_flag = request.form.get("is_proxy")
    desired = str(raw_flag).strip().lower() in {"1", "true", "yes", "on"}

    folder.is_proxy = desired
    _safe_commit()

    message = "Marked deck as proxy." if desired else "Marked deck as owned."
    level = "success" if desired else "info"

    if request.is_json:
        return jsonify({"ok": True, "is_proxy": desired})

    flash(message, level)
    return redirect(request.referrer or url_for("views.folder_detail", folder_id=folder_id))


def rename_proxy_deck(folder_id: int):
    folder = get_or_404(Folder, folder_id)
    ensure_folder_access(folder, write=True)
    new_name = (request.form.get("new_name") or "").strip()
    if not new_name:
        flash("Deck name cannot be empty.", "warning")
        return redirect(request.referrer or url_for("views.folder_detail", folder_id=folder_id))

    final_name = generate_unique_folder_name(new_name, exclude_id=folder.id)
    if final_name != new_name:
        flash(f'Deck name in use. Renamed to "{final_name}".', "info")

    if (folder.name or "").strip() == final_name:
        flash("Deck name unchanged.", "info")
        return redirect(request.referrer or url_for("views.folder_detail", folder_id=folder_id))

    folder.name = final_name
    _safe_commit()
    flash("Deck name updated.", "success")
    return redirect(request.referrer or url_for("views.folder_detail", folder_id=folder_id))


def refresh_folder_edhrec(folder_id: int):
    folder = get_or_404(Folder, folder_id)
    ensure_folder_access(folder, write=True)
    if folder.is_collection:
        flash("EDHREC recommendations are only available for deck folders.", "warning")
        return redirect(url_for("views.folder_detail", folder_id=folder.id))

    commander_oracle_id = primary_commander_oracle_id(folder.commander_oracle_id)
    commander_name = primary_commander_name(folder.commander_name) or folder.commander_name
    if not commander_oracle_id and commander_name:
        try:
            sc.ensure_cache_loaded()
            commander_oracle_id = sc.unique_oracle_by_name(commander_name) or None
        except Exception:
            commander_oracle_id = None
    if commander_oracle_id and not commander_name:
        try:
            prints = sc.prints_for_oracle(commander_oracle_id) or []
        except Exception:
            prints = []
        if prints:
            commander_name = (prints[0].get("name") or "").strip() or commander_name

    if not commander_oracle_id and not commander_name:
        flash("Set a commander before refreshing EDHREC data.", "warning")
        return redirect(url_for("views.folder_detail", folder_id=folder.id))

    tag_label = resolve_deck_tag_from_slug(folder.deck_tag) if folder.deck_tag else None
    tags = [tag_label] if tag_label else []

    def _runner():
        from app import create_app

        app = create_app()
        with app.app_context():
            try:
                ingest_commander_tag_data(
                    commander_oracle_id or "",
                    commander_name,
                    tags,
                    force_refresh=True,
                )
            except Exception:
                current_app.logger.error("Failed to refresh EDHREC data for folder %s.", folder.id, exc_info=True)

    thread = threading.Thread(
        target=_runner,
        name=f"folder-edhrec-{folder.id}",
        daemon=True,
    )
    thread.start()

    flash("EDHREC refresh queued. Reload in a moment to see updates.", "info")
    return redirect(url_for("views.folder_detail", folder_id=folder.id))


def folder_cards_json(folder_id: int):
    folder = get_or_404(Folder, folder_id)
    ensure_folder_access(folder, write=False)
    cards = (
        Card.query.filter_by(folder_id=folder.id)
        .order_by(Card.name.asc(), Card.set_code.asc(), Card.collector_number.asc())
        .all()
    )
    payload = [
        {
            "id": card.id,
            "name": card.name,
            "oracle_id": card.oracle_id,
            "set_code": (card.set_code or "").lower(),
            "collector_number": card.collector_number or "",
            "lang": (card.lang or "en").lower(),
            "is_foil": bool(card.is_foil),
            "quantity": card.quantity or 1,
        }
        for card in cards
    ]
    return jsonify(payload)


def folder_counts(folder_id: int):
    """Return lightweight unique/quantity counts for a folder."""
    folder = get_or_404(Folder, folder_id)
    ensure_folder_access(folder, write=False, allow_shared=True)
    unique_count, total_qty = (
        db.session.query(func.count(Card.id), func.coalesce(func.sum(Card.quantity), 0))
        .filter(Card.folder_id == folder_id)
        .one()
    )
    return jsonify({"ok": True, "unique": int(unique_count or 0), "total": int(total_qty or 0)})


__all__ = [
    "clear_folder_tag",
    "folder_cards_json",
    "folder_counts",
    "refresh_folder_edhrec",
    "rename_proxy_deck",
    "set_folder_owner",
    "set_folder_proxy",
    "set_folder_tag",
]
