from __future__ import annotations

from typing import Dict, Iterable, Tuple, Set

from extensions import db
from models import Card
from models.role import (
    CardRole,
    CardSubRole,
    Role,
    SubRole,
    OracleRole,
    OracleKeywordTag,
    OracleRoleTag,
    OracleTypalTag,
    OracleDeckTag,
    OracleEvergreenTag,
)
from roles.role_engine import get_primary_role, get_roles_for_card, get_subroles_for_card, get_land_tags_for_card
from services.oracle_tagging import derive_deck_tags, derive_evergreen_keywords, deck_tag_category, ensure_fallback_tag
from services import scryfall_cache as sc

_DASH = "\u2014"
_EXCLUDED_SET_TYPES = {"token", "memorabilia", "art_series"}
_TYPAL_TRIGGER_TYPES = {"creature", "tribal", "kindred"}


def _score_print(print_data: dict) -> int:
    score = 0
    if print_data.get("lang") == "en":
        score += 3
    if (print_data.get("set_type") or "") not in _EXCLUDED_SET_TYPES:
        score += 2
    if "paper" in (print_data.get("games") or []):
        score += 1
    if not print_data.get("digital"):
        score += 1
    return score


def _select_best_print(prints: Iterable[dict]) -> dict | None:
    best = None
    best_score = -1
    for pr in prints:
        if not isinstance(pr, dict):
            continue
        score = _score_print(pr)
        if score > best_score:
            best = pr
            best_score = score
    return best


def _join_faces(faces: list[dict], key: str) -> str | None:
    parts = []
    for face in faces:
        if not isinstance(face, dict):
            continue
        value = face.get(key)
        if value:
            parts.append(value)
    if not parts:
        return None
    return "\n\n//\n\n".join(parts)


def _oracle_text_from_print(print_data: dict) -> str:
    return print_data.get("oracle_text") or _join_faces(print_data.get("card_faces") or [], "oracle_text") or ""


def _type_line_from_print(print_data: dict) -> str:
    return print_data.get("type_line") or _join_faces(print_data.get("card_faces") or [], "type_line") or ""


def _iter_type_lines(print_data: dict) -> Iterable[str]:
    type_line = print_data.get("type_line")
    if type_line:
        yield type_line
    for face in print_data.get("card_faces") or []:
        if not isinstance(face, dict):
            continue
        face_line = face.get("type_line")
        if face_line:
            yield face_line


def _split_type_line(type_line: str) -> tuple[str, str] | None:
    if not type_line:
        return None
    if _DASH in type_line:
        left, right = type_line.split(_DASH, 1)
    elif " - " in type_line:
        left, right = type_line.split(" - ", 1)
    else:
        return None
    return left.strip(), right.strip()


def _typal_from_type_line(type_line: str) -> Set[str]:
    split = _split_type_line(type_line)
    if not split:
        return set()
    left, right = split
    left_norm = left.lower()
    if not any(token in left_norm for token in _TYPAL_TRIGGER_TYPES):
        return set()
    out: Set[str] = set()
    for token in right.split():
        token = token.strip()
        if token:
            out.add(token.lower())
    return out


def _collect_keywords(prints: Iterable[dict]) -> Set[str]:
    keywords: Set[str] = set()
    for pr in prints:
        if not isinstance(pr, dict):
            continue
        for kw in pr.get("keywords") or []:
            if not isinstance(kw, str):
                continue
            norm = kw.strip().lower()
            if norm:
                keywords.add(norm)
    return keywords


def _collect_typals(prints: Iterable[dict]) -> Set[str]:
    typals: Set[str] = set()
    for pr in prints:
        if not isinstance(pr, dict):
            continue
        for type_line in _iter_type_lines(pr):
            typals.update(_typal_from_type_line(type_line))
    return typals

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


