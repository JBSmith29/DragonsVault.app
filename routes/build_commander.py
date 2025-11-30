"""Commander recommendation route."""

from __future__ import annotations

from flask import render_template, session, request, flash, redirect, url_for
from sqlalchemy import func
from sqlalchemy.orm import load_only
from sqlalchemy.exc import IntegrityError

from extensions import db  # noqa: F401  # kept for parity with other route modules
from models import Card, Folder
from services.scryfall_cache import cache_epoch, ensure_cache_loaded
from flask_login import current_user

from .base import _bulk_print_lookup, _collection_metadata, views
from utils.commander_recommendations import recommend_commanders, recommend_deck_for_commander
from routes.build import _generate_unique_folder_name


@views.route("/build-commander/recommend")
def commander_recommend():
    """Recommend commanders based on the user's owned cards."""
    ensure_cache_loaded()
    collection_ids, _, collection_lower = _collection_metadata()

    query = (
        Card.query.options(
            load_only(
                Card.id,
                Card.name,
                Card.set_code,
                Card.collector_number,
                Card.oracle_id,
                Card.type_line,
                Card.quantity,
                Card.color_identity_mask,
                Card.lang,
                Card.is_proxy,
                Card.rarity,
            )
        )
        .join(Folder, Card.folder_id == Folder.id)
        .filter(Card.is_proxy.is_(False))
    )
    if collection_ids:
        query = query.filter(Card.folder_id.in_(collection_ids))
    elif collection_lower:
        query = query.filter(func.lower(Folder.name).in_(collection_lower))
    else:
        query = query.filter(func.coalesce(Folder.category, Folder.CATEGORY_DECK) == Folder.CATEGORY_COLLECTION)

    user_cards = query.order_by(func.lower(Card.name)).all()
    prints_map = _bulk_print_lookup(
        user_cards,
        cache_key=f"cmdr-rec:{session.get('_user_id') or 'anon'}",
        epoch=cache_epoch(),
    )
    recommendations = recommend_commanders(user_cards, prints_map=prints_map)
    recommended_sorted = sorted(recommendations, key=lambda r: r.get("synergy_score", 0), reverse=True)
    all_commanders = sorted(recommendations, key=lambda r: (r.get("name") or "").lower())
    return render_template(
        "build_commander_recommend.html",
        recommendations=recommended_sorted,
        all_commanders=all_commanders,
    )


@views.route("/build-commander/recommend/build", methods=["POST"])
def build_recommended_deck():
    """Create a full build deck from owned cards for the chosen commander."""
    commander_name = (request.form.get("commander_name") or "").strip()
    if not commander_name:
        flash("Commander name missing.", "warning")
        return redirect(url_for("views.commander_recommend"))

    ensure_cache_loaded()
    collection_ids, _, collection_lower = _collection_metadata()
    query = (
        Card.query.options(
            load_only(
                Card.id,
                Card.name,
                Card.set_code,
                Card.collector_number,
                Card.oracle_id,
                Card.type_line,
                Card.quantity,
                Card.color_identity_mask,
                Card.lang,
                Card.is_proxy,
                Card.rarity,
            )
        )
        .join(Folder, Card.folder_id == Folder.id)
        .filter(Card.is_proxy.is_(False))
    )
    if collection_ids:
        query = query.filter(Card.folder_id.in_(collection_ids))
    elif collection_lower:
        query = query.filter(func.lower(Folder.name).in_(collection_lower))
    else:
        query = query.filter(func.coalesce(Folder.category, Folder.CATEGORY_DECK) == Folder.CATEGORY_COLLECTION)

    user_cards = query.order_by(func.lower(Card.name)).all()
    prints_map = _bulk_print_lookup(
        user_cards,
        cache_key=f"cmdr-rec:{session.get('_user_id') or 'anon'}",
        epoch=cache_epoch(),
    )
    recommendations = recommend_commanders(user_cards, prints_map=prints_map)
    picked = None
    for rec in recommendations:
        if (rec.get("name") or "").strip().lower() == commander_name.lower():
            picked = rec
            break
    if not picked:
        flash("Commander not found in your collection.", "warning")
        return redirect(url_for("views.commander_recommend"))

    deck_cards = recommend_deck_for_commander(picked, user_cards, prints_map=prints_map, deck_size=100)
    if not deck_cards:
        flash("Could not build a deck from your collection for this commander.", "warning")
        return redirect(url_for("views.commander_recommend"))

    owner_name = None
    owner_user_id = None
    if current_user and getattr(current_user, "is_authenticated", False):
        owner_user_id = current_user.id
        owner_name = (getattr(current_user, "username", None) or getattr(current_user, "email", None) or "").strip() or None

    base_name = f"Proxy: {picked.get('name')} (Rec)"
    attempt = 0
    folder = None
    while True:
        candidate_name = base_name if attempt == 0 else f"{base_name} ({attempt + 1})"
        folder = Folder(
            name=candidate_name,
            category=Folder.CATEGORY_DECK,
            commander_name=picked.get("name"),
            commander_oracle_id=picked.get("card", {}).oracle_id if picked.get("card") else None,
            owner=owner_name,
            owner_user_id=owner_user_id,
            is_proxy=True,
        )
        db.session.add(folder)
        try:
            db.session.flush()
            break
        except IntegrityError:
            db.session.rollback()
            attempt += 1
            if attempt >= 5:
                flash("Could not generate a unique build name. Please try again.", "danger")
                return redirect(url_for("views.commander_recommend"))

    for entry in deck_cards:
        db.session.add(
            Card(
                name=entry.get("name") or "",
                set_code=entry.get("set_code") or "CSTM",
                collector_number=str(entry.get("collector_number") or "0"),
                folder_id=folder.id,
                oracle_id=entry.get("oracle_id"),
                lang=entry.get("lang") or "en",
                is_foil=False,
                quantity=max(int(entry.get("quantity") or 1), 1),
                is_proxy=True,
            )
        )

    db.session.commit()
    flash(f"Created recommended proxy deck for {picked.get('name')} with {len(deck_cards)} cards.", "success")
    return redirect(url_for("views.folder_detail", folder_id=folder.id))
