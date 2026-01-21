"""Background recomputation tasks for oracle-derived tags and roles."""

from __future__ import annotations

from collections import Counter, defaultdict
import logging
from pathlib import Path
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
    OracleCoreRoleTag,
    OracleDeckTag,
    OracleEvergreenTag,
    DeckTagCoreRoleSynergy,
    DeckTagEvergreenSynergy,
    DeckTagCardSynergy,
)
from roles.role_engine import get_primary_role, get_roles_for_card, get_subroles_for_card, get_land_tags_for_card
from core.domains.decks.services.oracle_tagging import (
    derive_deck_tags,
    derive_evergreen_keywords,
    deck_tag_category,
    ensure_fallback_tag,
    evergreen_source,
)
from core.domains.decks.services.core_role_logic import core_role_label, derive_core_roles
from core.domains.cards.services import scryfall_cache as sc
from sqlalchemy.exc import SQLAlchemyError

_DASH = "\u2014"
_EXCLUDED_SET_TYPES = {"token", "memorabilia", "art_series"}
_TYPAL_TRIGGER_TYPES = {"creature", "tribal", "kindred"}
_TYPE_LINE_SKIP_TOKENS = {
    "artifact",
    "battle",
    "basic",
    "creature",
    "enchantment",
    "instant",
    "kindred",
    "land",
    "legendary",
    "ongoing",
    "planeswalker",
    "scheme",
    "snow",
    "sorcery",
    "token",
    "tribal",
    "vanguard",
    "world",
}
_DECK_TAG_SYNERGY_SOURCE = "derived_synergy_v1"
_DECK_TAG_SYNERGY_MIN_COUNT = 3
_DECK_TAG_SYNERGY_MIN_LIFT = 1.15
_DECK_TAG_SYNERGY_MAX_ROLES = 12
_DECK_TAG_SYNERGY_MAX_EVERGREEN = 20
_DECK_TAG_SYNERGY_MAX_CARDS = 150
_DECK_TAG_SYNERGY_ROLE_WEIGHT = 1.0
_DECK_TAG_SYNERGY_EVERGREEN_WEIGHT = 0.7
_DECK_TAG_SYNERGY_TAG_BONUS = 1.5
_DECK_TAG_SYNERGY_MIN_CARD_SCORE = 2.0
_DECK_TAG_SYNERGY_MIN_NON_TAG_MATCHES = 2
ORACLE_DECK_TAG_VERSION = 1

_LOG = logging.getLogger(__name__)


def oracle_deck_tag_source_version() -> str:
    try:
        path = sc.default_cards_path()
        p = Path(path)
        if not p.exists():
            return "default_cards:missing"
        stat = p.stat()
        return f"default_cards:{int(stat.st_mtime)}:{stat.st_size}"
    except Exception:
        return "default_cards:unknown"


def _current_deck_tag_rows() -> list[OracleDeckTag]:
    source_version = oracle_deck_tag_source_version()
    return (
        OracleDeckTag.query.filter(
            OracleDeckTag.version == ORACLE_DECK_TAG_VERSION,
            OracleDeckTag.source_version == source_version,
        )
        .all()
    )


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
        if not token:
            continue
        if not any(char.isalpha() for char in token):
            continue
        token_norm = token.lower()
        if token_norm in _TYPE_LINE_SKIP_TOKENS:
            continue
        out.add(token_norm)
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
            roles_written += len(roles)

            for subrole_key in subroles:
                if not subrole_key:
                    continue
                parent_key, _, child_key = subrole_key.partition(":")
                parent = _get_or_create_role(parent_key or "utility", role_cache)
                subrole = _get_or_create_subrole(parent, child_key or subrole_key, subrole_cache)
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


def recompute_oracle_roles() -> dict:
    """
    Rebuild oracle-level role mappings using the local Scryfall cache.
    """
    return recompute_oracle_enrichment()


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


