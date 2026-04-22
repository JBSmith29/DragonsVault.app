"""Shared helpers for Scryfall browser, set, and print-detail services."""

from __future__ import annotations

from flask import request

from extensions import db
from models.role import OracleCoreRoleTag, OracleEvergreenTag, OracleRole
from core.domains.cards.viewmodels.card_vm import format_role_label
from shared.cache.request_cache import request_cached

RARITY_CHOICES = [
    {"value": "common", "label": "Common"},
    {"value": "uncommon", "label": "Uncommon"},
    {"value": "rare", "label": "Rare"},
    {"value": "mythic", "label": "Mythic"},
    {"value": "special", "label": "Special"},
    {"value": "bonus", "label": "Bonus"},
    {"value": "masterpiece", "label": "Masterpiece"},
    {"value": "timeshifted", "label": "Timeshifted"},
    {"value": "basic", "label": "Basic"},
]

BASE_TYPES = ["Artifact", "Battle", "Creature", "Enchantment", "Instant", "Land", "Planeswalker", "Sorcery"]
RARITY_CLASS_MAP = {
    "common": "secondary",
    "uncommon": "success",
    "rare": "warning",
    "mythic": "danger",
    "mythic rare": "danger",
}


def _type_badges(type_line: str | None) -> list[str]:
    if not type_line:
        return []
    return [value for value in BASE_TYPES if value in type_line]


def _rarity_badge_class(rarity_value: str | None) -> str | None:
    if not rarity_value:
        return None
    return RARITY_CLASS_MAP.get(rarity_value.lower(), "secondary")


def _price_lines(prices: dict | None) -> list[str]:
    if not prices:
        return []
    lines = []
    if prices.get("usd"):
        lines.append(f"USD {prices['usd']}")
    if prices.get("usd_foil"):
        lines.append(f"USD Foil {prices['usd_foil']}")
    if prices.get("usd_etched"):
        lines.append(f"USD Etched {prices['usd_etched']}")
    return lines


def _request_cached_evergreen_labels(oracle_id: str | None) -> list[str]:
    if not oracle_id:
        return []
    key = ("card_view", "evergreen", oracle_id)

    def _load() -> list[str]:
        return [
            row[0]
            for row in (
                db.session.query(OracleEvergreenTag.keyword)
                .filter(OracleEvergreenTag.oracle_id == oracle_id)
                .order_by(OracleEvergreenTag.keyword.asc())
                .all()
            )
            if row and row[0]
        ]

    return request_cached(key, _load)


def _request_cached_core_role_labels(oracle_id: str | None) -> list[str]:
    if not oracle_id:
        return []
    key = ("card_view", "core_roles", oracle_id)

    def _load() -> list[str]:
        rows = (
            db.session.query(OracleCoreRoleTag.role)
            .filter(OracleCoreRoleTag.oracle_id == oracle_id)
            .order_by(OracleCoreRoleTag.role.asc())
            .all()
        )
        labels: list[str] = []
        for row in rows:
            role = row[0] if row else None
            if not role:
                continue
            label = format_role_label(role)
            if label not in labels:
                labels.append(label)
        return labels

    return request_cached(key, _load)


def _request_cached_primary_oracle_role_label(oracle_id: str | None) -> str | None:
    if not oracle_id:
        return None
    key = ("card_view", "primary_oracle_role", oracle_id)

    def _load() -> str | None:
        row = (
            db.session.query(OracleRole.primary_role)
            .filter(OracleRole.oracle_id == oracle_id)
            .first()
        )
        if not row or not row[0]:
            return None
        return format_role_label(str(row[0]))

    return request_cached(key, _load)


def _faces_from_scry_json(data: dict | None) -> list[dict]:
    faces = []
    if not data:
        return faces
    if data.get("card_faces"):
        for face in data["card_faces"]:
            iu = (face or {}).get("image_uris") or {}
            faces.append({"large": iu.get("large"), "normal": iu.get("normal"), "small": iu.get("small")})
    else:
        iu = data.get("image_uris") or {}
        if iu:
            faces.append({"large": iu.get("large"), "normal": iu.get("normal"), "small": iu.get("small")})
    out = []
    seen = set()
    for face in faces:
        key = (face.get("large"), face.get("normal"), face.get("small"))
        if key in seen:
            continue
        seen.add(key)
        out.append(face)
    return out


__all__ = [
    "BASE_TYPES",
    "RARITY_CHOICES",
    "RARITY_CLASS_MAP",
    "_faces_from_scry_json",
    "_price_lines",
    "_rarity_badge_class",
    "_request_cached_core_role_labels",
    "_request_cached_evergreen_labels",
    "_request_cached_primary_oracle_role_label",
    "_type_badges",
]
