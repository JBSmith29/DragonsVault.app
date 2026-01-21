"""Landing page helpers for Build-A-Deck discovery."""

from __future__ import annotations

from flask import render_template, request
from flask_login import current_user
from sqlalchemy import func

from extensions import db
from models import BuildSession, BuildSessionCard, Card, EdhrecCommanderTagCard, Folder, FolderRole
from core.domains.cards.services import scryfall_cache as sc
from core.domains.decks.services.commander_brackets import BRACKET_RULESET_EPOCH, evaluate_commander_bracket, spellbook_dataset_epoch
from core.domains.decks.services.commander_cache import compute_bracket_signature
from core.domains.decks.services.deck_tags import get_deck_tag_groups
from core.domains.decks.services.edhrec_cache_service import cache_ready
from shared.cache.request_cache import request_cached
from core.routes.base import color_identity_name


def build_landing_page():
    selected_tag = (request.args.get("tag") or "").strip()
    user_id = current_user.id if current_user.is_authenticated else None
    context = build_landing_context(user_id, selected_tag or None)
    return render_template("decks/build_landing.html", **context)


def build_landing_context(user_id: int | None, selected_tag: str | None) -> dict:
    tag_groups = get_deck_tag_groups()
    edhrec_ready = cache_ready()
    collection_oracles = _collection_oracle_subquery(user_id) if user_id else None
    recommended_tags: list[dict] = []
    tag_commanders: list[dict] = []
    current_builds: list[dict] = []

    if edhrec_ready and collection_oracles is not None:
        recommended_tags = _collection_tag_fits(collection_oracles)
        if selected_tag:
            tag_commanders = _collection_tag_commanders(collection_oracles, selected_tag)

    if user_id:
        current_builds = _current_builds(user_id)

    return {
        "edhrec_ready": edhrec_ready,
        "tag_groups": tag_groups,
        "recommended_tags": recommended_tags,
        "tag_commanders": tag_commanders,
        "current_builds": current_builds,
        "selected_tag": selected_tag or "",
    }


def _collection_oracle_subquery(user_id: int):
    return (
        db.session.query(Card.oracle_id.label("oracle_id"))
        .join(Folder, Card.folder_id == Folder.id)
        .join(FolderRole, FolderRole.folder_id == Folder.id)
        .filter(
            FolderRole.role == FolderRole.ROLE_COLLECTION,
            Folder.owner_user_id == user_id,
            Card.oracle_id.isnot(None),
        )
        .distinct()
        .subquery()
    )


def _collection_tag_fits(collection_oracles) -> list[dict]:
    rows = (
        db.session.query(
            EdhrecCommanderTagCard.tag,
            func.count(EdhrecCommanderTagCard.card_oracle_id).label("owned_count"),
        )
        .join(collection_oracles, EdhrecCommanderTagCard.card_oracle_id == collection_oracles.c.oracle_id)
        .group_by(EdhrecCommanderTagCard.tag)
        .order_by(func.count(EdhrecCommanderTagCard.card_oracle_id).desc())
        .limit(12)
        .all()
    )
    return [
        {"tag": row.tag, "owned_count": int(row.owned_count or 0)}
        for row in rows
        if row.tag
    ]


def _collection_tag_commanders(collection_oracles, tag: str) -> list[dict]:
    rows = (
        db.session.query(
            EdhrecCommanderTagCard.commander_oracle_id,
            func.count(EdhrecCommanderTagCard.card_oracle_id).label("owned_count"),
        )
        .join(collection_oracles, EdhrecCommanderTagCard.card_oracle_id == collection_oracles.c.oracle_id)
        .filter(EdhrecCommanderTagCard.tag == tag)
        .group_by(EdhrecCommanderTagCard.commander_oracle_id)
        .order_by(func.count(EdhrecCommanderTagCard.card_oracle_id).desc())
        .limit(12)
        .all()
    )
    return [
        {
            "oracle_id": row.commander_oracle_id,
            "name": _oracle_name(row.commander_oracle_id) or row.commander_oracle_id,
            "image": _oracle_image(row.commander_oracle_id),
            "owned_count": int(row.owned_count or 0),
        }
        for row in rows
        if row.commander_oracle_id
    ]


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


def _oracle_image(oracle_id: str) -> str | None:
    if not oracle_id:
        return None
    try:
        sc.ensure_cache_loaded()
        prints = sc.prints_for_oracle(oracle_id) or []
    except Exception:
        return None
    if not prints:
        return None
    pr = prints[0]
    image_uris = pr.get("image_uris") or {}
    if not image_uris:
        faces = pr.get("card_faces") or []
        if faces:
            image_uris = (faces[0] or {}).get("image_uris") or {}
    return image_uris.get("normal") or image_uris.get("large") or image_uris.get("small")


def _oracle_colors(oracle_id: str) -> list[str]:
    if not oracle_id:
        return []
    try:
        sc.ensure_cache_loaded()
        prints = sc.prints_for_oracle(oracle_id) or []
    except Exception:
        return []
    if not prints:
        return []
    pr = prints[0]
    return pr.get("color_identity") or pr.get("colors") or []


def _oracle_color_label(oracle_id: str) -> str:
    letters, _ = sc.normalize_color_identity(_oracle_colors(oracle_id))
    return color_identity_name(letters)


def _oracle_detail(oracle_id: str, cache: dict[str, dict]) -> dict:
    cached = cache.get(oracle_id)
    if cached is not None:
        return cached
    payload = {"type_line": "", "cmc": None, "mana_costs": [], "oracle_text": ""}
    try:
        sc.ensure_cache_loaded()
        prints = sc.prints_for_oracle(oracle_id) or []
    except Exception:
        cache[oracle_id] = payload
        return payload
    if not prints:
        cache[oracle_id] = payload
        return payload
    pr = prints[0]
    payload["type_line"] = pr.get("type_line") or ""
    payload["cmc"] = pr.get("cmc")
    payload["mana_costs"] = _mana_costs_from_faces(pr)
    payload["oracle_text"] = _oracle_text_from_faces(pr)
    cache[oracle_id] = payload
    return payload