def _build_deck_tag_synergy_rows(
    deck_tag_rows: Iterable[OracleDeckTag],
    core_role_rows: Iterable[OracleCoreRoleTag],
    evergreen_rows: Iterable[OracleEvergreenTag],
) -> tuple[list[DeckTagCoreRoleSynergy], list[DeckTagEvergreenSynergy], list[DeckTagCardSynergy]]:
    deck_to_oracles: dict[str, set[str]] = defaultdict(set)
    for row in deck_tag_rows:
        if row and row.tag and row.oracle_id:
            deck_to_oracles[row.tag].add(row.oracle_id)

    role_by_oracle: dict[str, set[str]] = defaultdict(set)
    role_to_oracles: dict[str, set[str]] = defaultdict(set)
    for row in core_role_rows:
        if row and row.role and row.oracle_id:
            role_by_oracle[row.oracle_id].add(row.role)
            role_to_oracles[row.role].add(row.oracle_id)

    evergreen_by_oracle: dict[str, set[str]] = defaultdict(set)
    evergreen_to_oracles: dict[str, set[str]] = defaultdict(set)
    for row in evergreen_rows:
        if row and row.keyword and row.oracle_id:
            evergreen_by_oracle[row.oracle_id].add(row.keyword)
            evergreen_to_oracles[row.keyword].add(row.oracle_id)

    all_oracles: set[str] = set()
    for oids in deck_to_oracles.values():
        all_oracles.update(oids)
    all_oracles.update(role_by_oracle.keys())
    all_oracles.update(evergreen_by_oracle.keys())
    total_oracles = max(len(all_oracles), 1)

    global_role_counts = {role: len(oids) for role, oids in role_to_oracles.items()}
    global_evergreen_counts = {kw: len(oids) for kw, oids in evergreen_to_oracles.items()}

    core_synergy_rows: list[DeckTagCoreRoleSynergy] = []
    evergreen_synergy_rows: list[DeckTagEvergreenSynergy] = []
    card_synergy_rows: list[DeckTagCardSynergy] = []

    for deck_tag, base_oracles in deck_to_oracles.items():
        total = len(base_oracles)
        if total == 0:
            continue

        role_counts: Counter[str] = Counter()
        for oid in base_oracles:
            for role in role_by_oracle.get(oid, set()):
                role_counts[role] += 1

        role_scores: list[tuple[float, int, str]] = []
        for role, count in role_counts.items():
            if count < _DECK_TAG_SYNERGY_MIN_COUNT:
                continue
            global_count = global_role_counts.get(role, 0)
            if not global_count:
                continue
            support = count / total
            global_support = global_count / total_oracles
            lift = support / global_support if global_support else 0.0
            if lift < _DECK_TAG_SYNERGY_MIN_LIFT:
                continue
            role_scores.append((lift, count, role))
        role_scores.sort(key=lambda item: (-item[0], -item[1], item[2]))
        role_scores = role_scores[:_DECK_TAG_SYNERGY_MAX_ROLES]

        selected_roles = {role for _, _, role in role_scores}
        for lift, _, role in role_scores:
            core_synergy_rows.append(
                DeckTagCoreRoleSynergy(
                    deck_tag=deck_tag,
                    role=role,
                    weight=round(lift, 4),
                    source=_DECK_TAG_SYNERGY_SOURCE,
                )
            )

        evergreen_counts: Counter[str] = Counter()
        for oid in base_oracles:
            for keyword in evergreen_by_oracle.get(oid, set()):
                evergreen_counts[keyword] += 1

        evergreen_scores: list[tuple[float, int, str]] = []
        for keyword, count in evergreen_counts.items():
            if count < _DECK_TAG_SYNERGY_MIN_COUNT:
                continue
            global_count = global_evergreen_counts.get(keyword, 0)
            if not global_count:
                continue
            support = count / total
            global_support = global_count / total_oracles
            lift = support / global_support if global_support else 0.0
            if lift < _DECK_TAG_SYNERGY_MIN_LIFT:
                continue
            evergreen_scores.append((lift, count, keyword))
        evergreen_scores.sort(key=lambda item: (-item[0], -item[1], item[2]))
        evergreen_scores = evergreen_scores[:_DECK_TAG_SYNERGY_MAX_EVERGREEN]

        selected_evergreen = {keyword for _, _, keyword in evergreen_scores}
        for lift, _, keyword in evergreen_scores:
            evergreen_synergy_rows.append(
                DeckTagEvergreenSynergy(
                    deck_tag=deck_tag,
                    keyword=keyword,
                    weight=round(lift, 4),
                    source=_DECK_TAG_SYNERGY_SOURCE,
                )
            )

        candidate_oracles: set[str] = set(base_oracles)
        for role in selected_roles:
            candidate_oracles.update(role_to_oracles.get(role, set()))
        for keyword in selected_evergreen:
            candidate_oracles.update(evergreen_to_oracles.get(keyword, set()))

        card_scores: list[tuple[float, int, int, str]] = []
        for oid in candidate_oracles:
            role_matches = len(role_by_oracle.get(oid, set()) & selected_roles)
            evergreen_matches = len(evergreen_by_oracle.get(oid, set()) & selected_evergreen)
            if oid not in base_oracles and (role_matches + evergreen_matches) < _DECK_TAG_SYNERGY_MIN_NON_TAG_MATCHES:
                continue
            score = 0.0
            if oid in base_oracles:
                score += _DECK_TAG_SYNERGY_TAG_BONUS
            score += role_matches * _DECK_TAG_SYNERGY_ROLE_WEIGHT
            score += evergreen_matches * _DECK_TAG_SYNERGY_EVERGREEN_WEIGHT
            if score < _DECK_TAG_SYNERGY_MIN_CARD_SCORE:
                continue
            card_scores.append((score, role_matches, evergreen_matches, oid))

        card_scores.sort(key=lambda item: (-item[0], -item[1], -item[2], item[3]))
        for score, _, _, oid in card_scores[:_DECK_TAG_SYNERGY_MAX_CARDS]:
            card_synergy_rows.append(
                DeckTagCardSynergy(
                    deck_tag=deck_tag,
                    oracle_id=oid,
                    weight=round(score, 4),
                    source=_DECK_TAG_SYNERGY_SOURCE,
                )
            )

    return core_synergy_rows, evergreen_synergy_rows, card_synergy_rows