def recompute_all_roles(*, merge_existing: bool = True) -> None:
    """
    Rebuild role and subrole links for every card using the role engine.
    If merge_existing is True, union derived roles with existing ones.
    """
    role_cache: Dict[str, Role] = {}
    subrole_cache: Dict[Tuple[int, str], SubRole] = {}
    cache_ready = sc.ensure_cache_loaded()

    cards: Iterable[Card] = Card.query.all()
    for card in cards:
        print_data = None
        if cache_ready:
            try:
                if card.oracle_id:
                    prints = sc.prints_for_oracle(card.oracle_id) or []
                    print_data = _select_best_print(prints) or (prints[0] if prints else None)
                if not print_data:
                    print_data = sc.find_by_set_cn(card.set_code, card.collector_number, card.name)
            except Exception:
                print_data = None

        if print_data:
            mock = {
                "name": print_data.get("name") or card.name,
                "oracle_text": _oracle_text_from_print(print_data),
                "type_line": _type_line_from_print(print_data) or (card.type_line or ""),
                "card_faces": print_data.get("card_faces") or [],
                "layout": print_data.get("layout") or "",
                "produced_mana": print_data.get("produced_mana") or [],
            }
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
            role = _get_or_create_role(role_key, role_cache)
            db.session.add(
                CardRole(card_id=card.id, role_id=role.id, primary=bool(primary and role_key == primary))
            )

        for subrole_key in subroles:
            if not subrole_key:
                continue
            parent_key, _, child_key = subrole_key.partition(":")
            parent = _get_or_create_role(parent_key or "utility", role_cache)
            subrole = _get_or_create_subrole(parent, child_key or subrole_key, subrole_cache)
            db.session.add(CardSubRole(card_id=card.id, subrole_id=subrole.id))

    db.session.commit()


def recompute_oracle_roles() -> None:
    """
    Rebuild oracle-level role mappings using the local Scryfall cache.
    """
    recompute_oracle_enrichment()


def _iter_oracle_prints() -> Iterable[tuple[str, list[dict]]]:
    oracle_map = getattr(sc, "_by_oracle", {}) or {}
    for oid, prints in oracle_map.items():
        if not oid or not prints:
            continue
        yield oid, prints


def _ensure_oracle_cache() -> bool:
    if not sc.ensure_cache_loaded():
        return False
    return bool(getattr(sc, "_by_oracle", {}) or {})


def recompute_oracle_deck_tags() -> None:
    """
    Rebuild oracle-level deck tags and evergreen keywords using the Scryfall cache.
    """
    if not _ensure_oracle_cache():
        return

    OracleDeckTag.query.delete(synchronize_session=False)
    OracleEvergreenTag.query.delete(synchronize_session=False)

    deck_tag_rows = []
    evergreen_rows = []

    for oid, prints in _iter_oracle_prints():
        best = _select_best_print(prints) or (prints[0] if prints else None)
        if not best:
            continue
        oracle_text = _oracle_text_from_print(best)
        type_line = _type_line_from_print(best)
        mock = {
            "name": best.get("name") or "",
            "oracle_text": oracle_text,
            "type_line": type_line,
            "card_faces": best.get("card_faces") or [],
            "layout": best.get("layout") or "",
            "produced_mana": best.get("produced_mana") or [],
        }
        roles = get_roles_for_card(mock)
        keywords = _collect_keywords(prints)
        typals = _collect_typals(prints)
        land_tags = get_land_tags_for_card(mock)
        evergreen = set(land_tags) if land_tags else derive_evergreen_keywords(oracle_text=oracle_text, keywords=keywords)
        deck_tags = derive_deck_tags(
            oracle_text=oracle_text,
            type_line=type_line,
            keywords=keywords,
            typals=typals,
            roles=roles,
        )
        deck_tags = ensure_fallback_tag(deck_tags, evergreen)

        for tag in sorted(deck_tags):
            deck_tag_rows.append(
                OracleDeckTag(
                    oracle_id=oid,
                    tag=tag,
                    category=deck_tag_category(tag),
                    source="derived",
                )
            )

        for keyword in sorted(evergreen):
            evergreen_rows.append(
                OracleEvergreenTag(
                    oracle_id=oid,
                    keyword=keyword,
                    source="derived",
                )
            )

    if deck_tag_rows:
        db.session.bulk_save_objects(deck_tag_rows)
    if evergreen_rows:
        db.session.bulk_save_objects(evergreen_rows)
    db.session.commit()


