"""Admin card role and oracle tag renderers."""

from __future__ import annotations

from math import ceil
from typing import Set

from flask import current_app, flash, redirect, render_template, request, url_for
from sqlalchemy import func, or_

from extensions import db
from models.card import Card
from models.role import (
    CardRole,
    DeckTagCardSynergy,
    DeckTagCoreRoleSynergy,
    DeckTagEvergreenSynergy,
    OracleCoreRoleTag,
    OracleDeckTag,
    OracleEvergreenTag,
    OracleRole,
    Role,
)
from core.domains.cards.services import scryfall_cache as sc
from core.domains.cards.services.scryfall_cache import ensure_cache_loaded
from shared.cache.request_cache import request_cached
from shared.jobs.background.oracle_recompute import ORACLE_DECK_TAG_VERSION, oracle_deck_tag_source_version
from worker.tasks import recompute_oracle_deck_tags

_ADMIN_TABLE_PAGE_SIZE = 200

__all__ = [
    "render_admin_card_roles",
    "render_admin_deck_tag_card_synergies",
    "render_admin_deck_tag_core_role_synergies",
    "render_admin_deck_tag_evergreen_synergies",
    "render_admin_oracle_core_roles",
    "render_admin_oracle_deck_tags",
    "render_admin_oracle_evergreen_tags",
    "render_admin_oracle_tags",
]


def render_admin_card_roles():
    q = request.args.get("q") or ""
    query = Card.query
    if q:
        query = query.filter(Card.name.ilike(f"%{q}%"))
    cards = query.order_by(Card.name).all()

    def get_primary(card: Card):
        primary_entry = (
            db.session.query(Role)
            .join(CardRole, CardRole.role_id == Role.id)
            .filter(CardRole.card_id == card.id, CardRole.primary.is_(True))
            .first()
        )
        return primary_entry.label or getattr(primary_entry, "name", None) or primary_entry.key if primary_entry else None

    return render_template(
        "admin/card_roles.html",
        cards=cards,
        q=q,
        get_primary=get_primary,
    )


def _oracle_name_match_subquery(like: str):
    return db.session.query(Card.oracle_id).filter(Card.name.ilike(like)).subquery()


def _oracle_name_map(oracle_ids: Set[str]) -> dict[str, str]:
    if not oracle_ids:
        return {}
    names = (
        db.session.query(OracleRole.oracle_id, OracleRole.name)
        .filter(OracleRole.oracle_id.in_(oracle_ids))
        .all()
    )
    name_map = {oid: name for oid, name in names if name}
    missing = [oid for oid in oracle_ids if oid not in name_map]
    if missing:
        fallback = (
            db.session.query(Card.oracle_id, func.min(Card.name))
            .filter(Card.oracle_id.in_(missing))
            .group_by(Card.oracle_id)
            .all()
        )
        name_map.update({oid: name for oid, name in fallback if name})
    missing = [oid for oid in oracle_ids if oid not in name_map]
    if missing:
        try:
            if ensure_cache_loaded():
                for oid in missing:
                    try:
                        prints = sc.prints_for_oracle(oid) or []
                    except Exception:
                        prints = []
                    if prints:
                        name = prints[0].get("name")
                        if name:
                            name_map[oid] = name
        except Exception:
            pass
    return name_map


def _paginate_query(query, page: int, per_page: int):
    total = query.count()
    pages = max(1, ceil(total / per_page)) if total else 1
    page = max(1, min(page, pages))
    rows = query.limit(per_page).offset((page - 1) * per_page).all()
    return rows, total, page, pages


def _deck_synergy_counts() -> dict[str, int]:
    return request_cached(
        ("deck_synergy", "counts"),
        lambda: {
            "core": DeckTagCoreRoleSynergy.query.count(),
            "evergreen": DeckTagEvergreenSynergy.query.count(),
            "card": DeckTagCardSynergy.query.count(),
        },
    )


def _oracle_deck_tag_query():
    source_version = oracle_deck_tag_source_version()
    return OracleDeckTag.query.filter(
        OracleDeckTag.version == ORACLE_DECK_TAG_VERSION,
        OracleDeckTag.source_version == source_version,
    )


