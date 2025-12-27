"""Recommendation aggregation for Build-A-Deck (EDHREC + owned + rules-based)."""

from __future__ import annotations

import logging

from sqlalchemy import func

from extensions import db
from models import Card, DeckTagCoreRoleSynergy, Folder, FolderRole, OracleCoreRoleTag
from services import scryfall_cache as sc
from services.edhrec_recommendation_service import get_commander_synergy
from . import build_constraints_service as constraints
from .build_scoring_service import score_app_card, score_edhrec_card

_LOG = logging.getLogger(__name__)

ROLE_TARGETS = {
    "ramp": 8,
    "draw": 8,
    "removal": 6,
    "wipe": 2,
}
MAX_RECS = 40
MAX_APP_RECS = 16

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


def _type_group_label(type_line: str) -> str:
    lowered = (type_line or "").lower()
    for label, token in _TYPE_GROUPS:
        if token.lower() in lowered:
            return label
    return "Other"


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


def _card_display_meta(oracle_id: str, cache: dict[str, dict]) -> dict:
    if oracle_id in cache:
        return cache[oracle_id]
    pr = _preferred_print(oracle_id)
    meta = sc.metadata_from_print(pr) if pr else {}
    type_line = meta.get("type_line") or ""
    image = sc.image_for_print(pr) if pr else {}
    payload = {
        "type_line": type_line,
        "type_group": _type_group_label(type_line),
        "image_small": image.get("small"),
        "image_normal": image.get("normal") or image.get("large") or image.get("small"),
        "image_large": image.get("large") or image.get("normal") or image.get("small"),
    }
    cache[oracle_id] = payload
    return payload


def _collection_folder_ids(owner_user_id: int | None) -> list[int]:
    if not owner_user_id:
        return []
    rows = (
        db.session.query(FolderRole.folder_id)
        .join(Folder, Folder.id == FolderRole.folder_id)
        .filter(
            FolderRole.role == FolderRole.ROLE_COLLECTION,
            Folder.owner_user_id == owner_user_id,
        )
        .all()
    )
    return [row[0] for row in rows]


def _owned_counts(owner_user_id: int | None, oracle_ids: list[str]) -> dict[str, int]:
    if not owner_user_id or not oracle_ids:
        return {}
    collection_ids = _collection_folder_ids(owner_user_id)
    if not collection_ids:
        return {}
    rows = (
        db.session.query(Card.oracle_id, func.coalesce(func.sum(Card.quantity), 0))
        .filter(Card.folder_id.in_(collection_ids), Card.oracle_id.in_(oracle_ids))
        .group_by(Card.oracle_id)
        .all()
    )
    return {str(oid).strip(): int(total or 0) for oid, total in rows if oid}


def _tag_role_weights(tags: list[str]) -> dict[str, float]:
    if not tags:
        return {}
    rows = (
        db.session.query(DeckTagCoreRoleSynergy.role, DeckTagCoreRoleSynergy.weight)
        .filter(DeckTagCoreRoleSynergy.deck_tag.in_(tags))
        .all()
    )
    weights: dict[str, float] = {}
    for role, weight in rows:
        if not role:
            continue
        if weight is None:
            weight = 0.0
        weights[role] = max(weights.get(role, 0.0), float(weight))
    return weights


def _missing_roles(role_counts: dict[str, int], tags: list[str]) -> dict[str, int]:
    missing: dict[str, int] = {}
    for role, target in ROLE_TARGETS.items():
        count = int(role_counts.get(role, 0) or 0)
        if count < target:
            missing[role] = target - count

    tag_weights = _tag_role_weights(tags)
    for role, weight in tag_weights.items():
        if weight <= 0:
            continue
        count = int(role_counts.get(role, 0) or 0)
        target = ROLE_TARGETS.get(role, 4)
        if count < target:
            missing.setdefault(role, target - count)

    return missing


def _role_map_for_oracles(oracle_ids: list[str]) -> dict[str, set[str]]:
    if not oracle_ids:
        return {}
    rows = (
        db.session.query(OracleCoreRoleTag.oracle_id, OracleCoreRoleTag.role)
        .filter(OracleCoreRoleTag.oracle_id.in_(oracle_ids))
        .all()
    )
    role_map: dict[str, set[str]] = {}
    for oid, role in rows:
        if not oid or not role:
            continue
        role_map.setdefault(str(oid).strip(), set()).add(str(role).strip())
    return role_map


