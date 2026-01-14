"""Recommendation helpers for Build-A-Deck sessions."""

from __future__ import annotations

from collections import defaultdict

from extensions import db
from models import OracleCoreRoleTag
from services import scryfall_cache as sc
from services.edhrec_cache_service import get_commander_category_groups, get_commander_synergy


def get_edhrec_recommendations(
    commander_oracle_id: str,
    tags: list[str] | None = None,
    *,
    prefer_tag_specific: bool = True,
    limit: int | None = None,
) -> list[dict]:
    if not commander_oracle_id:
        return []

    try:
        sc.ensure_cache_loaded()
    except Exception:
        return []

    commander_identity = _color_identity_set(commander_oracle_id)
    raw_cards = get_commander_synergy(
        commander_oracle_id,
        tags,
        prefer_tag_specific=prefer_tag_specific,
        limit=limit,
    )
    filtered: dict[str, dict] = {}
    for rec in raw_cards:
        oracle_id = (rec.get("oracle_id") or "").strip()
        if not oracle_id:
            continue
        card_identity = _color_identity_set(oracle_id)
        if card_identity and not card_identity.issubset(commander_identity):
            continue
        payload = _card_payload(oracle_id)
        if not payload:
            continue
        reasons = ["edhrec synergy"]
        tag_matches = rec.get("tag_matches") or []
        if tag_matches:
            reasons.append(f"tag: {', '.join(tag_matches)}")
        filtered[oracle_id] = {
            "oracle_id": oracle_id,
            "name": payload["name"],
            "image": payload["image"],
            "synergy_score": rec.get("synergy_score"),
            "synergy_percent": rec.get("synergy_percent"),
            "inclusion_percent": rec.get("inclusion_percent"),
            "synergy_rank": rec.get("synergy_rank"),
            "reasons": reasons,
        }

    ordered = list(filtered.values())
    ordered.sort(
        key=lambda item: (
            -(item.get("synergy_score") or 0.0),
            item.get("synergy_rank") if item.get("synergy_rank") is not None else 999999,
        )
    )
    return ordered


def build_recommendation_sections(
    commander_oracle_id: str,
    tags: list[str] | None,
    *,
    role_needs: set[str] | None = None,
    sort_mode: str = "synergy",
) -> list[dict]:
    if not commander_oracle_id:
        return []

    commander_identity = _color_identity_set(commander_oracle_id)
    role_needs = {role for role in (role_needs or set()) if role}
    selected_tag = tags[0] if tags else None

    category_groups = get_commander_category_groups(
        commander_oracle_id,
        tag=selected_tag,
        limit=None,
    )

    all_oracles: set[str] = set()
    for group in category_groups:
        for rec in group.get("cards") or []:
            oid = (rec.get("oracle_id") or "").strip()
            if oid:
                all_oracles.add(oid)

    role_map = _role_map(all_oracles)
    sections: list[dict] = []
    for group in _order_category_groups(category_groups):
        label = group.get("label") or "edhrec"
        cards: list[dict] = []
        for rec in group.get("cards") or []:
            card = _edhrec_card_payload(rec, role_map, role_needs, commander_identity)
            if not card:
                continue
            card["reasons"].append(f"edhrec: {label}")
            cards.append(card)
        if not cards:
            continue
        sections.append(
            _section(
                _slugify_label(label),
                label,
                "edhrec category list.",
                cards,
                sort_mode=sort_mode,
                default_open=_is_default_open(label),
            )
        )

    return sections


def _color_identity_set(oracle_id: str) -> set[str]:
    if not oracle_id:
        return set()
    try:
        prints = sc.prints_for_oracle(oracle_id) or []
    except Exception:
        return set()
    if not prints:
        return set()
    identity_raw = prints[0].get("color_identity") or prints[0].get("colors") or []
    letters, _ = sc.normalize_color_identity(identity_raw)
    return set(letters)


def _card_payload(oracle_id: str) -> dict | None:
    if not oracle_id:
        return None
    try:
        prints = sc.prints_for_oracle(oracle_id) or []
    except Exception:
        return None
    if not prints:
        return None
    pr = prints[0]
    name = (pr.get("name") or "").strip()
    type_line = pr.get("type_line") or ""
    image = None
    image_uris = pr.get("image_uris") or {}
    if not image_uris:
        faces = pr.get("card_faces") or []
        if faces:
            image_uris = (faces[0] or {}).get("image_uris") or {}
    image = image_uris.get("normal") or image_uris.get("large") or image_uris.get("small")
    lowered = type_line.lower()
    is_basic_land = "land" in lowered and "basic" in lowered
    return {
        "name": name or oracle_id,
        "image": image,
        "type_line": type_line,
        "is_basic_land": is_basic_land,
    }


