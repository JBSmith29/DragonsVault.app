"""Landing page helpers for Build-A-Deck discovery."""

from __future__ import annotations

from typing import Iterable

from flask import render_template, request
from flask_login import current_user
from sqlalchemy import func

from extensions import db
from models import (
    Card,
    EdhrecCommanderCard,
    EdhrecCommanderTag,
    EdhrecCommanderTagCard,
    Folder,
    FolderRole,
)
from services import scryfall_cache as sc
from services.deck_tags import get_deck_tag_groups
from services.edhrec_cache_service import cache_ready


def build_landing_page():
    selected_tag = (request.args.get("tag") or "").strip()
    user_id = current_user.id if current_user.is_authenticated else None
    context = build_landing_context(user_id, selected_tag or None)
    return render_template("decks/build_landing.html", **context)


def build_landing_context(user_id: int | None, selected_tag: str | None) -> dict:
    tag_groups = get_deck_tag_groups()
    edhrec_ready = cache_ready()
    collection_oracles = _collection_oracle_subquery(user_id) if user_id else None
    recommended_commanders: list[dict] = []
    recommended_tags: list[dict] = []
    tag_commanders: list[dict] = []

    if edhrec_ready and collection_oracles is not None:
        recommended_commanders = _collection_commander_fits(collection_oracles)
        recommended_tags = _collection_tag_fits(collection_oracles)
        if selected_tag:
            tag_commanders = _collection_tag_commanders(collection_oracles, selected_tag)

    return {
        "edhrec_ready": edhrec_ready,
        "tag_groups": tag_groups,
        "recommended_commanders": recommended_commanders,
        "recommended_tags": recommended_tags,
        "tag_commanders": tag_commanders,
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


def _collection_commander_fits(collection_oracles) -> list[dict]:
    rows = (
        db.session.query(
            EdhrecCommanderCard.commander_oracle_id,
            func.count(EdhrecCommanderCard.card_oracle_id).label("owned_count"),
        )
        .join(collection_oracles, EdhrecCommanderCard.card_oracle_id == collection_oracles.c.oracle_id)
        .group_by(EdhrecCommanderCard.commander_oracle_id)
        .order_by(func.count(EdhrecCommanderCard.card_oracle_id).desc())
        .limit(12)
        .all()
    )
    commander_ids = [row.commander_oracle_id for row in rows if row.commander_oracle_id]
    tag_map = _commander_tag_map(commander_ids)

    return [
        {
            "oracle_id": row.commander_oracle_id,
            "name": _oracle_name(row.commander_oracle_id) or row.commander_oracle_id,
            "image": _oracle_image(row.commander_oracle_id, size="small"),
            "owned_count": int(row.owned_count or 0),
            "tags": tag_map.get(row.commander_oracle_id, [])[:3],
        }
        for row in rows
        if row.commander_oracle_id
    ]


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


def _commander_tag_map(commander_ids: Iterable[str]) -> dict[str, list[str]]:
    if not commander_ids:
        return {}
    rows = (
        EdhrecCommanderTag.query.filter(EdhrecCommanderTag.commander_oracle_id.in_(commander_ids))
        .order_by(EdhrecCommanderTag.tag.asc())
        .all()
    )
    tag_map: dict[str, list[str]] = {}
    for row in rows:
        if not row.tag:
            continue
        tag_map.setdefault(row.commander_oracle_id, []).append(row.tag)
    return tag_map


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


def _oracle_image(oracle_id: str, *, size: str = "normal") -> str | None:
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
    preferred = (size or "normal").lower()
    if preferred == "small":
        return image_uris.get("small") or image_uris.get("normal") or image_uris.get("large")
    if preferred == "large":
        return image_uris.get("large") or image_uris.get("normal") or image_uris.get("small")
    return image_uris.get("normal") or image_uris.get("large") or image_uris.get("small")


__all__ = ["build_landing_context", "build_landing_page"]
