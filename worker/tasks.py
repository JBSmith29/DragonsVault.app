from __future__ import annotations

from typing import Dict, Iterable, Tuple

from extensions import db
from models import Card
from models.role import CardRole, CardSubRole, Role, SubRole, OracleRole
from roles.role_engine import get_primary_role, get_roles_for_card, get_subroles_for_card
from services import scryfall_cache as sc


def _get_or_create_role(key: str, cache: Dict[str, Role]) -> Role:
    key_norm = key.lower().strip()
    if key_norm in cache:
        return cache[key_norm]
    role = Role.query.filter_by(key=key_norm).first()
    if not role:
        role = Role(key=key_norm, label=key_norm.replace("_", " ").title())
        db.session.add(role)
        db.session.flush()
    cache[key_norm] = role
    return role


def _get_or_create_subrole(parent: Role, sub_key: str, cache: Dict[Tuple[int, str], SubRole]) -> SubRole:
    sub_norm = sub_key.lower().strip()
    cache_key = (parent.id, sub_norm)
    if cache_key in cache:
        return cache[cache_key]
    subrole = SubRole.query.filter_by(role_id=parent.id, key=sub_norm).first()
    if not subrole:
        subrole = SubRole(role_id=parent.id, key=sub_norm, label=sub_norm.replace("_", " ").title())
        db.session.add(subrole)
        db.session.flush()
    cache[cache_key] = subrole
    return subrole


def recompute_all_roles() -> None:
    """
    Rebuild role and subrole links for every card using the role engine.
    """
    role_cache: Dict[str, Role] = {}
    subrole_cache: Dict[Tuple[int, str], SubRole] = {}

    cards: Iterable[Card] = Card.query.all()
    for card in cards:
        roles = get_roles_for_card(card)
        subroles = get_subroles_for_card(card)
        primary = get_primary_role(roles)

        CardRole.query.filter_by(card_id=card.id).delete(synchronize_session=False)
        CardSubRole.query.filter_by(card_id=card.id).delete(synchronize_session=False)

        for role_key in roles:
            role = _get_or_create_role(role_key, role_cache)
            db.session.add(CardRole(card_id=card.id, role_id=role.id))

        for subrole_key in subroles:
            parent_key, _, child_key = subrole_key.partition(":")
            parent = _get_or_create_role(parent_key or "utility", role_cache)
            subrole = _get_or_create_subrole(parent, child_key or subrole_key, subrole_cache)
            db.session.add(CardSubRole(card_id=card.id, subrole_id=subrole.id))

        if primary:
            # Ensure primary role exists in the set (helpful for downstream consumers)
            primary_role = _get_or_create_role(primary, role_cache)
            db.session.add(CardRole(card_id=card.id, role_id=primary_role.id))

    db.session.commit()


def recompute_oracle_roles() -> None:
    """
    Rebuild oracle-level role mappings using the local Scryfall cache.
    """
    sc.ensure_cache_loaded()
    oracle_map = getattr(sc, "_by_oracle", {}) or {}
    if not oracle_map:
        return

    for oid, prints in oracle_map.items():
        if not oid:
            continue
        sample = prints[0] if prints else {}
        mock = {
            "name": sample.get("name") or "",
            "oracle_text": sample.get("oracle_text") or "",
            "type_line": sample.get("type_line") or "",
        }
        roles = get_roles_for_card(mock)
        subroles = get_subroles_for_card(mock)
        primary = get_primary_role(roles)
        entry = OracleRole.query.get(oid) or OracleRole(oracle_id=oid)
        entry.name = mock["name"]
        entry.type_line = mock["type_line"]
        entry.primary_role = primary
        entry.roles = roles
        entry.subroles = subroles
        db.session.merge(entry)

    db.session.commit()