def _role_map(oracle_ids: set[str]) -> dict[str, set[str]]:
    if not oracle_ids:
        return {}
    rows = (
        db.session.query(OracleCoreRoleTag.oracle_id, OracleCoreRoleTag.role)
        .filter(OracleCoreRoleTag.oracle_id.in_(oracle_ids))
        .all()
    )
    role_map: dict[str, set[str]] = defaultdict(set)
    for oracle_id, role in rows:
        if not oracle_id or not role:
            continue
        role_map[oracle_id].add(str(role))
    return role_map


def _edhrec_card_payload(
    rec: dict,
    role_map: dict[str, set[str]],
    role_needs: set[str],
    commander_identity: set[str] | None,
) -> dict | None:
    oracle_id = (rec.get("oracle_id") or "").strip()
    if not oracle_id:
        return None
    if commander_identity is not None:
        card_identity = _color_identity_set(oracle_id)
        if card_identity and not card_identity.issubset(commander_identity):
            return None
    payload = _card_payload(oracle_id)
    if not payload:
        return None
    synergy_score = rec.get("synergy_score")
    synergy_percent = rec.get("synergy_percent")
    inclusion_percent = rec.get("inclusion_percent")
    if synergy_percent is None and synergy_score is not None:
        synergy_percent = round(float(synergy_score) * 100.0, 1)
    roles = role_map.get(oracle_id, set())
    needed_roles = sorted(role_needs & roles)
    reasons = ["edhrec synergy"]
    for role in needed_roles[:2]:
        reasons.append(f"fills {role.lower()}")
    return {
        "oracle_id": oracle_id,
        "name": payload["name"],
        "image": payload["image"],
        "type_line": payload.get("type_line") or "",
        "is_basic_land": bool(payload.get("is_basic_land")),
        "synergy_score": float(synergy_score or 0.0),
        "synergy_percent": synergy_percent,
        "inclusion_percent": inclusion_percent,
        "synergy_rank": rec.get("synergy_rank"),
        "roles": sorted(roles),
        "role_score": len(roles),
        "need_score": len(needed_roles),
        "reasons": reasons,
    }


def _build_card_payloads(
    cards: list[dict],
    role_map: dict[str, set[str]],
    role_needs: set[str],
) -> list[dict]:
    payloads: list[dict] = []
    for rec in cards:
        card = _edhrec_card_payload(rec, role_map, role_needs, None)
        if not card:
            continue
        payloads.append(card)
    return payloads


def _dedupe_cards(cards: list[dict], used: set[str]) -> list[dict]:
    ordered: list[dict] = []
    for card in cards:
        oracle_id = card.get("oracle_id")
        if not oracle_id or oracle_id in used:
            continue
        used.add(oracle_id)
        ordered.append(card)
    return ordered


def _section(
    key: str,
    label: str,
    description: str,
    cards: list[dict],
    *,
    sort_mode: str,
    default_open: bool = False,
) -> dict:
    return {
        "key": key,
        "label": label,
        "description": description,
        "cards": _sort_cards(cards, sort_mode),
        "default_open": default_open,
        "count": len(cards),
    }


def _sort_cards(cards: list[dict], sort_mode: str) -> list[dict]:
    mode = (sort_mode or "synergy").strip().lower()
    if mode == "role":
        key = lambda item: (-item.get("role_score", 0), -(item.get("synergy_score") or 0.0))
    elif mode == "need":
        key = lambda item: (-item.get("need_score", 0), -item.get("role_score", 0), -(item.get("synergy_score") or 0.0))
    else:
        key = lambda item: (-(item.get("synergy_score") or 0.0), item.get("synergy_rank") or 999999)
    return sorted(cards, key=key)


_EDHREC_CATEGORY_ORDER = [
    "New Cards",
    "High Synergy Cards",
    "Top Cards",
    "Game Changers",
    "Creatures",
    "Instants",
    "Sorceries",
    "Utility Artifacts",
    "Enchantments",
    "Planeswalkers",
    "Utility Lands",
    "Mana Artifacts",
    "Lands",
]


def _order_category_groups(groups: list[dict]) -> list[dict]:
    if not groups:
        return []
    order_map = {label.casefold(): idx for idx, label in enumerate(_EDHREC_CATEGORY_ORDER)}
    return sorted(
        groups,
        key=lambda group: (
            order_map.get((group.get("label") or "").casefold(), 999),
            (group.get("label") or "").casefold(),
        ),
    )


def _slugify_label(label: str) -> str:
    return (label or "edhrec").strip().lower().replace(" ", "-")


def _is_default_open(label: str) -> bool:
    key = (label or "").casefold()
    return key in {
        "high synergy cards",
        "top cards",
        "creatures",
        "instants",
        "sorceries",
        "lands",
    }


__all__ = ["build_recommendation_sections", "get_edhrec_recommendations"]