def recompute_deck_tag_synergies(
    *,
    deck_tag_rows: Iterable[OracleDeckTag] | None = None,
    core_role_rows: Iterable[OracleCoreRoleTag] | None = None,
    evergreen_rows: Iterable[OracleEvergreenTag] | None = None,
) -> dict:
    try:
        deck_tag_rows = list(deck_tag_rows) if deck_tag_rows is not None else _current_deck_tag_rows()
        if not deck_tag_rows:
            _LOG.warning("Deck tag synergies skipped: no deck tags available.")
            return {"deck_tags": 0, "core_roles": 0, "evergreen": 0, "cards": 0}
        DeckTagCoreRoleSynergy.query.delete(synchronize_session=False)
        DeckTagEvergreenSynergy.query.delete(synchronize_session=False)
        DeckTagCardSynergy.query.delete(synchronize_session=False)
        core_role_rows = list(core_role_rows) if core_role_rows is not None else OracleCoreRoleTag.query.all()
        evergreen_rows = list(evergreen_rows) if evergreen_rows is not None else OracleEvergreenTag.query.all()

        _LOG.info("Deck tag synergy recompute started (deck_tags=%s).", len(deck_tag_rows))
        core_synergy_rows, evergreen_synergy_rows, card_synergy_rows = _build_deck_tag_synergy_rows(
            deck_tag_rows,
            core_role_rows,
            evergreen_rows,
        )
        if core_synergy_rows:
            db.session.bulk_save_objects(core_synergy_rows)
        if evergreen_synergy_rows:
            db.session.bulk_save_objects(evergreen_synergy_rows)
        if card_synergy_rows:
            db.session.bulk_save_objects(card_synergy_rows)
        summary = {
            "deck_tags": len(deck_tag_rows),
            "core_roles": len(core_synergy_rows),
            "evergreen": len(evergreen_synergy_rows),
            "cards": len(card_synergy_rows),
        }
        _LOG.info(
            "Deck tag synergy recompute completed: deck_tags=%s core_roles=%s evergreen=%s cards=%s",
            summary["deck_tags"],
            summary["core_roles"],
            summary["evergreen"],
            summary["cards"],
        )
        return summary
    except SQLAlchemyError:
        db.session.rollback()
        _LOG.error("Deck tag synergy recompute failed due to database error.", exc_info=True)
        raise
    except Exception:
        db.session.rollback()
        _LOG.error("Deck tag synergy recompute failed.", exc_info=True)
        raise