def _requested_page() -> int:
    try:
        return max(1, int(request.args.get("page", 1)))
    except ValueError:
        return 1


def render_admin_oracle_tags():
    if request.method == "POST":
        if not ensure_cache_loaded():
            flash("No Scryfall bulk cache found. Download default cards first.", "warning")
            return redirect(url_for("views.admin_oracle_tags"))
        try:
            recompute_oracle_deck_tags()
            flash("Oracle core roles and evergreen tags refreshed.", "success")
        except Exception as exc:
            current_app.logger.exception("Oracle tag refresh failed")
            flash(f"Failed to refresh oracle tags: {exc}", "danger")
        return redirect(url_for("views.admin_oracle_tags"))

    q = (request.args.get("q") or "").strip()
    core_query = OracleCoreRoleTag.query
    evergreen_query = OracleEvergreenTag.query
    if q:
        like = f"%{q}%"
        name_match = _oracle_name_match_subquery(like)
        core_query = core_query.filter(
            or_(
                OracleCoreRoleTag.role.ilike(like),
                OracleCoreRoleTag.oracle_id.ilike(like),
                OracleCoreRoleTag.oracle_id.in_(name_match),
            )
        )
        evergreen_query = evergreen_query.filter(
            or_(
                OracleEvergreenTag.keyword.ilike(like),
                OracleEvergreenTag.oracle_id.ilike(like),
                OracleEvergreenTag.oracle_id.in_(name_match),
            )
        )

    core_rows = core_query.order_by(OracleCoreRoleTag.role, OracleCoreRoleTag.oracle_id).limit(500).all()
    evergreen_rows = (
        evergreen_query.order_by(OracleEvergreenTag.keyword, OracleEvergreenTag.oracle_id).limit(500).all()
    )

    oracle_ids = {row.oracle_id for row in core_rows} | {row.oracle_id for row in evergreen_rows}
    name_map = _oracle_name_map(oracle_ids)
    synergy_counts = _deck_synergy_counts()

    return render_template(
        "admin/oracle_tags.html",
        core_rows=core_rows,
        evergreen_rows=evergreen_rows,
        core_total=OracleCoreRoleTag.query.count(),
        deck_total=_oracle_deck_tag_query().count(),
        evergreen_total=OracleEvergreenTag.query.count(),
        synergy_core_total=synergy_counts["core"],
        synergy_evergreen_total=synergy_counts["evergreen"],
        synergy_card_total=synergy_counts["card"],
        name_map=name_map,
        q=q,
    )


def render_admin_oracle_core_roles():
    q = (request.args.get("q") or "").strip()
    page = _requested_page()
    query = OracleCoreRoleTag.query
    if q:
        like = f"%{q}%"
        name_match = _oracle_name_match_subquery(like)
        query = query.filter(
            or_(
                OracleCoreRoleTag.role.ilike(like),
                OracleCoreRoleTag.oracle_id.ilike(like),
                OracleCoreRoleTag.oracle_id.in_(name_match),
            )
        )
    query = query.order_by(OracleCoreRoleTag.role, OracleCoreRoleTag.oracle_id)
    cache_key = ("deck_synergy", "core", q, page, _ADMIN_TABLE_PAGE_SIZE)
    rows, total, page, pages = request_cached(
        cache_key,
        lambda: _paginate_query(query, page, _ADMIN_TABLE_PAGE_SIZE),
    )
    name_map = _oracle_name_map({row.oracle_id for row in rows})
    return render_template(
        "admin/oracle_core_roles.html",
        rows=rows,
        total=total,
        page=page,
        pages=pages,
        name_map=name_map,
        q=q,
    )


def render_admin_oracle_evergreen_tags():
    q = (request.args.get("q") or "").strip()
    page = _requested_page()
    query = OracleEvergreenTag.query
    if q:
        like = f"%{q}%"
        name_match = _oracle_name_match_subquery(like)
        query = query.filter(
            or_(
                OracleEvergreenTag.keyword.ilike(like),
                OracleEvergreenTag.oracle_id.ilike(like),
                OracleEvergreenTag.oracle_id.in_(name_match),
            )
        )
    query = query.order_by(OracleEvergreenTag.keyword, OracleEvergreenTag.oracle_id)
    cache_key = ("deck_synergy", "evergreen", q, page, _ADMIN_TABLE_PAGE_SIZE)
    rows, total, page, pages = request_cached(
        cache_key,
        lambda: _paginate_query(query, page, _ADMIN_TABLE_PAGE_SIZE),
    )
    name_map = _oracle_name_map({row.oracle_id for row in rows})
    return render_template(
        "admin/oracle_evergreen_tags.html",
        rows=rows,
        total=total,
        page=page,
        pages=pages,
        name_map=name_map,
        q=q,
    )


