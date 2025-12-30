"""Build session workflow helpers (proxy-only)."""

from __future__ import annotations

from typing import Iterable

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from extensions import db
from models import BuildSession, BuildSessionCard
from services import scryfall_cache as sc
from services.build_recommendation_service import get_edhrec_recommendations


def build_session_page(session_id: int):
    session = _get_session(session_id)
    if session is None:
        abort(404)

    tags = _normalized_tags(session.tags_json)
    commander = _oracle_payload(session.commander_oracle_id, fallback=session.commander_name)
    recommendations = get_edhrec_recommendations(session.commander_oracle_id or "", tags)
    cards = _session_cards(session.cards or [])

    return render_template(
        "decks/build_session.html",
        session=session,
        commander=commander,
        tags=tags,
        recommendations=recommendations,
        session_cards=cards,
    )


def start_build_session():
    commander_oracle_id = (request.form.get("commander_oracle_id") or "").strip()
    commander_name = (request.form.get("commander_name") or "").strip()
    tags = _normalized_tags(_collect_tags_from_request())
    if not commander_oracle_id and commander_name:
        try:
            sc.ensure_cache_loaded()
            commander_oracle_id = sc.unique_oracle_by_name(commander_name) or ""
        except Exception:
            commander_oracle_id = ""
        if commander_oracle_id:
            commander_name = _oracle_name(commander_oracle_id) or commander_name
    if not commander_oracle_id:
        flash("Commander not found. Please check the name and try again.", "warning")
        return redirect(url_for("views.build_landing"))

    commander_name = commander_name or _oracle_name(commander_oracle_id)
    session = BuildSession(
        owner_user_id=current_user.id,
        commander_oracle_id=commander_oracle_id,
        commander_name=commander_name,
        tags_json=tags or None,
    )
    db.session.add(session)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash("Unable to start build session. Please try again.", "danger")
        return redirect(url_for("views.build_landing"))

    return redirect(url_for("views.build_session", session_id=session.id))


def add_card(session_id: int):
    session = _get_session(session_id)
    if session is None:
        abort(404)
    oracle_id = (request.form.get("card_oracle_id") or "").strip()
    if not oracle_id:
        return redirect(url_for("views.build_session", session_id=session_id))

    entry = BuildSessionCard.query.filter_by(session_id=session.id, card_oracle_id=oracle_id).first()
    if entry:
        entry.quantity = int(entry.quantity or 0) + 1
    else:
        entry = BuildSessionCard(session_id=session.id, card_oracle_id=oracle_id, quantity=1)
        db.session.add(entry)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash("Unable to add card to the build session.", "danger")
    return redirect(url_for("views.build_session", session_id=session_id))


def remove_card(session_id: int):
    session = _get_session(session_id)
    if session is None:
        abort(404)
    oracle_id = (request.form.get("card_oracle_id") or "").strip()
    if not oracle_id:
        return redirect(url_for("views.build_session", session_id=session_id))

    entry = BuildSessionCard.query.filter_by(session_id=session.id, card_oracle_id=oracle_id).first()
    if not entry:
        return redirect(url_for("views.build_session", session_id=session_id))
    if (entry.quantity or 0) > 1:
        entry.quantity = int(entry.quantity or 0) - 1
    else:
        db.session.delete(entry)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash("Unable to remove card from the build session.", "danger")
    return redirect(url_for("views.build_session", session_id=session_id))


def _get_session(session_id: int) -> BuildSession | None:
    if not current_user.is_authenticated:
        return None
    return (
        BuildSession.query.filter_by(id=session_id, owner_user_id=current_user.id)
        .first()
    )


def _collect_tags_from_request() -> list[str]:
    tags = []
    for value in request.form.getlist("deck_tags"):
        if value:
            tags.append(value)
    single = (request.form.get("deck_tag") or "").strip()
    if single:
        tags.append(single)
    return tags


def _normalized_tags(tags: Iterable[str] | None) -> list[str]:
    if isinstance(tags, str):
        tags = [tags]
    normalized: list[str] = []
    seen: set[str] = set()
    for tag in tags or []:
        label = (tag or "").strip()
        if not label:
            continue
        key = label.casefold()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(label)
    return normalized


def _oracle_name(oracle_id: str) -> str | None:
    if not oracle_id:
        return None
    try:
        sc.ensure_cache_loaded()
        prints = sc.prints_for_oracle(oracle_id) or []
    except Exception:
        return None
    if not prints:
        return None
    return (prints[0].get("name") or "").strip() or None


def _oracle_payload(oracle_id: str | None, *, fallback: str | None = None) -> dict:
    if not oracle_id:
        return {"oracle_id": None, "name": fallback or "", "image": None}
    try:
        sc.ensure_cache_loaded()
        prints = sc.prints_for_oracle(oracle_id) or []
    except Exception:
        prints = []
    name = fallback or oracle_id
    image = None
    if prints:
        pr = prints[0]
        name = (pr.get("name") or "").strip() or name
        image_uris = pr.get("image_uris") or {}
        if not image_uris:
            faces = pr.get("card_faces") or []
            if faces:
                image_uris = (faces[0] or {}).get("image_uris") or {}
        image = image_uris.get("normal") or image_uris.get("large") or image_uris.get("small")
    return {"oracle_id": oracle_id, "name": name, "image": image}


def _session_cards(entries: Iterable[BuildSessionCard]) -> list[dict]:
    cards: list[dict] = []
    for entry in entries:
        oracle_id = (entry.card_oracle_id or "").strip()
        if not oracle_id:
            continue
        payload = _oracle_payload(oracle_id)
        cards.append(
            {
                "oracle_id": oracle_id,
                "name": payload["name"],
                "image": payload["image"],
                "quantity": int(entry.quantity or 0),
            }
        )
    cards.sort(key=lambda item: (item["name"].casefold(), item["oracle_id"]))
    return cards


__all__ = [
    "add_card",
    "build_session_page",
    "remove_card",
    "start_build_session",
]
