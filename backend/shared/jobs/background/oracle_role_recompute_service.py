"""Role and subrole recomputation helpers for card rows."""

from __future__ import annotations

import logging
from typing import Dict, Iterable, Tuple

from extensions import db
from models import Card
from models.role import CardRole, CardSubRole, Role, SubRole
from roles.role_engine import get_primary_role, get_roles_for_card, get_subroles_for_card
from core.domains.cards.services import scryfall_cache as sc
from shared.jobs.background import oracle_profile_service
from sqlalchemy.exc import SQLAlchemyError

_LOG = logging.getLogger(__name__)


def get_or_create_role(key: str, cache: Dict[str, Role]) -> Role:
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


def get_or_create_subrole(parent: Role, sub_key: str, cache: Dict[Tuple[int, str], SubRole]) -> SubRole:
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


def recompute_all_roles(*, merge_existing: bool = True) -> dict:
    """
    Rebuild role and subrole links for every card using the role engine.
    If merge_existing is True, union derived roles with existing ones.
    """
    _LOG.info("Role recompute started (merge_existing=%s).", merge_existing)
    role_cache: Dict[str, Role] = {}
    subrole_cache: Dict[Tuple[int, str], SubRole] = {}
    cache_ready = sc.ensure_cache_loaded()

    cards: Iterable[Card] = Card.query.all()
    cards_scanned = 0
    roles_written = 0
    subroles_written = 0
    try:
        for card in cards:
            cards_scanned += 1
            print_data = None
            if cache_ready:
                try:
                    if card.oracle_id:
                        prints = sc.prints_for_oracle(card.oracle_id) or []
                        print_data = oracle_profile_service.select_best_print(prints) or (prints[0] if prints else None)
                    if not print_data:
                        print_data = sc.find_by_set_cn(card.set_code, card.collector_number, card.name)
                except Exception:
                    print_data = None

            if print_data:
                mock = oracle_profile_service.build_oracle_mock(print_data)
                mock["name"] = mock["name"] or card.name
                mock["type_line"] = mock["type_line"] or (card.type_line or "")
            else:
                mock = {
                    "name": card.name,
                    "oracle_text": "",
                    "type_line": card.type_line or "",
                    "card_faces": [],
                    "layout": "",
                    "produced_mana": [],
                }

            derived_roles_list = get_roles_for_card(mock)
            derived_subroles_list = get_subroles_for_card(mock)
            derived_roles = set(derived_roles_list)
            derived_subroles = set(derived_subroles_list)
            derived_primary = get_primary_role(derived_roles_list)

            existing_roles: set[str] = set()
            existing_primary = None
            existing_subroles: set[str] = set()
            if merge_existing:
                existing_role_rows = (
                    db.session.query(Role.key, CardRole.primary)
                    .join(CardRole, CardRole.role_id == Role.id)
                    .filter(CardRole.card_id == card.id)
                    .all()
                )
                for role_key, is_primary in existing_role_rows:
                    if role_key:
                        existing_roles.add(role_key)
                        if is_primary and not existing_primary:
                            existing_primary = role_key

                existing_subrole_rows = (
                    db.session.query(Role.key, SubRole.key)
                    .join(SubRole, SubRole.role_id == Role.id)
                    .join(CardSubRole, CardSubRole.subrole_id == SubRole.id)
                    .filter(CardSubRole.card_id == card.id)
                    .all()
                )
                for parent_key, sub_key in existing_subrole_rows:
                    if parent_key and sub_key:
                        existing_subroles.add(f"{parent_key}:{sub_key}".lower())

            roles = set(existing_roles) | derived_roles
            subroles = set(existing_subroles) | derived_subroles
            primary = existing_primary or derived_primary
            if primary:
                roles.add(primary)

            CardRole.query.filter_by(card_id=card.id).delete(synchronize_session=False)
            CardSubRole.query.filter_by(card_id=card.id).delete(synchronize_session=False)

            for role_key in roles:
                if not role_key:
                    continue
                role = get_or_create_role(role_key, role_cache)
                db.session.add(
                    CardRole(card_id=card.id, role_id=role.id, primary=bool(primary and role_key == primary))
                )
            roles_written += len(roles)

            for subrole_key in subroles:
                if not subrole_key:
                    continue
                parent_key, _, child_key = subrole_key.partition(":")
                parent = get_or_create_role(parent_key or "utility", role_cache)
                subrole = get_or_create_subrole(parent, child_key or subrole_key, subrole_cache)
                db.session.add(CardSubRole(card_id=card.id, subrole_id=subrole.id))
            subroles_written += len(subroles)

        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        _LOG.error("Role recompute failed due to database error.", exc_info=True)
        raise
    except Exception:
        db.session.rollback()
        _LOG.error("Role recompute failed.", exc_info=True)
        raise

    summary = {
        "cards_scanned": cards_scanned,
        "roles_written": roles_written,
        "subroles_written": subroles_written,
        "merged_existing": bool(merge_existing),
    }
    _LOG.info(
        "Role recompute completed: cards=%s roles=%s subroles=%s",
        cards_scanned,
        roles_written,
        subroles_written,
    )
    return summary


__all__ = [
    "get_or_create_role",
    "get_or_create_subrole",
    "recompute_all_roles",
]
