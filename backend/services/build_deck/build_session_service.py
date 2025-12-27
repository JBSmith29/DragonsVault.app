"""Build session orchestration for Build-A-Deck."""

from __future__ import annotations

import logging
from typing import Iterable

from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError

from flask import has_request_context
from flask_login import current_user

from extensions import db
from models import Card, DeckBuildSession, Folder, OracleCoreRoleTag
from services import scryfall_cache as sc
from services.commander_utils import split_commander_oracle_ids
from services.deck_service import get_deck_stats, recompute_deck_stats
from services.deck_tags import is_valid_deck_tag
from services.symbols_cache import colors_to_icons
from . import build_constraints_service as constraints
from .build_recommendation_service import get_build_recommendations

_LOG = logging.getLogger(__name__)

_TYPE_GROUPS = [
    ("Creatures", "Creature"),
    ("Instants", "Instant"),
    ("Sorceries", "Sorcery"),
    ("Artifacts", "Artifact"),
    ("Enchantments", "Enchantment"),
    ("Planeswalkers", "Planeswalker"),
    ("Lands", "Land"),
    ("Battles", "Battle"),
]

_ROLE_TARGETS = {
    "ramp": 8,
    "draw": 8,
    "removal": 6,
    "wipe": 2,
}


def _normalize_tags(tags: Iterable[str] | None) -> list[str]:
    if not tags:
        return []
    seen: set[str] = set()
    cleaned: list[str] = []
    for tag in tags:
        label = (tag or "").strip()
        if not label:
            continue
        key = label.casefold()
        if key in seen:
            continue
        seen.add(key)
        if not is_valid_deck_tag(label):
            _LOG.info("Skipping unknown deck tag: %s", label)
            continue
        cleaned.append(label)
    return cleaned


def _folder_name_exists(name: str) -> bool:
    return bool(
        db.session.query(Folder.id).filter(func.lower(Folder.name) == name.lower()).first()
    )


def _unique_folder_name(base: str) -> str:
    candidate = base
    suffix = 2
    while _folder_name_exists(candidate):
        candidate = f"{base} ({suffix})"
        suffix += 1
    return candidate


def _preferred_print(oracle_id: str) -> dict | None:
    prints = sc.prints_for_oracle(oracle_id) or ()
    if not prints:
        return None
    for pr in prints:
        if pr.get("digital"):
            continue
        if (pr.get("lang") or "en").lower() == "en":
            return pr
    return prints[0]


def _type_group_label(type_line: str) -> str:
    lowered = (type_line or "").lower()
    for label, token in _TYPE_GROUPS:
        if token.lower() in lowered:
            return label
    return "Other"


def _group_by_type(items: list[dict]) -> list[dict]:
    group_map: dict[str, list[dict]] = {label: [] for label, _ in _TYPE_GROUPS}
    other: list[dict] = []
    for item in items:
        label = item.get("type_group") or "Other"
        if label in group_map:
            group_map[label].append(item)
        else:
            other.append(item)
    groups: list[dict] = []
    for label, _ in _TYPE_GROUPS:
        cards = group_map.get(label, [])
        groups.append({"label": label, "cards": cards, "count": len(cards)})
    if other:
        groups.append({"label": "Other", "cards": other, "count": len(other)})
    return groups


def _commander_image(commander_oracle_id: str | None) -> str | None:
    if not commander_oracle_id:
        return None
    for oid in split_commander_oracle_ids(commander_oracle_id):
        pr = _preferred_print(oid)
        if not pr:
            continue
        image = sc.image_for_print(pr)
        for key in ("normal", "large", "small"):
            url = image.get(key)
            if url:
                return url
    return None


def _commander_color_identity(commander_oracle_id: str | None) -> list[str]:
    colors: set[str] = set()
    for oid in split_commander_oracle_ids(commander_oracle_id):
        pr = _preferred_print(oid)
        if not pr:
            continue
        meta = sc.metadata_from_print(pr)
        for color in meta.get("color_identity") or []:
            colors.add(str(color).upper())
    order = ["W", "U", "B", "R", "G"]
    return [c for c in order if c in colors]


def _deck_role_counts(deck_oracle_ids: set[str]) -> dict[str, int]:
    if not deck_oracle_ids:
        return {}
    rows = (
        db.session.query(OracleCoreRoleTag.role, func.count(OracleCoreRoleTag.oracle_id))
        .filter(OracleCoreRoleTag.oracle_id.in_(list(deck_oracle_ids)))
        .group_by(OracleCoreRoleTag.role)
        .all()
    )
    return {str(role).strip(): int(total or 0) for role, total in rows if role}


