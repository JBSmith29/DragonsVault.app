"""Background recomputation tasks for oracle-derived tags and roles."""

from __future__ import annotations

import logging
from typing import Dict, Iterable, Tuple, Set

from extensions import db
from models import Card
from models.role import (
    OracleRole,
    OracleKeywordTag,
    OracleRoleTag,
    OracleTypalTag,
    OracleCoreRoleTag,
    OracleDeckTag,
    OracleEvergreenTag,
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
from shared.jobs.background import (
    oracle_deck_tag_synergy_service,
    oracle_profile_service,
    oracle_role_recompute_service,
)
from sqlalchemy.exc import SQLAlchemyError

_LOG = logging.getLogger(__name__)

ORACLE_DECK_TAG_VERSION = oracle_deck_tag_synergy_service.ORACLE_DECK_TAG_VERSION
oracle_deck_tag_source_version = oracle_deck_tag_synergy_service.oracle_deck_tag_source_version
recompute_deck_tag_synergies = oracle_deck_tag_synergy_service.recompute_deck_tag_synergies


def recompute_all_roles(*, merge_existing: bool = True) -> dict:
    return oracle_role_recompute_service.recompute_all_roles(merge_existing=merge_existing)


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
            analysis = oracle_profile_service.analyze_oracle_prints(
                prints,
                get_land_tags_for_card_fn=get_land_tags_for_card,
                derive_evergreen_keywords_fn=derive_evergreen_keywords,
                derive_core_roles_fn=derive_core_roles,
                core_role_label_fn=core_role_label,
            )
            if not analysis:
                skipped_oracles += 1
                continue
            core_role_tags = analysis["core_role_tags"]
            evergreen = analysis["evergreen"]

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
        deck_tag_rows = oracle_deck_tag_synergy_service.current_deck_tag_rows()
        synergy_summary = {}
        if deck_tag_rows:
            synergy_summary = oracle_deck_tag_synergy_service.recompute_deck_tag_synergies(
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
        "deck_tag_version": oracle_deck_tag_synergy_service.ORACLE_DECK_TAG_VERSION,
        "deck_tag_source_version": oracle_deck_tag_synergy_service.oracle_deck_tag_source_version(),
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
    deck_tag_source_version = oracle_deck_tag_synergy_service.oracle_deck_tag_source_version()
    try:
        for oid, prints in _iter_oracle_prints():
            oracle_id = oid
            assert oracle_id, "oracle_id must exist"
            oracle_count += 1
            analysis = oracle_profile_service.analyze_oracle_prints(
                prints,
                get_land_tags_for_card_fn=get_land_tags_for_card,
                derive_evergreen_keywords_fn=derive_evergreen_keywords,
                derive_core_roles_fn=derive_core_roles,
                core_role_label_fn=core_role_label,
                get_roles_for_card_fn=get_roles_for_card,
                get_subroles_for_card_fn=get_subroles_for_card,
                get_primary_role_fn=get_primary_role,
                derive_deck_tags_fn=derive_deck_tags,
                ensure_fallback_tag_fn=ensure_fallback_tag,
            )
            if not analysis:
                skipped_oracles += 1
                continue
            mock = analysis["mock"]
            roles = analysis["roles"]
            subroles = analysis["subroles"]
            primary = analysis["primary_role"]
            keywords = analysis["keywords"]
            typals = analysis["typals"]
            evergreen = analysis["evergreen"]
            deck_tags = analysis["deck_tags"]
            core_role_tags = analysis["core_role_tags"]

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
                        version=oracle_deck_tag_synergy_service.ORACLE_DECK_TAG_VERSION,
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
            synergy_summary = oracle_deck_tag_synergy_service.recompute_deck_tag_synergies(
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
        "deck_tag_version": oracle_deck_tag_synergy_service.ORACLE_DECK_TAG_VERSION,
        "deck_tag_source_version": deck_tag_source_version,
    }
    _LOG.info(
        "Oracle enrichment recompute completed: oracles=%s deck_tags=%s evergreen=%s",
        oracle_count,
        summary["deck_tags"],
        summary["evergreen"],
    )
    return summary


__all__ = [
    "ORACLE_DECK_TAG_VERSION",
    "oracle_deck_tag_source_version",
    "recompute_all_roles",
    "recompute_deck_tag_synergies",
    "recompute_oracle_deck_tags",
    "recompute_oracle_enrichment",
    "recompute_oracle_roles",
]
