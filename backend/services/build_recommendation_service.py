"""Recommendation helpers for Build-A-Deck sessions."""

from __future__ import annotations

from services import scryfall_cache as sc
from services.edhrec_cache_service import get_commander_synergy


def get_edhrec_recommendations(
    commander_oracle_id: str,
    tags: list[str] | None = None,
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
        prefer_tag_specific=True,
        limit=None,
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
        reasons = ["high synergy with commander"]
        tag_matches = rec.get("tag_matches") or []
        if tag_matches:
            reasons.append(f"matches {', '.join(tag_matches)}")
        filtered[oracle_id] = {
            "oracle_id": oracle_id,
            "name": payload["name"],
            "image": payload["image"],
            "synergy_score": rec.get("synergy_score"),
            "synergy_percent": rec.get("synergy_percent"),
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
    image = None
    image_uris = pr.get("image_uris") or {}
    if not image_uris:
        faces = pr.get("card_faces") or []
        if faces:
            image_uris = (faces[0] or {}).get("image_uris") or {}
    image = image_uris.get("normal") or image_uris.get("large") or image_uris.get("small")
    return {"name": name or oracle_id, "image": image}


__all__ = ["get_edhrec_recommendations"]