def _deck_health_hints(deck_oracle_ids: set[str]) -> list[str]:
    role_counts = _deck_role_counts(deck_oracle_ids)
    hints: list[str] = []
    for role, target in _ROLE_TARGETS.items():
        count = int(role_counts.get(role, 0) or 0)
        if count < target:
            hints.append(f"Low {role} ({count}/{target})")
    return hints


def _commander_names_from_oracle_ids(commander_oracle_id: str) -> str | None:
    names: list[str] = []
    for oid in split_commander_oracle_ids(commander_oracle_id):
        pr = _preferred_print(oid)
        if pr and pr.get("name"):
            names.append(pr.get("name"))
    if not names:
        return None
    return " // ".join(names)


def _add_card_to_folder(folder: Folder, card_oracle_id: str, *, quantity: int = 1) -> Card:
    existing = (
        Card.query.filter(Card.folder_id == folder.id, Card.oracle_id == card_oracle_id)
        .order_by(Card.id.asc())
        .first()
    )
    if existing:
        existing.quantity = int(existing.quantity or 0) + max(int(quantity), 1)
        db.session.add(existing)
        return existing

    pr = _preferred_print(card_oracle_id)
    metadata = sc.metadata_from_print(pr)
    name = None
    set_code = "CSTM"
    collector_number = "P000"
    lang = "en"
    is_foil = False
    if pr:
        name = pr.get("name")
        set_code = (pr.get("set") or "CSTM").upper()
        collector_number = str(pr.get("collector_number") or "P000")
        lang = (pr.get("lang") or "en").lower()

    card = Card(
        name=name or card_oracle_id,
        set_code=set_code,
        collector_number=collector_number,
        folder_id=folder.id,
        oracle_id=card_oracle_id,
        lang=lang,
        is_foil=is_foil,
        quantity=max(int(quantity), 1),
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
    db.session.add(card)
    return card


def start_build(commander_oracle_id: str, tags: list[str] | None = None) -> dict:
    """
    Create a build deck folder for a commander and return build metadata.
    """
    commander_oracle_id = (commander_oracle_id or "").strip()
    assert commander_oracle_id, "commander_oracle_id must exist"
    commander_oracle_id = ",".join(split_commander_oracle_ids(commander_oracle_id)) or commander_oracle_id

    try:
        sc.ensure_cache_loaded()
    except Exception as exc:
        _LOG.warning("Scryfall cache unavailable when starting build: %s", exc)

    legal, error = constraints.commander_is_legal(commander_oracle_id)
    if not legal:
        raise ValueError(error or "Commander is not legal.")

    commander_name = _commander_names_from_oracle_ids(commander_oracle_id)
    base_name = commander_name or "New Build"
    folder_name = _unique_folder_name(f"[Build] {base_name}")

    folder = Folder(
        name=folder_name,
        commander_oracle_id=commander_oracle_id,
        commander_name=commander_name,
    )
    if has_request_context() and getattr(current_user, "is_authenticated", False):
        folder.owner_user_id = current_user.id
        owner_label = (current_user.username or current_user.email or "").strip()
        folder.owner = owner_label or None
    folder.set_primary_role(Folder.CATEGORY_BUILD)
    db.session.add(folder)
    db.session.flush()

    normalized_tags = _normalize_tags(tags)
    session = DeckBuildSession(folder_id=folder.id, tags_json=normalized_tags or [])
    db.session.add(session)

    recompute_deck_stats(folder.id)
    try:
        db.session.commit()
    except SQLAlchemyError as exc:
        db.session.rollback()
        _LOG.error("Build deck creation failed: %s", exc)
        raise

    return {
        "ok": True,
        "folder_id": folder.id,
        "folder_name": folder.name,
        "commander_oracle_id": commander_oracle_id,
        "commander_name": commander_name,
        "tags": normalized_tags,
    }


def add_card_to_build(folder_id: int, card_oracle_id: str) -> None:
    """
    Add a card to a build deck, enforcing constraints.
    """
    card_oracle_id = (card_oracle_id or "").strip()
    assert card_oracle_id, "card_oracle_id must exist"

    folder = db.session.get(Folder, folder_id)
    if not folder or not folder.is_build:
        raise ValueError("Build deck not found.")

    try:
        sc.ensure_cache_loaded()
    except Exception as exc:
        _LOG.warning("Scryfall cache unavailable for build add: %s", exc)
        raise ValueError("Card cache unavailable; try again later.") from exc

    ok, message = constraints.enforce_color_identity(folder.commander_oracle_id, card_oracle_id)
    if not ok:
        raise ValueError(message or "Card is not legal for this commander.")

    deck_oracle_ids = {
        str(row[0]).strip()
        for row in db.session.query(Card.oracle_id).filter(Card.folder_id == folder_id).all()
        if row and row[0]
    }
    ok, message = constraints.validate_singleton(card_oracle_id, deck_oracle_ids)
    if not ok:
        raise ValueError(message or "Singleton rule violation.")

    try:
        _add_card_to_folder(folder, card_oracle_id, quantity=1)
        recompute_deck_stats(folder.id)
        db.session.commit()
    except SQLAlchemyError as exc:
        db.session.rollback()
        _LOG.error("Failed to add card to build deck: %s", exc)
        raise


def _load_build_tags(folder_id: int) -> list[str]:
    session = db.session.get(DeckBuildSession, folder_id)
    tags = session.tags_json if session else []
    if isinstance(tags, list):
        return [str(t) for t in tags if str(t).strip()]
    return []


def get_build_state(folder_id: int) -> dict:
    """
    Return the current build deck state with recommendations and stats.
    """
    folder = db.session.get(Folder, folder_id)
    if not folder:
        return {"ok": False, "error": "Build deck not found."}
    if not folder.is_build:
        return {"ok": False, "error": "Folder is not a build deck."}

    tags = _load_build_tags(folder_id)
    deck_cards = (
        db.session.query(Card.id, Card.name, Card.oracle_id, Card.quantity)
        .filter(Card.folder_id == folder_id)
        .all()
    )
    deck_card_map: dict[str, dict] = {}
    for row in deck_cards:
        oracle_id = (row.oracle_id or "").strip()
        if not oracle_id:
            continue
        entry = deck_card_map.get(oracle_id)
        qty = int(row.quantity or 0) or 1
        if entry:
            entry["quantity"] += qty
        else:
            deck_card_map[oracle_id] = {
                "card_id": row.id,
                "name": row.name,
                "oracle_id": row.oracle_id,
                "quantity": qty,
            }

    deck_card_details: list[dict] = []
    for entry in deck_card_map.values():
        oracle_id = (entry.get("oracle_id") or "").strip()
        pr = _preferred_print(oracle_id) if oracle_id else None
        meta = sc.metadata_from_print(pr) if pr else {}
        type_line = meta.get("type_line") or ""
        image = sc.image_for_print(pr) if pr else {}
        deck_card_details.append(
            {
                **entry,
                "type_line": type_line,
                "type_group": _type_group_label(type_line),
                "image_small": image.get("small"),
                "image_normal": image.get("normal") or image.get("large") or image.get("small"),
                "image_large": image.get("large") or image.get("normal") or image.get("small"),
            }
        )

    total_qty = sum(int(entry.get("quantity") or 0) for entry in deck_card_details)
    total_unique = len(deck_card_details)

    recommendations = get_build_recommendations(
        commander_oracle_id=folder.commander_oracle_id or "",
        tags=tags,
        deck_oracle_ids=set(deck_card_map.keys()),
        owner_user_id=folder.owner_user_id,
    )

    edhrec_recs = list((recommendations.get("owned") or []) + (recommendations.get("external") or []))
    edhrec_recs.sort(
        key=lambda item: (
            -(item.get("synergy_score") or 0.0),
            item.get("synergy_rank") if item.get("synergy_rank") is not None else 999999,
            (item.get("name") or "").lower(),
        )
    )
    edhrec_groups = _group_by_type(edhrec_recs)
    app_groups = _group_by_type(recommendations.get("app") or [])
    deck_groups = _group_by_type(deck_card_details)
    commander_colors = _commander_color_identity(folder.commander_oracle_id)
    commander_color_icons = colors_to_icons(commander_colors)

    return {
        "ok": True,
        "folder_id": folder.id,
        "folder_name": folder.name,
        "commander_oracle_id": folder.commander_oracle_id,
        "commander_name": folder.commander_name,
        "commander_image": _commander_image(folder.commander_oracle_id),
        "color_identity": commander_colors,
        "color_icons": commander_color_icons,
        "tags": tags,
        "deck_cards": deck_card_details,
        "deck_groups": deck_groups,
        "deck_total_qty": total_qty,
        "deck_total_unique": total_unique,
        "deck_stats": get_deck_stats(folder.id),
        "recommendations": recommendations,
        "edhrec_groups": edhrec_groups,
        "app_groups": app_groups,
        "health_hints": _deck_health_hints(set(deck_card_map.keys())),
    }


def finish_build(folder_id: int) -> None:
    folder = db.session.get(Folder, folder_id)
    if not folder or not folder.is_build:
        raise ValueError("Build deck not found.")

    folder.set_primary_role(Folder.CATEGORY_DECK)
    recompute_deck_stats(folder.id)
    try:
        db.session.commit()
    except SQLAlchemyError as exc:
        db.session.rollback()
        _LOG.error("Failed to finish build deck: %s", exc)
        raise


__all__ = ["start_build", "add_card_to_build", "get_build_state", "finish_build"]