def render_admin_oracle_deck_tags():
    q = (request.args.get("q") or "").strip()
    page = _requested_page()
    query = _oracle_deck_tag_query()
    if q:
        like = f"%{q}%"
        name_match = _oracle_name_match_subquery(like)
        query = query.filter(
            or_(
                OracleDeckTag.tag.ilike(like),
                OracleDeckTag.category.ilike(like),
                OracleDeckTag.oracle_id.ilike(like),
                OracleDeckTag.oracle_id.in_(name_match),
            )
        )
    query = query.order_by(OracleDeckTag.tag, OracleDeckTag.category, OracleDeckTag.oracle_id)
    cache_key = ("deck_synergy", "card", q, page, _ADMIN_TABLE_PAGE_SIZE)
    rows, total, page, pages = request_cached(
        cache_key,
        lambda: _paginate_query(query, page, _ADMIN_TABLE_PAGE_SIZE),
    )
    name_map = _oracle_name_map({row.oracle_id for row in rows})
    return render_template(
        "admin/oracle_deck_tags.html",
        rows=rows,
        total=total,
        page=page,
        pages=pages,
        name_map=name_map,
        q=q,
    )


def render_admin_deck_tag_core_role_synergies():
    q = (request.args.get("q") or "").strip()
    page = _requested_page()
    query = DeckTagCoreRoleSynergy.query
    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                DeckTagCoreRoleSynergy.deck_tag.ilike(like),
                DeckTagCoreRoleSynergy.role.ilike(like),
            )
        )
    query = query.order_by(DeckTagCoreRoleSynergy.deck_tag, DeckTagCoreRoleSynergy.role)
    rows, total, page, pages = _paginate_query(query, page, _ADMIN_TABLE_PAGE_SIZE)
    return render_template(
        "admin/deck_tag_core_role_synergies.html",
        rows=rows,
        total=total,
        page=page,
        pages=pages,
        q=q,
    )


def render_admin_deck_tag_evergreen_synergies():
    q = (request.args.get("q") or "").strip()
    page = _requested_page()
    query = DeckTagEvergreenSynergy.query
    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                DeckTagEvergreenSynergy.deck_tag.ilike(like),
                DeckTagEvergreenSynergy.keyword.ilike(like),
            )
        )
    query = query.order_by(DeckTagEvergreenSynergy.deck_tag, DeckTagEvergreenSynergy.keyword)
    rows, total, page, pages = _paginate_query(query, page, _ADMIN_TABLE_PAGE_SIZE)
    return render_template(
        "admin/deck_tag_evergreen_synergies.html",
        rows=rows,
        total=total,
        page=page,
        pages=pages,
        q=q,
    )


def render_admin_deck_tag_card_synergies():
    q = (request.args.get("q") or "").strip()
    page = _requested_page()
    query = DeckTagCardSynergy.query
    if q:
        like = f"%{q}%"
        name_match = _oracle_name_match_subquery(like)
        query = query.filter(
            or_(
                DeckTagCardSynergy.deck_tag.ilike(like),
                DeckTagCardSynergy.oracle_id.ilike(like),
                DeckTagCardSynergy.oracle_id.in_(name_match),
            )
        )
    query = query.order_by(DeckTagCardSynergy.deck_tag, DeckTagCardSynergy.oracle_id)
    rows, total, page, pages = _paginate_query(query, page, _ADMIN_TABLE_PAGE_SIZE)
    name_map = _oracle_name_map({row.oracle_id for row in rows})
    return render_template(
        "admin/deck_tag_card_synergies.html",
        rows=rows,
        total=total,
        page=page,
        pages=pages,
        name_map=name_map,
        q=q,
    )