def _app_suggestions(
    *,
    deck_oracle_ids: set[str],
    owner_user_id: int | None,
    tags: list[str],
    missing_roles: dict[str, int],
    commander_mask: int,
    commander_mask_ok: bool,
) -> list[dict]:
    if not owner_user_id or not missing_roles:
        return []
    collection_ids = _collection_folder_ids(owner_user_id)
    if not collection_ids:
        return []

    role_list = list(missing_roles.keys())
    rows = (
        db.session.query(
            Card.oracle_id,
            func.max(Card.name).label("name"),
            func.coalesce(func.sum(Card.quantity), 0).label("qty"),
            OracleCoreRoleTag.role,
        )
        .join(OracleCoreRoleTag, OracleCoreRoleTag.oracle_id == Card.oracle_id)
        .filter(
            Card.folder_id.in_(collection_ids),
            Card.oracle_id.isnot(None),
            OracleCoreRoleTag.role.in_(role_list),
        )
        .group_by(Card.oracle_id, OracleCoreRoleTag.role)
        .all()
    )

    candidates: dict[str, dict] = {}
    for oracle_id, name, qty, role in rows:
        oid = (oracle_id or "").strip()
        if not oid or oid in deck_oracle_ids:
            continue
        entry = candidates.setdefault(
            oid,
            {"oracle_id": oid, "name": name or oid, "owned_qty": int(qty or 0), "roles": set()},
        )
        if role:
            entry["roles"].add(str(role).strip())

    if not candidates:
        return []

    tag_weights = _tag_role_weights(tags)
    results: list[dict] = []
    meta_cache: dict[str, dict] = {}
    for oid, entry in candidates.items():
        card_mask, card_ok = constraints.card_color_mask(oid)
        legal = True
        legal_reason = None
        if commander_mask_ok and card_ok and commander_mask & card_mask != card_mask:
            legal = False
            legal_reason = "Outside commander color identity."
        roles = entry.get("roles") or set()
        gap_roles = [r for r in roles if r in missing_roles]
        if not gap_roles:
            continue
        tag_bonus = sum(tag_weights.get(role, 0.0) for role in gap_roles)
        score, reasons = score_app_card(
            owned_qty=int(entry.get("owned_qty") or 0),
            gap_roles=gap_roles,
            tag_bonus=tag_bonus,
        )
        meta = _card_display_meta(oid, meta_cache)
        results.append(
            {
                "oracle_id": oid,
                "name": entry.get("name") or oid,
                "owned": True,
                "owned_qty": int(entry.get("owned_qty") or 0),
                "in_deck": False,
                "source": "app",
                "score": score,
                "reasons": reasons,
                "legal": legal,
                "legal_reason": legal_reason,
                "can_add": legal,
                "disabled_reason": legal_reason if not legal else None,
                **meta,
            }
        )

    results.sort(
        key=lambda item: (
            -(item.get("score") or 0.0),
            -(item.get("owned_qty") or 0),
            (item.get("name") or "").lower(),
        )
    )
    return results[:MAX_APP_RECS]


def get_build_recommendations(
    *,
    commander_oracle_id: str,
    tags: list[str],
    deck_oracle_ids: set[str],
    owner_user_id: int | None,
) -> dict:
    if not commander_oracle_id:
        return {"owned": [], "external": [], "app": []}

    try:
        sc.ensure_cache_loaded()
    except Exception as exc:
        _LOG.warning("Scryfall cache unavailable for build recommendations: %s", exc)

    commander_mask, commander_mask_ok = constraints.commander_color_mask(commander_oracle_id)

    role_counts = _deck_role_counts_for_recs(deck_oracle_ids)
    missing_roles = _missing_roles(role_counts, tags)

    recs = get_commander_synergy(commander_oracle_id, tags) or []
    recs = recs[:MAX_RECS]
    rec_oracle_ids = [str(rec.get("oracle_id") or "").strip() for rec in recs if rec.get("oracle_id")]
    role_map = _role_map_for_oracles(rec_oracle_ids)

    owned_counts = _owned_counts(owner_user_id, rec_oracle_ids)
    owned_recs: list[dict] = []
    external_recs: list[dict] = []
    meta_cache: dict[str, dict] = {}

    for rec in recs:
        oracle_id = (rec.get("oracle_id") or "").strip()
        if not oracle_id:
            continue
        card_mask, card_ok = constraints.card_color_mask(oracle_id)
        legal = True
        legal_reason = None
        if commander_mask_ok and card_ok and commander_mask & card_mask != card_mask:
            legal = False
            legal_reason = "Outside commander color identity."
        owned_qty = owned_counts.get(oracle_id, 0)
        in_deck = oracle_id in deck_oracle_ids
        gap_roles = [r for r in role_map.get(oracle_id, set()) if r in missing_roles]
        score, reasons = score_edhrec_card(
            synergy_score=float(rec.get("synergy_score") or 0.0),
            owned_qty=owned_qty,
            tag_matches=rec.get("tag_matches") or [],
            gap_roles=gap_roles,
        )
        meta = _card_display_meta(oracle_id, meta_cache)
        can_add = legal and not in_deck
        disabled_reason = None
        if not legal:
            disabled_reason = legal_reason
        elif in_deck:
            disabled_reason = "Already in deck."
        payload = {
            **rec,
            "oracle_id": oracle_id,
            "owned": bool(owned_qty),
            "owned_qty": owned_qty,
            "in_deck": in_deck,
            "score": score,
            "reasons": reasons,
            "legal": legal,
            "legal_reason": legal_reason,
            "can_add": can_add,
            "disabled_reason": disabled_reason,
            **meta,
        }
        if owned_qty:
            owned_recs.append(payload)
        else:
            external_recs.append(payload)

    owned_recs.sort(
        key=lambda item: (
            -(item.get("score") or 0.0),
            -(item.get("synergy_score") or 0.0),
            (item.get("name") or "").lower(),
        )
    )
    external_recs.sort(
        key=lambda item: (
            -(item.get("score") or 0.0),
            -(item.get("synergy_score") or 0.0),
            (item.get("name") or "").lower(),
        )
    )

    app_recs = _app_suggestions(
        deck_oracle_ids=deck_oracle_ids,
        owner_user_id=owner_user_id,
        tags=tags,
        missing_roles=missing_roles,
        commander_mask=commander_mask,
        commander_mask_ok=commander_mask_ok,
    )

    return {"owned": owned_recs, "external": external_recs, "app": app_recs}


def _deck_role_counts_for_recs(deck_oracle_ids: set[str]) -> dict[str, int]:
    if not deck_oracle_ids:
        return {}
    rows = (
        db.session.query(
            OracleCoreRoleTag.role,
            func.count(OracleCoreRoleTag.oracle_id),
        )
        .filter(OracleCoreRoleTag.oracle_id.in_(list(deck_oracle_ids)))
        .group_by(OracleCoreRoleTag.role)
        .all()
    )
    return {str(role).strip(): int(total or 0) for role, total in rows if role}


__all__ = ["get_build_recommendations"]