def recompute_oracle_enrichment() -> None:
    """
    Rebuild oracle-level role, keyword, typal, deck, and evergreen tags using the cache.
    """
    if not _ensure_oracle_cache():
        return

    OracleRoleTag.query.delete(synchronize_session=False)
    OracleKeywordTag.query.delete(synchronize_session=False)
    OracleTypalTag.query.delete(synchronize_session=False)
    OracleDeckTag.query.delete(synchronize_session=False)
    OracleEvergreenTag.query.delete(synchronize_session=False)
    OracleRole.query.delete(synchronize_session=False)

    role_rows = []
    role_tag_rows = []
    keyword_rows = []
    typal_rows = []
    deck_tag_rows = []
    evergreen_rows = []

    for oid, prints in _iter_oracle_prints():
        best = _select_best_print(prints) or (prints[0] if prints else None)
        if not best:
            continue
        oracle_text = _oracle_text_from_print(best)
        type_line = _type_line_from_print(best)
        mock = {
            "name": best.get("name") or "",
            "oracle_text": oracle_text,
            "type_line": type_line,
            "card_faces": best.get("card_faces") or [],
            "layout": best.get("layout") or "",
            "produced_mana": best.get("produced_mana") or [],
        }
        roles = get_roles_for_card(mock)
        subroles = get_subroles_for_card(mock)
        primary = get_primary_role(roles)
        keywords = _collect_keywords(prints)
        typals = _collect_typals(prints)
        land_tags = get_land_tags_for_card(mock)
        evergreen = set(land_tags) if land_tags else derive_evergreen_keywords(oracle_text=oracle_text, keywords=keywords)
        deck_tags = derive_deck_tags(
            oracle_text=oracle_text,
            type_line=type_line,
            keywords=keywords,
            typals=typals,
            roles=roles,
        )
        deck_tags = ensure_fallback_tag(deck_tags, evergreen)

        role_rows.append(
            OracleRole(
                oracle_id=oid,
                name=mock["name"] or None,
                type_line=mock["type_line"] or None,
                primary_role=primary,
                roles=roles,
                subroles=subroles,
            )
        )

        for role in roles:
            role_tag_rows.append(
                OracleRoleTag(
                    oracle_id=oid,
                    role=role,
                    is_primary=(role == primary),
                    source="derived",
                )
            )

        for keyword in sorted(keywords):
            keyword_rows.append(
                OracleKeywordTag(
                    oracle_id=oid,
                    keyword=keyword,
                    source="scryfall",
                )
            )

        for typal in sorted(typals):
            typal_rows.append(
                OracleTypalTag(
                    oracle_id=oid,
                    typal=typal,
                    source="derived",
                )
            )

        for tag in sorted(deck_tags):
            deck_tag_rows.append(
                OracleDeckTag(
                    oracle_id=oid,
                    tag=tag,
                    category=deck_tag_category(tag),
                    source="derived",
                )
            )

        for keyword in sorted(evergreen):
            evergreen_rows.append(
                OracleEvergreenTag(
                    oracle_id=oid,
                    keyword=keyword,
                    source="derived",
                )
            )

    if role_rows:
        db.session.bulk_save_objects(role_rows)
    if role_tag_rows:
        db.session.bulk_save_objects(role_tag_rows)
    if keyword_rows:
        db.session.bulk_save_objects(keyword_rows)
    if typal_rows:
        db.session.bulk_save_objects(typal_rows)
    if deck_tag_rows:
        db.session.bulk_save_objects(deck_tag_rows)
    if evergreen_rows:
        db.session.bulk_save_objects(evergreen_rows)
    db.session.commit()