def recompute_oracle_deck_tags() -> dict:
    """
    Rebuild oracle-level core roles and evergreen tags using the Scryfall cache.
    """
    _LOG.info("Oracle deck tag recompute started.")
    if not _ensure_oracle_cache():
        _LOG.warning("Oracle deck tag recompute skipped: cache_unavailable.")
        return {"status": "skipped", "reason": "cache_unavailable"}

    OracleCoreRoleTag.query.delete(synchronize_session=False)
    OracleEvergreenTag.query.delete(synchronize_session=False)

    core_role_rows = []
    evergreen_rows = []
    evergreen_src = evergreen_source()

    oracle_count = 0
    skipped_oracles = 0
    try:
        for oid, prints in _iter_oracle_prints():
            oracle_id = oid
            assert oracle_id, "oracle_id must exist"
            oracle_count += 1
            best = _select_best_print(prints) or (prints[0] if prints else None)
            if not best:
                skipped_oracles += 1
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
            keywords = _collect_keywords(prints)
            typals = _collect_typals(prints)
            land_tags = get_land_tags_for_card(mock)
            evergreen = derive_evergreen_keywords(
                oracle_text=oracle_text,
                type_line=type_line,
                name=mock["name"],
                keywords=keywords,
                typals=typals,
                colors=best.get("color_identity") or best.get("colors"),
            )
            if land_tags:
                evergreen |= set(land_tags)
            core_roles = derive_core_roles(
                oracle_text=oracle_text,
                type_line=type_line,
                name=mock["name"],
            )
            core_role_tags: Set[str] = set()
            for role in core_roles:
                label = core_role_label(role)
                if label:
                    core_role_tags.add(label)

            for tag in sorted(core_role_tags):
                core_role_rows.append(
                    OracleCoreRoleTag(
                        oracle_id=oracle_id,
                        role=tag,
                        source="core-role",
                    )
                )

            for keyword in sorted(evergreen):
                evergreen_rows.append(
                    OracleEvergreenTag(
                        oracle_id=oracle_id,
                        keyword=keyword,
                        source=evergreen_src,
                    )
                )

        if core_role_rows:
            db.session.bulk_save_objects(core_role_rows)
        if evergreen_rows:
            db.session.bulk_save_objects(evergreen_rows)
        deck_tag_rows = _current_deck_tag_rows()
        synergy_summary = {}
        if deck_tag_rows:
            synergy_summary = recompute_deck_tag_synergies(
                deck_tag_rows=deck_tag_rows,
                core_role_rows=core_role_rows,
                evergreen_rows=evergreen_rows,
            )
        db.session.commit()
    except AssertionError as exc:
        db.session.rollback()
        _LOG.error("Oracle deck tag recompute failed: %s", exc)
        raise
    except SQLAlchemyError:
        db.session.rollback()
        _LOG.error("Oracle deck tag recompute failed due to database error.", exc_info=True)
        raise
    except Exception:
        db.session.rollback()
        _LOG.error("Oracle deck tag recompute failed.", exc_info=True)
        raise

    if skipped_oracles:
        _LOG.warning("Oracle deck tag recompute skipped %s oracles without prints.", skipped_oracles)

    summary = {
        "status": "ok",
        "oracles_scanned": oracle_count,
        "core_roles": len(core_role_rows),
        "evergreen": len(evergreen_rows),
        "deck_tags": len(deck_tag_rows) if deck_tag_rows else 0,
        "synergies": synergy_summary,
        "deck_tag_version": ORACLE_DECK_TAG_VERSION,
        "deck_tag_source_version": oracle_deck_tag_source_version(),
    }
    _LOG.info(
        "Oracle deck tag recompute completed: oracles=%s core_roles=%s evergreen=%s",
        oracle_count,
        summary["core_roles"],
        summary["evergreen"],
    )
    return summary


