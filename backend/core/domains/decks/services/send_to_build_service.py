"""Transfer a saved deck folder into a new build session."""

from __future__ import annotations

from collections import Counter
from typing import Any, Optional

from flask import current_app, flash, redirect, request, url_for
from flask_login import current_user
from sqlalchemy import func

from extensions import db
from models import BuildSession, BuildSessionCard, Card, Folder
from core.domains.cards.services import scryfall_cache as sc
from core.domains.cards.services.scryfall_cache import find_by_set_cn
from core.domains.decks.services.build_session_service import ensure_build_session_tables
from core.domains.decks.services.commander_utils import (
    primary_commander_name,
    primary_commander_oracle_id,
)
from shared.auth import ensure_folder_access
from shared.database import get_or_404


def send_to_build(folder_id: int):
    folder = get_or_404(Folder, folder_id)
    ensure_folder_access(folder, write=True)
    if folder.is_collection:
        flash("Collection folders cannot be sent to Build-A-Deck.", "warning")
        return redirect(url_for("views.folder_detail", folder_id=folder.id))

    extra_oracles = [str(val or "").strip() for val in request.form.getlist("card_oracle_id") if str(val or "").strip()]
    extra_counts = Counter(extra_oracles)

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

    ensure_build_session_tables()
    tags = [folder.deck_tag] if folder.deck_tag else []
    session = BuildSession(
        owner_user_id=current_user.id,
        commander_oracle_id=commander_oracle_id,
        commander_name=commander_name,
        build_name=folder.name,
        tags_json=tags or None,
    )
    db.session.add(session)
    db.session.flush()

    def _normalize_oracle_id(value: Any) -> str:
        return str(value or "").strip()

    entry_map: dict[str, BuildSessionCard] = {}
    added = 0

    def _add_card_to_session(oracle_id: str, quantity: int) -> None:
        nonlocal added
        normalized_oracle_id = _normalize_oracle_id(oracle_id)
        qty_val = int(quantity or 0)
        if not normalized_oracle_id or qty_val <= 0:
            return
        key = normalized_oracle_id.casefold()
        existing = entry_map.get(key)
        if existing:
            existing.quantity = int(existing.quantity or 0) + qty_val
        else:
            existing = BuildSessionCard(
                session_id=session.id,
                card_oracle_id=normalized_oracle_id,
                quantity=qty_val,
            )
            db.session.add(existing)
            entry_map[key] = existing
        added += qty_val

    resolution_cache: dict[tuple[str, str, str], Optional[str]] = {}
    unresolved_qty = 0
    cache_loaded = False

    def _resolve_oracle_id(
        oracle_id: Any,
        set_code: Any,
        collector_number: Any,
        card_name: Any,
    ) -> Optional[str]:
        nonlocal cache_loaded
        direct = _normalize_oracle_id(oracle_id)
        if direct:
            return direct

        name_text = str(card_name or "").strip()
        if not name_text:
            return None

        key = (
            str(set_code or "").strip().casefold(),
            str(collector_number or "").strip().casefold(),
            name_text.casefold(),
        )
        if key in resolution_cache:
            return resolution_cache[key]

        if not cache_loaded:
            try:
                sc.ensure_cache_loaded()
            except Exception:
                pass
            cache_loaded = True

        resolved: Optional[str] = None
        try:
            found = find_by_set_cn(set_code, collector_number, name_text)
            if isinstance(found, dict):
                resolved = _normalize_oracle_id(found.get("oracle_id")) or None
        except Exception:
            resolved = None

        if not resolved:
            try:
                resolved = _normalize_oracle_id(sc.unique_oracle_by_name(name_text)) or None
            except Exception:
                resolved = None

        resolution_cache[key] = resolved
        return resolved

    rows = (
        db.session.query(
            Card.oracle_id,
            Card.set_code,
            Card.collector_number,
            Card.name,
            func.coalesce(func.sum(Card.quantity), 0),
        )
        .filter(Card.folder_id == folder.id)
        .group_by(Card.oracle_id, Card.set_code, Card.collector_number, Card.name)
        .all()
    )

    for oracle_id, set_code, collector_number, card_name, qty in rows:
        qty = int(qty or 0)
        if qty <= 0:
            continue
        resolved_oracle_id = _resolve_oracle_id(oracle_id, set_code, collector_number, card_name)
        if not resolved_oracle_id:
            unresolved_qty += qty
            continue
        _add_card_to_session(resolved_oracle_id, qty)

    commander_key = _normalize_oracle_id(commander_oracle_id).casefold()
    if commander_key and commander_key not in entry_map:
        _add_card_to_session(str(commander_oracle_id), 1)

    if extra_counts:
        for oracle_id, qty in extra_counts.items():
            _add_card_to_session(oracle_id, int(qty))

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.error("Failed to send deck to build session.", exc_info=True)
        flash("Unable to send this deck to Build-A-Deck.", "danger")
        return redirect(url_for("views.folder_detail", folder_id=folder.id))

    if added > 0:
        flash(f"Build session created with {added} cards.", "success")
    else:
        flash("Build session created, but no cards could be added from this deck.", "warning")

    if unresolved_qty > 0:
        noun = "card was" if unresolved_qty == 1 else "cards were"
        flash(
            f"{unresolved_qty} deck {noun} skipped because Oracle IDs could not be resolved.",
            "warning",
        )
    return redirect(url_for("views.build_session", session_id=session.id))


__all__ = ["send_to_build"]
