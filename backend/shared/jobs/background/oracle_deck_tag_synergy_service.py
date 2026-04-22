"""Deck-tag synergy helpers for oracle recomputation."""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable

from extensions import db
from models.role import (
    DeckTagCardSynergy,
    DeckTagCoreRoleSynergy,
    DeckTagEvergreenSynergy,
    OracleCoreRoleTag,
    OracleDeckTag,
    OracleEvergreenTag,
)
from core.domains.cards.services import scryfall_cache as sc
from sqlalchemy.exc import SQLAlchemyError
import logging

_LOG = logging.getLogger(__name__)

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


def current_deck_tag_rows() -> list[OracleDeckTag]:
    source_version = oracle_deck_tag_source_version()
    return (
        OracleDeckTag.query.filter(
            OracleDeckTag.version == ORACLE_DECK_TAG_VERSION,
            OracleDeckTag.source_version == source_version,
        )
        .all()
    )


def build_deck_tag_synergy_rows(
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
        deck_tag_rows = list(deck_tag_rows) if deck_tag_rows is not None else current_deck_tag_rows()
        if not deck_tag_rows:
            _LOG.warning("Deck tag synergies skipped: no deck tags available.")
            return {"deck_tags": 0, "core_roles": 0, "evergreen": 0, "cards": 0}
        DeckTagCoreRoleSynergy.query.delete(synchronize_session=False)
        DeckTagEvergreenSynergy.query.delete(synchronize_session=False)
        DeckTagCardSynergy.query.delete(synchronize_session=False)
        core_role_rows = list(core_role_rows) if core_role_rows is not None else OracleCoreRoleTag.query.all()
        evergreen_rows = list(evergreen_rows) if evergreen_rows is not None else OracleEvergreenTag.query.all()

        _LOG.info("Deck tag synergy recompute started (deck_tags=%s).", len(deck_tag_rows))
        core_synergy_rows, evergreen_synergy_rows, card_synergy_rows = build_deck_tag_synergy_rows(
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


__all__ = [
    "ORACLE_DECK_TAG_VERSION",
    "build_deck_tag_synergy_rows",
    "current_deck_tag_rows",
    "oracle_deck_tag_source_version",
    "recompute_deck_tag_synergies",
]