def recompute_oracle_enrichment() -> dict:
    """
    Rebuild oracle-level role, keyword, typal, core role, deck, and evergreen tags using the cache.
    """
    _LOG.info("Oracle enrichment recompute started.")
    if not _ensure_oracle_cache():
        _LOG.warning("Oracle enrichment recompute skipped: cache_unavailable.")
        return {"status": "skipped", "reason": "cache_unavailable"}

    OracleRoleTag.query.delete(synchronize_session=False)
    OracleKeywordTag.query.delete(synchronize_session=False)
    OracleTypalTag.query.delete(synchronize_session=False)
    OracleCoreRoleTag.query.delete(synchronize_session=False)
    OracleDeckTag.query.delete(synchronize_session=False)
    OracleEvergreenTag.query.delete(synchronize_session=False)
    OracleRole.query.delete(synchronize_session=False)

    role_rows = []
    role_tag_rows = []
    keyword_rows = []
    typal_rows = []
    core_role_rows = []
    deck_tag_rows = []
    evergreen_rows = []
    evergreen_src = evergreen_source()

    oracle_count = 0
    skipped_oracles = 0
    deck_tag_source_version = oracle_deck_tag_source_version()
    try:
        for oid, prints in _iter_oracle_prints():
            oracle_id = oid
            assert oracle_id, "oracle_id must exist"
            oracle_count += 1
            best = _select_best_print(prints) or (prints[0] if prints else None)
            if not best:
                skipped_oracles += 1
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
            evergreen = derive_evergreen_keywords(
                oracle_text=oracle_text,
                type_line=type_line,
                name=mock["name"],
                keywords=keywords,
                typals=typals,
                colors=best.get("color_identity") or best.get("colors"),
            )
            if land_tags:
                evergreen |= set(land_tags)
            deck_tags = derive_deck_tags(
                oracle_text=oracle_text,
                type_line=type_line,
                keywords=keywords,
                typals=typals,
                roles=roles,
            )
            core_roles = derive_core_roles(
                oracle_text=oracle_text,
                type_line=type_line,
                name=mock["name"],
            )
            core_role_tags: Set[str] = set()
            for role in core_roles:
                label = core_role_label(role)
                if label:
                    core_role_tags.add(label)
            if not core_role_tags:
                deck_tags = ensure_fallback_tag(deck_tags, evergreen)

            role_rows.append(
                OracleRole(
                    oracle_id=oracle_id,
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
                        oracle_id=oracle_id,
                        role=role,
                        is_primary=(role == primary),
                        source="derived",
                    )
                )

            for keyword in sorted(keywords):
                keyword_rows.append(
                    OracleKeywordTag(
                        oracle_id=oracle_id,
                        keyword=keyword,
                        source="scryfall",
                    )
                )

            for typal in sorted(typals):
                typal_rows.append(
                    OracleTypalTag(
                        oracle_id=oracle_id,
                        typal=typal,
                        source="derived",
                    )
                )

            for tag in sorted(deck_tags):
                deck_tag_rows.append(
                    OracleDeckTag(
                        oracle_id=oracle_id,
                        tag=tag,
                        category=deck_tag_category(tag),
                        source="derived",
                        version=ORACLE_DECK_TAG_VERSION,
                        source_version=deck_tag_source_version,
                    )
                )

            for tag in sorted(core_role_tags):
                core_role_rows.append(
                    OracleCoreRoleTag(
                        oracle_id=oracle_id,
                        role=tag,
                        source="core-role",
                    )
                )

            for keyword in sorted(evergreen):
                evergreen_rows.append(
                    OracleEvergreenTag(
                        oracle_id=oracle_id,
                        keyword=keyword,
                        source=evergreen_src,
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
        if core_role_rows:
            db.session.bulk_save_objects(core_role_rows)
        if deck_tag_rows:
            db.session.bulk_save_objects(deck_tag_rows)
        if evergreen_rows:
            db.session.bulk_save_objects(evergreen_rows)
        synergy_summary = {}
        if deck_tag_rows and (core_role_rows or evergreen_rows):
            synergy_summary = recompute_deck_tag_synergies(
                deck_tag_rows=deck_tag_rows,
                core_role_rows=core_role_rows,
                evergreen_rows=evergreen_rows,
            )
        db.session.commit()
    except AssertionError as exc:
        db.session.rollback()
        _LOG.error("Oracle enrichment recompute failed: %s", exc)
        raise
    except SQLAlchemyError:
        db.session.rollback()
        _LOG.error("Oracle enrichment recompute failed due to database error.", exc_info=True)
        raise
    except Exception:
        db.session.rollback()
        _LOG.error("Oracle enrichment recompute failed.", exc_info=True)
        raise

    if skipped_oracles:
        _LOG.warning("Oracle enrichment recompute skipped %s oracles without prints.", skipped_oracles)

    summary = {
        "status": "ok",
        "oracles_scanned": oracle_count,
        "oracle_roles": len(role_rows),
        "role_tags": len(role_tag_rows),
        "keywords": len(keyword_rows),
        "typals": len(typal_rows),
        "core_roles": len(core_role_rows),
        "deck_tags": len(deck_tag_rows),
        "evergreen": len(evergreen_rows),
        "synergies": synergy_summary,
        "deck_tag_version": ORACLE_DECK_TAG_VERSION,
        "deck_tag_source_version": deck_tag_source_version,
    }
    _LOG.info(
        "Oracle enrichment recompute completed: oracles=%s deck_tags=%s evergreen=%s",
        oracle_count,
        summary["deck_tags"],
        summary["evergreen"],
    )
    return summary