def _mana_costs_from_faces(print_obj: dict) -> list[str]:
    faces = print_obj.get("card_faces") or []
    face_costs: list[str] = []
    for face in faces:
        if not isinstance(face, dict):
            continue
        face_cost = face.get("mana_cost")
        if face_cost:
            face_costs.append(str(face_cost))
    if face_costs:
        return [cost for cost in face_costs if cost]
    mana_cost = print_obj.get("mana_cost")
    if mana_cost:
        return [str(mana_cost)]
    return []


def _oracle_text_from_faces(print_obj: dict) -> str:
    texts: list[str] = []
    oracle_text = print_obj.get("oracle_text")
    if oracle_text:
        texts.append(str(oracle_text))
    faces = print_obj.get("card_faces") or []
    for face in faces:
        if not isinstance(face, dict):
            continue
        face_text = face.get("oracle_text")
        if face_text:
            texts.append(str(face_text))
    return " // ".join([t for t in texts if t])


def _build_session_bracket_context(
    session_id: int,
    commander_oracle_id: str,
    commander_name: str | None,
    cards: list[BuildSessionCard],
) -> dict:
    detail_cache: dict[str, dict] = {}
    bracket_cards: list[dict[str, object]] = []
    for entry in cards or []:
        oracle_id = (entry.card_oracle_id or "").strip()
        if not oracle_id:
            continue
        qty = int(entry.quantity or 0)
        if qty <= 0:
            continue
        detail = _oracle_detail(oracle_id, detail_cache)
        costs = [cost for cost in (detail.get("mana_costs") or []) if cost]
        bracket_cards.append(
            {
                "name": _oracle_name(oracle_id) or oracle_id,
                "type_line": detail.get("type_line") or "",
                "oracle_text": detail.get("oracle_text") or "",
                "mana_value": detail.get("cmc"),
                "quantity": qty,
                "mana_cost": " // ".join(costs) if costs else None,
                "produced_mana": None,
            }
        )
    commander_stub = {
        "oracle_id": commander_oracle_id,
        "name": commander_name or _oracle_name(commander_oracle_id) or commander_oracle_id,
    }
    epoch = sc.cache_epoch() + BRACKET_RULESET_EPOCH + spellbook_dataset_epoch()
    signature = compute_bracket_signature(bracket_cards, commander_stub, epoch=epoch)
    cache_key = ("build_landing_bracket", session_id, signature, epoch)
    commander_ctx = request_cached(
        cache_key,
        lambda: evaluate_commander_bracket(bracket_cards, commander_stub),
    )
    return commander_ctx or {}


def _current_builds(user_id: int) -> list[dict]:
    rows = (
        db.session.query(
            BuildSession.id,
            BuildSession.commander_oracle_id,
            BuildSession.commander_name,
            BuildSession.build_name,
            BuildSession.tags_json,
            BuildSession.updated_at,
            BuildSession.created_at,
            func.coalesce(func.sum(BuildSessionCard.quantity), 0).label("card_count"),
        )
        .outerjoin(BuildSessionCard, BuildSessionCard.session_id == BuildSession.id)
        .filter(BuildSession.owner_user_id == user_id, BuildSession.status == "active")
        .group_by(BuildSession.id)
        .order_by(BuildSession.updated_at.desc().nullslast(), BuildSession.created_at.desc())
        .limit(12)
        .all()
    )
    session_ids = [row.id for row in rows]
    cards_by_session: dict[int, list[BuildSessionCard]] = {}
    if session_ids:
        card_rows = (
            BuildSessionCard.query.filter(BuildSessionCard.session_id.in_(session_ids))
            .order_by(BuildSessionCard.session_id.asc())
            .all()
        )
        for card in card_rows:
            cards_by_session.setdefault(card.session_id, []).append(card)

    builds: list[dict] = []
    for row in rows:
        oracle_id = (row.commander_oracle_id or "").strip()
        commander_name = (row.commander_name or "").strip() or _oracle_name(oracle_id) or "unknown commander"
        updated = row.updated_at or row.created_at
        updated_label = updated.strftime("%Y-%m-%d") if updated else ""
        bracket_ctx = _build_session_bracket_context(row.id, oracle_id, commander_name, cards_by_session.get(row.id, []))
        builds.append(
            {
                "id": row.id,
                "build_name": (row.build_name or "").strip() or None,
                "commander_name": commander_name,
                "image": _oracle_image(oracle_id),
                "colors": _oracle_colors(oracle_id),
                "color_label": _oracle_color_label(oracle_id),
                "tags": _normalized_tags(row.tags_json),
                "card_count": int(row.card_count or 0),
                "updated_label": updated_label,
                "bracket_level": bracket_ctx.get("level"),
                "bracket_label": bracket_ctx.get("label"),
                "bracket_score": bracket_ctx.get("score"),
            }
        )
    return builds


def _normalized_tags(tags) -> list[str]:
    if not tags:
        return []
    if isinstance(tags, str):
        items = [tags]
    else:
        items = list(tags)
    seen: set[str] = set()
    output: list[str] = []
    for tag in items:
        label = (tag or "").strip()
        if not label:
            continue
        key = label.casefold()
        if key in seen:
            continue
        seen.add(key)
        output.append(label)
    return output


__all__ = ["build_landing_context", "build_landing_page"]
