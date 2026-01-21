"""Build session workflow helpers (proxy-only)."""

from __future__ import annotations

import logging
import os
import re
import threading
import uuid
from collections import defaultdict
from typing import Iterable

from flask import abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user

from sqlalchemy import inspect, text

from extensions import db
from models import BuildSession, BuildSessionCard, Card, Folder, FolderRole, OracleCoreRoleTag, OracleDeckTag
from services import scryfall_cache as sc
from services.build_recommendation_service import build_recommendation_sections
from services.commander_brackets import BRACKET_RULESET_EPOCH, evaluate_commander_bracket, spellbook_dataset_epoch
from services.commander_cache import compute_bracket_signature
from services.deck_tags import get_deck_tag_category, get_deck_tag_groups, resolve_deck_tag_from_slug
from services.edhrec_cache_service import (
    get_commander_synergy,
    get_commander_type_distribution,
)
from services.edhrec.edhrec_ingestion_service import ingest_commander_tag_data
from services.live_updates import emit_job_event, latest_job_events
from services.request_cache import request_cached
from services.symbols_cache import colors_to_icons, render_mana_html
from utils.assets import static_url

_LOG = logging.getLogger(__name__)


def build_session_page(session_id: int):
    session = _get_session(session_id)
    if session is None:
        abort(404)

    tags = _normalized_tags(session.tags_json)
    commander = _oracle_payload(session.commander_oracle_id, fallback=session.commander_name)
    cards = _session_cards(session.cards or [])
    build_oracle_ids = _build_oracle_ids(session.cards or [])
    cards_by_type = _group_session_cards_by_type(cards)
    metrics = _deck_metrics(session.cards or [])
    deck_type_breakdown = _type_breakdown_for_entries(session.cards or [])
    deck_type_distribution = _distribution_breakdown_for_entries(session.cards or [])
    edhrec_type_distribution = _edhrec_type_breakdown(
        session.commander_oracle_id or "",
        tags,
    )
    build_bracket = _build_session_bracket_context(session, session.cards or [])
    sort_mode = _normalize_sort_mode(request.args.get("sort"))
    build_view = _normalize_build_view(request.args.get("build_view"))
    rec_source = (request.args.get("rec_source") or "edhrec").strip().lower()
    edhrec_job_id = (request.args.get("edhrec_job_id") or "").strip() or None
    if rec_source not in {"edhrec", "collection"}:
        rec_source = "edhrec"
    tag_groups = get_deck_tag_groups()
    recommendations = build_recommendation_sections(
        session.commander_oracle_id or "",
        tags,
        role_needs=metrics["role_needs"],
        sort_mode=sort_mode,
    )
    collection_oracles = _collection_oracle_ids(current_user.id)
    _mark_collection_cards(recommendations, collection_oracles)
    edhrec_oracles = _recommendation_oracle_ids(recommendations)
    collection_sections = _collection_recommendation_sections(
        session.commander_oracle_id or "",
        tags,
        collection_oracles,
        metrics["role_needs"],
        exclude_oracles=edhrec_oracles,
        sort_mode=sort_mode,
    )
    _mark_build_cards(recommendations, build_oracle_ids)
    _mark_build_cards(collection_sections, build_oracle_ids)

    return render_template(
        "decks/build_session.html",
        build_session=session,
        commander=commander,
        tags=tags,
        tag_groups=tag_groups,
        recommendations=recommendations,
        collection_sections=collection_sections,
        deck_metrics=metrics,
        deck_type_breakdown=deck_type_breakdown,
        deck_type_distribution=deck_type_distribution,
        edhrec_type_distribution=edhrec_type_distribution,
        mana_pip_dist=metrics["mana_pip_dist"],
        land_mana_sources=metrics["land_mana_sources"],
        sort_mode=sort_mode,
        rec_source=rec_source,
        build_view=build_view,
        edhrec_estimate_seconds=_edhrec_estimate_seconds(tags),
        edhrec_job_id=edhrec_job_id,
        phase=metrics["phase"],
        session_cards=cards,
        session_cards_by_type=cards_by_type,
        build_bracket=build_bracket,
    )


def api_build_session_insight(session_id: int):
    session = _get_session(session_id)
    if session is None:
        abort(404)
    payload = _build_session_drawer_summary(session)
    return jsonify(payload)


def start_build_session():
    commander_oracle_id = (request.form.get("commander_oracle_id") or "").strip()
    commander_name = (request.form.get("commander_name") or "").strip()
    build_name = (request.form.get("build_name") or "").strip()
    tags = _normalized_tags(_collect_tags_from_request())
    if not commander_oracle_id and commander_name:
        try:
            sc.ensure_cache_loaded()
            commander_oracle_id = sc.unique_oracle_by_name(commander_name) or ""
        except Exception:
            commander_oracle_id = ""
        if commander_oracle_id:
            commander_name = _oracle_name(commander_oracle_id) or commander_name
    if not commander_oracle_id:
        flash("Commander not found. Please check the name and try again.", "warning")
        return redirect(url_for("views.build_landing"))

    _ensure_build_session_tables()

    commander_name = commander_name or _oracle_name(commander_oracle_id)
    session = BuildSession(
        owner_user_id=current_user.id,
        commander_oracle_id=commander_oracle_id,
        commander_name=commander_name,
        build_name=build_name or None,
        tags_json=tags or None,
    )
    db.session.add(session)
    if commander_oracle_id:
        db.session.add(
            BuildSessionCard(
                session=session,
                card_oracle_id=commander_oracle_id,
                quantity=1,
            )
        )
    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        _LOG.error("Failed to create build session: %s", exc)
        flash("Unable to start build session. Please try again.", "danger")
        return redirect(url_for("views.build_landing"))

    return redirect(url_for("views.build_session", session_id=session.id))


def add_card(session_id: int):
    session = _get_session(session_id)
    if session is None:
        abort(404)
    oracle_id = (request.form.get("card_oracle_id") or "").strip()
    if not oracle_id:
        return _redirect_session(session_id)

    entry = BuildSessionCard.query.filter_by(session_id=session.id, card_oracle_id=oracle_id).first()
    if entry:
        entry.quantity = int(entry.quantity or 0) + 1
    else:
        entry = BuildSessionCard(session_id=session.id, card_oracle_id=oracle_id, quantity=1)
        db.session.add(entry)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash("Unable to add card to the build session.", "danger")
    return _redirect_session(session_id)


def add_cards_bulk(session_id: int):
    session = _get_session(session_id)
    if session is None:
        abort(404)
    oracle_ids = [oid.strip() for oid in request.form.getlist("card_oracle_id") if oid]
    if not oracle_ids:
        flash("No cards selected to add.", "warning")
        return _redirect_session(session_id)

    unique_oracles = []
    seen: set[str] = set()
    for oracle_id in oracle_ids:
        key = oracle_id.casefold()
        if key in seen:
            continue
        seen.add(key)
        unique_oracles.append(oracle_id)

    for oracle_id in unique_oracles:
        entry = BuildSessionCard.query.filter_by(session_id=session.id, card_oracle_id=oracle_id).first()
        if entry:
            entry.quantity = int(entry.quantity or 0) + 1
        else:
            db.session.add(BuildSessionCard(session_id=session.id, card_oracle_id=oracle_id, quantity=1))
    try:
        db.session.commit()
        flash(f"Added {len(unique_oracles)} cards to the build.", "success")
    except Exception:
        db.session.rollback()
        flash("Unable to add cards to the build session.", "danger")
    return _redirect_session(session_id)


def remove_card(session_id: int):
    session = _get_session(session_id)
    if session is None:
        abort(404)
    oracle_id = (request.form.get("card_oracle_id") or "").strip()
    if not oracle_id:
        return _redirect_session(session_id)

    entry = BuildSessionCard.query.filter_by(session_id=session.id, card_oracle_id=oracle_id).first()
    if not entry:
        return _redirect_session(session_id)
    if (entry.quantity or 0) > 1:
        entry.quantity = int(entry.quantity or 0) - 1
    else:
        db.session.delete(entry)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash("Unable to remove card from the build session.", "danger")
    return _redirect_session(session_id)


def update_quantity(session_id: int):
    session = _get_session(session_id)
    if session is None:
        abort(404)
    oracle_id = (request.form.get("card_oracle_id") or "").strip()
    if not oracle_id:
        return _redirect_session(session_id)
    try:
        quantity = int(request.form.get("quantity") or 0)
    except (TypeError, ValueError):
        quantity = 0

    entry = BuildSessionCard.query.filter_by(session_id=session.id, card_oracle_id=oracle_id).first()
    if not entry:
        return _redirect_session(session_id)

    if quantity <= 0:
        db.session.delete(entry)
    else:
        entry.quantity = quantity
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash("Unable to update card quantity.", "danger")
    return _redirect_session(session_id)


def update_tags(session_id: int):
    session = _get_session(session_id)
    if session is None:
        abort(404)
    tags = _normalized_tags(_collect_tags_from_request())
    session.tags_json = tags or None
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash("Unable to update tags for this build session.", "danger")
    return _redirect_session(session_id)


def update_name(session_id: int):
    session = _get_session(session_id)
    if session is None:
        abort(404)
    build_name = (request.form.get("build_name") or "").strip()
    session.build_name = build_name or None
    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        _LOG.error("Failed to update build session name: %s", exc)
        flash("Unable to update build name. Please try again.", "danger")
    return _redirect_session(session_id)


def delete_session(session_id: int):
    session = _get_session(session_id)
    if session is None:
        abort(404)
    db.session.delete(session)
    try:
        db.session.commit()
        flash("Build session deleted.", "success")
    except Exception as exc:
        db.session.rollback()
        _LOG.error("Failed to delete build session: %s", exc)
        flash("Unable to delete build session. Please try again.", "danger")
    return redirect(url_for("views.build_landing"))


def refresh_edhrec(session_id: int):
    session = _get_session(session_id)
    if session is None:
        abort(404)
    wants_json = (
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or request.accept_mimetypes["application/json"] >= request.accept_mimetypes["text/html"]
    )
    commander_oracle_id = (session.commander_oracle_id or "").strip()
    commander_name = (session.commander_name or "").strip()
    if not commander_oracle_id and not commander_name:
        message = "Set a commander before loading EDHREC data."
        if wants_json:
            return jsonify({"ok": False, "error": message}), 400
        flash(message, "warning")
        return _redirect_session(session_id)

    tags = _normalized_tags(session.tags_json)
    requested_tag = (request.form.get("deck_tag") or "").strip()
    if requested_tag:
        tags = [requested_tag]
    if not tags:
        message = "Set at least one deck tag before loading EDHREC data."
        if wants_json:
            return jsonify({"ok": False, "error": message}), 400
        flash(message, "warning")
        return _redirect_session(session_id)
    job_id = _enqueue_edhrec_refresh_job(
        session_id=session_id,
        commander_oracle_id=commander_oracle_id,
        commander_name=commander_name or None,
        tags=tags[:1],
    )
    status_url = url_for("views.build_session_edhrec_status", session_id=session_id, job_id=job_id)
    if wants_json:
        resp = jsonify({"ok": True, "job_id": job_id, "status_url": status_url})
        resp.status_code = 202
        return resp
    flash("EDHREC refresh queued. Leave this page open for updates.", "info")
    return _redirect_session(session_id, extra_params={"edhrec_job_id": job_id})


def edhrec_status(session_id: int):
    session = _get_session(session_id)
    if session is None:
        abort(404)
    job_id = (request.args.get("job_id") or "").strip()
    if not job_id:
        return jsonify({"ok": False, "error": "job_id is required"}), 400
    events = latest_job_events("build_edhrec", dataset=_edhrec_job_dataset(session_id))
    user_id = current_user.id if current_user.is_authenticated else None
    filtered = []
    for event in events:
        if event.get("job_id") != job_id:
            continue
        event_user_id = event.get("user_id")
        if user_id is not None and event_user_id not in (None, user_id):
            continue
        filtered.append(event)
    return jsonify({"ok": True, "job_id": job_id, "events": filtered})


def _enqueue_edhrec_refresh_job(
    *,
    session_id: int,
    commander_oracle_id: str,
    commander_name: str | None,
    tags: list[str],
) -> str:
    job_id = uuid.uuid4().hex
    dataset = _edhrec_job_dataset(session_id)
    user_id = current_user.id if current_user.is_authenticated else None
    tag_label = tags[0] if tags else ""
    emit_job_event(
        "build_edhrec",
        "queued",
        job_id=job_id,
        dataset=dataset,
        session_id=session_id,
        user_id=user_id,
        commander_oracle_id=commander_oracle_id,
        commander_name=commander_name or "",
        tag=tag_label,
    )

    def _runner():
        from app import create_app

        app = create_app()
        with app.app_context():
            emit_job_event(
                "build_edhrec",
                "started",
                job_id=job_id,
                dataset=dataset,
                session_id=session_id,
                user_id=user_id,
            )
            try:
                result = ingest_commander_tag_data(
                    commander_oracle_id,
                    commander_name,
                    tags,
                    force_refresh=False,
                )
                status = result.get("status")
                message = result.get("message") or "EDHREC refresh completed."
            except Exception as exc:
                _LOG.exception("EDHREC refresh failed for build session %s", session_id)
                emit_job_event(
                    "build_edhrec",
                    "failed",
                    job_id=job_id,
                    dataset=dataset,
                    session_id=session_id,
                    user_id=user_id,
                    error=str(exc),
                )
                return

            if status == "ok":
                emit_job_event(
                    "build_edhrec",
                    "completed",
                    job_id=job_id,
                    dataset=dataset,
                    session_id=session_id,
                    user_id=user_id,
                    message=message,
                    status="ok",
                )
            else:
                emit_job_event(
                    "build_edhrec",
                    "failed",
                    job_id=job_id,
                    dataset=dataset,
                    session_id=session_id,
                    user_id=user_id,
                    error=message,
                )

    thread = threading.Thread(
        target=_runner,
        name=f"build-edhrec-{job_id[:8]}",
        daemon=True,
    )
    thread.start()
    return job_id


def _edhrec_job_dataset(session_id: int) -> str:
    return f"build_session_{session_id}"


def _get_session(session_id: int) -> BuildSession | None:
    if not current_user.is_authenticated:
        return None
    return (
        BuildSession.query.filter_by(id=session_id, owner_user_id=current_user.id)
        .first()
    )


def _redirect_session(session_id: int, extra_params: dict[str, str] | None = None):
    rec_source_raw = request.form.get("rec_source") or request.args.get("rec_source")
    rec_source = (rec_source_raw or "").strip().lower()
    if rec_source not in {"edhrec", "collection"}:
        rec_source = ""
    sort_raw = request.form.get("sort") or request.args.get("sort")
    sort_mode = _normalize_sort_mode(sort_raw) if sort_raw else ""
    build_view_raw = request.form.get("build_view") or request.args.get("build_view")
    build_view = _normalize_build_view(build_view_raw)
    params: dict[str, str] = {}
    if rec_source:
        params["rec_source"] = rec_source
    if sort_mode:
        params["sort"] = sort_mode
    if build_view:
        params["build_view"] = build_view
    if extra_params:
        for key, value in extra_params.items():
            if value:
                params[key] = value
    return redirect(url_for("views.build_session", session_id=session_id, **params))


def _collection_oracle_ids(user_id: int | None) -> set[str]:
    if not user_id:
        return set()
    rows = (
        db.session.query(Card.oracle_id)
        .join(Folder, Card.folder_id == Folder.id)
        .join(FolderRole, FolderRole.folder_id == Folder.id)
        .filter(
            FolderRole.role == FolderRole.ROLE_COLLECTION,
            Folder.owner_user_id == user_id,
            Card.oracle_id.isnot(None),
        )
        .distinct()
        .all()
    )
    return {row[0] for row in rows if row and row[0]}


def _build_oracle_ids(entries: Iterable[BuildSessionCard]) -> set[str]:
    oracle_ids: set[str] = set()
    for entry in entries or []:
        oracle_id = (entry.card_oracle_id or "").strip()
        if not oracle_id:
            continue
        qty = int(entry.quantity or 0)
        if qty <= 0:
            continue
        oracle_ids.add(oracle_id)
    return oracle_ids


def _recommendation_oracle_ids(sections: list[dict] | None) -> set[str]:
    oracle_ids: set[str] = set()
    for section in sections or []:
        for card in section.get("cards") or []:
            oracle_id = (card.get("oracle_id") or "").strip()
            if oracle_id:
                oracle_ids.add(oracle_id)
    return oracle_ids


def _mark_collection_cards(sections: list[dict] | None, owned_oracles: set[str]) -> None:
    if not sections or not owned_oracles:
        return
    for section in sections:
        for card in section.get("cards") or []:
            oracle_id = (card.get("oracle_id") or "").strip()
            card["in_collection"] = bool(oracle_id and oracle_id in owned_oracles)


def _is_basic_land(type_line: str | None) -> bool:
    lowered = (type_line or "").lower()
    return "land" in lowered and "basic" in lowered


def _mark_build_cards(sections: list[dict] | None, build_oracles: set[str]) -> None:
    if not sections:
        return
    for section in sections:
        for card in section.get("cards") or []:
            oracle_id = (card.get("oracle_id") or "").strip()
            if not oracle_id:
                continue
            type_line = (card.get("type_line") or "").strip()
            if not type_line:
                type_line = _oracle_meta(oracle_id).get("type_line") or ""
                card["type_line"] = type_line
            card["is_basic_land"] = bool(card.get("is_basic_land")) or _is_basic_land(type_line)
            card["in_build"] = oracle_id in build_oracles


def _collection_recommendation_sections(
    commander_oracle_id: str,
    tags: list[str] | None,
    owned_oracles: set[str],
    role_needs: set[str] | None,
    *,
    exclude_oracles: set[str] | None = None,
    sort_mode: str = "synergy",
) -> list[dict]:
    if not commander_oracle_id or not owned_oracles:
        return []
    try:
        sc.ensure_cache_loaded()
    except Exception:
        return []

    commander_identity = _color_identity_set(commander_oracle_id)
    excluded = {oid.casefold() for oid in (exclude_oracles or set()) if oid}
    selected_tags = [tag for tag in _normalized_tags(tags) if tag]
    role_needs = {role for role in (role_needs or set()) if role}

    edhrec_rows = get_commander_synergy(
        commander_oracle_id,
        selected_tags,
        prefer_tag_specific=True,
        limit=None,
    )
    edhrec_map: dict[str, dict] = {}
    for rec in edhrec_rows:
        oracle_id = (rec.get("oracle_id") or "").strip()
        if not oracle_id:
            continue
        edhrec_map[oracle_id] = rec

    tag_map: dict[str, set[str]] = defaultdict(set)
    if selected_tags:
        rows = (
            db.session.query(OracleDeckTag.oracle_id, OracleDeckTag.tag)
            .filter(OracleDeckTag.tag.in_(selected_tags))
            .all()
        )
        for oracle_id, tag in rows:
            if not oracle_id or not tag:
                continue
            tag_map[oracle_id].add(str(tag))

    role_map: dict[str, set[str]] = defaultdict(set)
    if role_needs:
        rows = (
            db.session.query(OracleCoreRoleTag.oracle_id, OracleCoreRoleTag.role)
            .filter(OracleCoreRoleTag.role.in_(role_needs))
            .all()
        )
        for oracle_id, role in rows:
            if not oracle_id or not role:
                continue
            role_map[oracle_id].add(str(role))

    detail_cache: dict[str, dict] = {}
    grouped: dict[str, list[dict]] = defaultdict(list)

    # Build collection-only recommendations without duplicating EDHRec list entries.
    for oracle_id in owned_oracles:
        if not oracle_id:
            continue
        if oracle_id.casefold() in excluded:
            continue
        if oracle_id not in edhrec_map and oracle_id not in tag_map and oracle_id not in role_map:
            continue
        card_identity = _color_identity_set(oracle_id)
        if card_identity and not card_identity.issubset(commander_identity):
            continue
        payload = _oracle_payload(oracle_id)
        if not payload:
            continue
        detail = _oracle_detail(oracle_id, detail_cache)
        type_line = detail.get("type_line") or ""
        type_group = _collection_type_group(type_line)
        if not type_group:
            continue
        is_basic_land = _is_basic_land(type_line)

        reasons: list[str] = []
        synergy_score = None
        synergy_percent = None
        synergy_rank = None
        inclusion_percent = None
        edhrec_rec = edhrec_map.get(oracle_id)
        if edhrec_rec:
            synergy_score = edhrec_rec.get("synergy_score")
            synergy_percent = edhrec_rec.get("synergy_percent")
            synergy_rank = edhrec_rec.get("synergy_rank")
            inclusion_percent = edhrec_rec.get("inclusion_percent")
            reasons.append("edhrec synergy")

        tag_matches = sorted(tag_map.get(oracle_id, set()))
        if tag_matches:
            reasons.append(f"tag: {', '.join(tag_matches[:2])}")

        role_matches = sorted(role_map.get(oracle_id, set()))
        if role_matches:
            reasons.append(f"fills {role_matches[0].lower()}")

        score = float(synergy_score or 0.0)
        score += 0.12 * len(tag_matches)
        score += 0.1 * len(role_matches)

        if synergy_percent is None and synergy_score is not None:
            try:
                synergy_percent = round(float(synergy_score) * 100.0, 1)
            except (TypeError, ValueError):
                synergy_percent = None

        grouped[type_group].append(
            {
                "oracle_id": oracle_id,
                "name": payload.get("name") or oracle_id,
                "image": payload.get("image"),
                "type_group": type_group,
                "type_line": type_line,
                "is_basic_land": is_basic_land,
                "synergy_score": synergy_score,
                "synergy_percent": synergy_percent,
                "inclusion_percent": inclusion_percent,
                "synergy_rank": synergy_rank,
                "role_score": len(role_matches),
                "need_score": len(role_matches),
                "score": score,
                "reasons": reasons,
            }
        )

    all_cards = [card for cards in grouped.values() for card in cards]
    max_score = max((float(card.get("score") or 0.0) for card in all_cards), default=0.0)
    if max_score > 0:
        for card in all_cards:
            card["collection_score_percent"] = round((float(card.get("score") or 0.0) / max_score) * 100.0, 1)
    else:
        for card in all_cards:
            card["collection_score_percent"] = None

    sections: list[dict] = []
    for label in _COLLECTION_GROUP_ORDER:
        cards = grouped.get(label, [])
        if not cards:
            continue
        sections.append(
            {
                "key": _slugify(label),
                "label": label,
                "cards": _sort_collection_cards(cards, sort_mode),
                "default_open": label in {"Creatures", "Lands"},
                "count": len(cards),
            }
        )

    leftovers = grouped.get("Other", [])
    if leftovers:
        sections.append(
            {
                "key": "other",
                "label": "Other",
                "cards": _sort_collection_cards(leftovers, sort_mode),
                "default_open": False,
                "count": len(leftovers),
            }
        )

    return sections


def _ensure_build_session_tables() -> None:
    try:
        inspector = inspect(db.engine)
        tables = set(inspector.get_table_names())
        missing = []
        if "build_sessions" not in tables:
            missing.append(BuildSession.__table__)
        if "build_session_cards" not in tables:
            missing.append(BuildSessionCard.__table__)
        if missing:
            db.metadata.create_all(db.engine, tables=missing)
        if "build_sessions" in tables:
            columns = {col["name"] for col in inspector.get_columns("build_sessions")}
            if "build_name" not in columns:
                db.session.execute(text("ALTER TABLE build_sessions ADD COLUMN build_name VARCHAR(200)"))
                db.session.commit()
    except Exception as exc:
        _LOG.error("Failed to ensure build session tables: %s", exc)


def ensure_build_session_tables() -> None:
    _ensure_build_session_tables()


def _collect_tags_from_request() -> list[str]:
    tags = []
    for value in request.form.getlist("deck_tags"):
        if value:
            tags.append(value)
    raw_list = (request.form.get("deck_tags_csv") or "").strip()
    if raw_list:
        for entry in raw_list.split(","):
            if entry.strip():
                tags.append(entry.strip())
    single = (request.form.get("deck_tag") or "").strip()
    if single:
        tags.append(single)
    return tags


def _normalized_tags(tags: Iterable[str] | None) -> list[str]:
    if isinstance(tags, str):
        tags = [tags]
    normalized: list[str] = []
    seen: set[str] = set()
    for tag in tags or []:
        label = (tag or "").strip()
        if not label:
            continue
        key = label.casefold()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(label)
    return normalized


def _oracle_name(oracle_id: str) -> str | None:
    if not oracle_id:
        return None
    try:
        sc.ensure_cache_loaded()
        prints = sc.prints_for_oracle(oracle_id) or []
    except Exception:
        return None
    if not prints:
        return None
    return (prints[0].get("name") or "").strip() or None


def _oracle_payload(oracle_id: str | None, *, fallback: str | None = None) -> dict:
    if not oracle_id:
        return {"oracle_id": None, "name": fallback or "", "image": None, "colors": []}
    try:
        sc.ensure_cache_loaded()
        prints = sc.prints_for_oracle(oracle_id) or []
    except Exception:
        prints = []
    name = fallback or oracle_id
    image = None
    colors: list[str] = []
    if prints:
        pr = prints[0]
        name = (pr.get("name") or "").strip() or name
        colors = pr.get("color_identity") or pr.get("colors") or []
        image_uris = pr.get("image_uris") or {}
        if not image_uris:
            faces = pr.get("card_faces") or []
            if faces:
                image_uris = (faces[0] or {}).get("image_uris") or {}
        image = image_uris.get("normal") or image_uris.get("large") or image_uris.get("small")
    return {"oracle_id": oracle_id, "name": name, "image": image, "colors": colors}


def _price_to_float(value: object | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _cheapest_price_for_oracle(oracle_id: str, cache: dict[str, str | None]) -> str | None:
    cached = cache.get(oracle_id)
    if cached is not None:
        return cached
    min_usd = None
    min_eur = None
    min_tix = None
    try:
        sc.ensure_cache_loaded()
        prints = sc.prints_for_oracle(oracle_id) or []
    except Exception:
        prints = []
    for pr in prints:
        prices = pr.get("prices") or {}
        for key in ("usd", "usd_foil", "usd_etched"):
            val = _price_to_float(prices.get(key))
            if val and val > 0:
                min_usd = val if min_usd is None or val < min_usd else min_usd
        for key in ("eur", "eur_foil"):
            val = _price_to_float(prices.get(key))
            if val and val > 0:
                min_eur = val if min_eur is None or val < min_eur else min_eur
        val = _price_to_float(prices.get("tix"))
        if val and val > 0:
            min_tix = val if min_tix is None or val < min_tix else min_tix
    if min_usd is not None:
        price_text = f"${min_usd:.2f}"
    elif min_eur is not None:
        price_text = f"EUR {min_eur:.2f}"
    elif min_tix is not None:
        price_text = f"{min_tix:.2f} TIX"
    else:
        price_text = None
    cache[oracle_id] = price_text
    return price_text


def _session_cards(entries: Iterable[BuildSessionCard]) -> list[dict]:
    cards: list[dict] = []
    detail_cache: dict[str, dict] = {}
    price_cache: dict[str, str | None] = {}
    for entry in entries:
        oracle_id = (entry.card_oracle_id or "").strip()
        if not oracle_id:
            continue
        payload = _oracle_payload(oracle_id)
        detail = _oracle_detail(oracle_id, detail_cache)
        price_text = _cheapest_price_for_oracle(oracle_id, price_cache)
        raw_costs = [cost for cost in (detail.get("mana_costs") or []) if cost]
        mana_cost_line = " // ".join(raw_costs) if raw_costs else ""
        mana_cost_html = render_mana_html(mana_cost_line, use_local=True) if mana_cost_line else None
        cmc_raw = detail.get("cmc")
        cmc_val = None
        if cmc_raw is not None:
            try:
                cmc_val = float(cmc_raw)
            except (TypeError, ValueError):
                cmc_val = None
        if cmc_val is None:
            cmc_bucket = ""
        else:
            bucket_val = int(round(cmc_val))
            if bucket_val < 0:
                bucket_val = 0
            cmc_bucket = str(bucket_val) if bucket_val <= 6 else "7+"
        cards.append(
            {
                "oracle_id": oracle_id,
                "name": payload["name"],
                "image": payload["image"],
                "quantity": int(entry.quantity or 0),
                "type_line": detail.get("type_line") or "",
                "cmc_bucket": cmc_bucket,
                "cmc_value": cmc_val,
                "price_text": price_text,
                "mana_cost_html": mana_cost_html,
            }
        )
    cards.sort(key=lambda item: (item["name"].casefold(), item["oracle_id"]))
    return cards


def _group_session_cards_by_type(cards: list[dict]) -> list[dict]:
    groups: dict[str, list[dict]] = {label: [] for label in _COLLECTION_GROUP_ORDER}
    extras: list[dict] = []
    for card in cards:
        label = _collection_type_group(card.get("type_line") or "")
        if label in groups:
            groups[label].append(card)
        else:
            extras.append(card)

    def _sort_key(card: dict) -> tuple:
        cmc = card.get("cmc_value")
        name = (card.get("name") or "").casefold()
        oracle_id = card.get("oracle_id") or ""
        if cmc is None:
            return (1, 0, name, oracle_id)
        return (0, cmc, name, oracle_id)

    grouped: list[dict] = []
    for label in _COLLECTION_GROUP_ORDER:
        entries = groups.get(label) or []
        if entries:
            entries.sort(key=_sort_key)
            grouped.append({"label": label, "cards": entries})
    if extras:
        extras.sort(key=_sort_key)
        grouped.append({"label": "Other", "cards": extras})
    return grouped


def _deck_metrics(entries: Iterable[BuildSessionCard]) -> dict:
    items = list(entries or [])
    oracle_ids = {entry.card_oracle_id for entry in items if entry.card_oracle_id}
    role_map = _oracle_role_map(oracle_ids)

    total_cards = 0
    land_count = 0
    mana_pip_non_land = {c: 0 for c in ["W", "U", "B", "R", "G"]}
    production_counts = {c: 0 for c in ["W", "U", "B", "R", "G", "C"]}
    curve_buckets = {"0": 0, "1": 0, "2": 0, "3": 0, "4": 0, "5": 0, "6": 0, "7+": 0}
    missing_cmc = 0
    role_counts = {key: 0 for key in _ROLE_BUCKETS}
    detail_cache: dict[str, dict] = {}

    for entry in items:
        oracle_id = (entry.card_oracle_id or "").strip()
        if not oracle_id:
            continue
        qty = int(entry.quantity or 0)
        if qty <= 0:
            continue
        total_cards += qty
        meta = _oracle_detail(oracle_id, detail_cache)
        type_line = (meta.get("type_line") or "").lower()
        cmc_raw = meta.get("cmc")
        if cmc_raw is None:
            cmc = None
        else:
            try:
                cmc = float(cmc_raw)
            except (TypeError, ValueError):
                cmc = None
        if "land" in type_line:
            land_count += qty
        else:
            for mana_cost in meta.get("mana_costs") or []:
                _add_colored_pips(mana_cost, qty, mana_pip_non_land)
        if meta.get("is_permanent"):
            for ch in _colors_from_oracle_text_add(meta.get("oracle_text") or ""):
                production_counts[ch] += qty
        if "land" not in type_line:
            if cmc is None:
                missing_cmc += qty
            else:
                bucket_val = int(round(cmc))
                if bucket_val < 0:
                    bucket_val = 0
                bucket = str(bucket_val) if bucket_val <= 6 else "7+"
                curve_buckets[bucket] += qty
        roles = role_map.get(oracle_id, set())
        for key, bucket_roles in _ROLE_BUCKETS.items():
            if roles & bucket_roles:
                role_counts[key] += qty

    non_land_count = max(total_cards - land_count, 0)
    deck_health, role_needs = _deck_health(role_counts)
    phase = "exploration" if total_cards < 20 else "refinement"

    total_curve = sum(curve_buckets.values()) or 1
    curve_rows = []
    for label in ["0", "1", "2", "3", "4", "5", "6", "7+"]:
        count = int(curve_buckets.get(label) or 0)
        pct = int(round(100.0 * count / total_curve)) if total_curve else 0
        curve_rows.append({"label": label, "count": count, "pct": pct})

    return {
        "total_cards": total_cards,
        "land_count": land_count,
        "non_land_count": non_land_count,
        "mana_pip_dist": _mana_pip_dist(mana_pip_non_land),
        "land_mana_sources": _mana_source_dist(production_counts),
        "curve_buckets": curve_buckets,
        "curve_rows": curve_rows,
        "missing_cmc": missing_cmc,
        "role_counts": role_counts,
        "deck_health": deck_health,
        "role_needs": role_needs,
        "phase": phase,
    }


def _oracle_role_map(oracle_ids: set[str]) -> dict[str, set[str]]:
    if not oracle_ids:
        return {}
    rows = (
        db.session.query(OracleCoreRoleTag.oracle_id, OracleCoreRoleTag.role)
        .filter(OracleCoreRoleTag.oracle_id.in_(oracle_ids))
        .all()
    )
    role_map: dict[str, set[str]] = {}
    for oracle_id, role in rows:
        if not oracle_id or not role:
            continue
        role_map.setdefault(oracle_id, set()).add(str(role))
    return role_map


def _oracle_meta(oracle_id: str) -> dict:
    try:
        sc.ensure_cache_loaded()
        prints = sc.prints_for_oracle(oracle_id) or []
    except Exception:
        return {}
    if not prints:
        return {}
    pr = prints[0]
    return {
        "type_line": pr.get("type_line") or "",
        "cmc": pr.get("cmc") or 0.0,
    }


def _oracle_detail(oracle_id: str, cache: dict[str, dict]) -> dict:
    cached = cache.get(oracle_id)
    if cached is not None:
        return cached
    payload = {"type_line": "", "cmc": None, "mana_costs": [], "oracle_text": "", "is_permanent": False}
    try:
        sc.ensure_cache_loaded()
        prints = sc.prints_for_oracle(oracle_id) or []
    except Exception:
        cache[oracle_id] = payload
        return payload
    if not prints:
        cache[oracle_id] = payload
        return payload
    pr = prints[0]
    type_line = pr.get("type_line") or ""
    payload["type_line"] = type_line
    payload["cmc"] = pr.get("cmc")
    payload["mana_costs"] = _mana_costs_from_faces(pr)
    payload["oracle_text"] = _oracle_text_from_faces(pr)
    payload["is_permanent"] = _is_permanent_type(type_line)
    cache[oracle_id] = payload
    return payload


def _mana_costs_from_faces(print_obj: dict) -> list[str]:
    faces = print_obj.get("card_faces") or []
    face_costs: list[str] = []
    for face in faces:
        if not isinstance(face, dict):
            continue
        face_cost = face.get("mana_cost")
        if face_cost:
            face_costs.append(str(face_cost))
    if face_costs:
        return [cost for cost in face_costs if cost]
    mana_cost = print_obj.get("mana_cost")
    if mana_cost:
        return [str(mana_cost)]
    return []


def _oracle_text_from_faces(print_obj: dict) -> str:
    texts: list[str] = []
    oracle_text = print_obj.get("oracle_text")
    if oracle_text:
        texts.append(str(oracle_text))
    faces = print_obj.get("card_faces") or []
    for face in faces:
        if not isinstance(face, dict):
            continue
        face_text = face.get("oracle_text")
        if face_text:
            texts.append(str(face_text))
    return " // ".join([t for t in texts if t])


def _is_permanent_type(type_line: str) -> bool:
    lowered = (type_line or "").lower()
    return any(token in lowered for token in ("land", "creature", "artifact", "enchantment", "planeswalker", "battle"))


def _add_colored_pips(mana_cost: str, qty: int, counts: dict[str, int]) -> None:
    for symbol in RE_COST_SYMBOL.findall(mana_cost or ""):
        token = symbol.upper()
        for ch in ("W", "U", "B", "R", "G"):
            if ch in token:
                counts[ch] += qty


def _colors_from_oracle_text_add(text: str) -> set[str]:
    out: set[str] = set()
    if not text:
        return out
    upper = text.upper()
    if "ADD" not in upper:
        return out
    for sym in RE_COST_SYMBOL.findall(text):
        token = sym.upper()
        for ch in ("W", "U", "B", "R", "G", "C"):
            if ch in token:
                out.add(ch)
    if "ANY COLOR" in upper:
        out.update({"W", "U", "B", "R", "G"})
    return out


def _mana_pip_dist(counts: dict[str, int]) -> list[tuple[str, str | None, int]]:
    dist: list[tuple[str, str | None, int]] = []
    for c in ["W", "U", "B", "R", "G"]:
        value = int(counts.get(c) or 0)
        if value <= 0:
            continue
        icons = colors_to_icons([c], use_local=True)
        dist.append((c, icons[0] if icons else None, value))
    return dist


def _mana_source_dist(counts: dict[str, int]) -> list[tuple[str, str | None, int]]:
    dist: list[tuple[str, str | None, int]] = []
    for c in ["W", "U", "B", "R", "G", "C"]:
        value = int(counts.get(c) or 0)
        if value <= 0:
            continue
        icon = None
        if c in {"W", "U", "B", "R", "G"}:
            icons = colors_to_icons([c], use_local=True)
            icon = icons[0] if icons else None
        dist.append((c, icon, value))
    return dist


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


def _type_breakdown_for_entries(entries: Iterable[BuildSessionCard]) -> list[tuple[str, int]]:
    type_counts = {t: 0 for t in _BASE_TYPES}
    for entry in entries or []:
        oracle_id = (entry.card_oracle_id or "").strip()
        if not oracle_id:
            continue
        qty = int(entry.quantity or 0)
        if qty <= 0:
            continue
        type_line = (_oracle_meta(oracle_id).get("type_line") or "").lower()
        if not type_line:
            continue
        for t in _BASE_TYPES:
            if t.lower() in type_line:
                type_counts[t] += qty
    return [(t, int(type_counts.get(t, 0))) for t in _BASE_TYPES]


def _primary_type_for_distribution(type_line: str) -> str | None:
    lowered = (type_line or "").lower()
    if not lowered:
        return None
    for t in _DISTRIBUTION_PRIORITY:
        if t.lower() in lowered:
            return t
    return None


def _distribution_breakdown_for_entries(entries: Iterable[BuildSessionCard]) -> list[tuple[str, int]]:
    type_counts = {t: 0 for t in _DISTRIBUTION_TYPES}
    detail_cache: dict[str, dict] = {}
    for entry in entries or []:
        oracle_id = (entry.card_oracle_id or "").strip()
        if not oracle_id:
            continue
        qty = int(entry.quantity or 0)
        if qty <= 0:
            continue
        type_line = (_oracle_detail(oracle_id, detail_cache).get("type_line") or "").lower()
        if not type_line:
            continue
        primary = _primary_type_for_distribution(type_line)
        if primary and primary in type_counts:
            type_counts[primary] += qty
    return [(t, int(type_counts.get(t, 0))) for t in _DISTRIBUTION_TYPES]


def _edhrec_type_breakdown(
    commander_oracle_id: str,
    tags: list[str] | None,
) -> list[tuple[str, int]]:
    if not commander_oracle_id:
        return []
    tag_label = None
    if tags:
        tag_label = resolve_deck_tag_from_slug(tags[0])
        tag_label = (tag_label or "").strip() or None

    dist_rows = get_commander_type_distribution(commander_oracle_id, tag=tag_label)
    if tag_label and not dist_rows:
        dist_rows = get_commander_type_distribution(commander_oracle_id, tag=None)
    if dist_rows:
        counts = {label: int(count or 0) for label, count in dist_rows if label}
        trimmed = {label: int(counts.get(label, 0)) for label in _DISTRIBUTION_TYPES}
        return [(label, int(trimmed.get(label, 0))) for label in _DISTRIBUTION_TYPES]
    return []


def _curve_bucket(cmc: float) -> str:
    if cmc <= 2:
        return "0-2"
    if cmc <= 4:
        return "3-4"
    if cmc <= 6:
        return "5-6"
    return "7+"


def _deck_health(role_counts: dict) -> tuple[list[dict], set[str]]:
    health: list[dict] = []
    role_needs: set[str] = set()
    for key, config in _ROLE_TARGETS.items():
        count = int(role_counts.get(key, 0))
        target = config["target"]
        status = _status_label(count, target)
        if status == "low":
            role_needs |= config["roles"]
        health.append(
            {
                "key": key,
                "label": config["label"],
                "count": count,
                "target": target,
                "status": status,
            }
        )
    return health, role_needs


def _status_label(count: int, target: int) -> str:
    if target <= 0:
        return "ok"
    if count < max(int(target * 0.7), 1):
        return "low"
    if count > int(target * 1.4):
        return "high"
    return "ok"


def _primary_type_for_breakdown(type_line: str) -> str | None:
    lowered = (type_line or "").lower()
    if not lowered:
        return None
    for t in _TYPE_PRIORITY:
        if t.lower() in lowered:
            return t
    return None


def _normalize_distribution_total(counts: dict[str, int], *, target_total: int) -> dict[str, int]:
    total = sum(int(value or 0) for value in counts.values())
    if total <= 0 or total == target_total:
        return counts
    adjusted = {key: int(value or 0) for key, value in counts.items()}
    if total == target_total + 1:
        if adjusted.get("Creature", 0) > 0:
            adjusted["Creature"] -= 1
            return adjusted
    scaled = {}
    for key, value in adjusted.items():
        scaled[key] = int(round((value / total) * target_total)) if total else 0
    current = sum(scaled.values())
    if current == target_total:
        return scaled
    ordered = sorted(scaled.items(), key=lambda item: item[1], reverse=True)
    idx = 0
    while current != target_total and ordered:
        key = ordered[idx % len(ordered)][0]
        if current < target_total:
            scaled[key] += 1
            current += 1
        else:
            if scaled[key] > 0:
                scaled[key] -= 1
                current -= 1
        idx += 1
    return scaled


def _build_session_bracket_context(session: BuildSession, entries: Iterable[BuildSessionCard]) -> dict:
    detail_cache: dict[str, dict] = {}
    bracket_cards: list[dict[str, object]] = []
    for entry in entries or []:
        oracle_id = (entry.card_oracle_id or "").strip()
        if not oracle_id:
            continue
        qty = int(entry.quantity or 0)
        if qty <= 0:
            continue
        detail = _oracle_detail(oracle_id, detail_cache)
        costs = [cost for cost in (detail.get("mana_costs") or []) if cost]
        bracket_cards.append(
            {
                "name": _oracle_name(oracle_id) or oracle_id,
                "type_line": detail.get("type_line") or "",
                "oracle_text": detail.get("oracle_text") or "",
                "mana_value": detail.get("cmc"),
                "quantity": qty,
                "mana_cost": " // ".join(costs) if costs else None,
                "produced_mana": None,
            }
        )

    commander_stub = {
        "oracle_id": session.commander_oracle_id,
        "name": session.commander_name or _oracle_name(session.commander_oracle_id or ""),
    }
    epoch = sc.cache_epoch() + BRACKET_RULESET_EPOCH + spellbook_dataset_epoch()
    signature = compute_bracket_signature(bracket_cards, commander_stub, epoch=epoch)
    cache_key = ("build_session_bracket", session.id, signature, epoch)
    commander_ctx = request_cached(
        cache_key,
        lambda: evaluate_commander_bracket(bracket_cards, commander_stub),
    )
    spellbook_details = commander_ctx.get("spellbook_details") or []
    if len(spellbook_details) > 8:
        spellbook_details = spellbook_details[:8]
    return {
        "level": commander_ctx.get("level"),
        "label": commander_ctx.get("label"),
        "score": commander_ctx.get("score"),
        "summary_points": commander_ctx.get("summary_points") or [],
        "spellbook_combos": spellbook_details,
    }


def _build_session_drawer_summary(session: BuildSession) -> dict:
    tags = _normalized_tags(session.tags_json)
    tag_label = None
    if tags:
        category = get_deck_tag_category(tags[0])
        tag_label = f"{category}: {tags[0]}" if category else tags[0]

    deck_name = session.build_name or session.commander_name or "Build"
    commander_payload = _commander_drawer_payload(
        session.commander_oracle_id,
        session.commander_name,
    )

    entries = session.cards or []
    metrics = _deck_metrics(entries)
    type_breakdown = [
        (label, count)
        for label, count in _type_breakdown_for_entries(entries)
        if count > 0
    ]
    mana_pip_dist = [
        {"color": color, "icon": icon, "count": count}
        for color, icon, count in metrics.get("mana_pip_dist") or []
    ]
    land_mana_sources = [
        {"color": color, "icon": icon, "label": color, "count": count}
        for color, icon, count in metrics.get("land_mana_sources") or []
    ]
    curve_rows = _curve_rows_for_entries(entries)
    deck_colors = sorted(_color_identity_set(session.commander_oracle_id or ""))
    commander_ctx = _build_session_bracket_context(session, entries)

    return {
        "deck": {
            "id": session.id,
            "name": deck_name,
            "tag": tags[0] if tags else None,
            "tag_label": tag_label,
        },
        "commander": commander_payload,
        "bracket": commander_ctx,
        "type_breakdown": type_breakdown,
        "mana_pip_dist": mana_pip_dist,
        "land_mana_sources": land_mana_sources,
        "curve_rows": curve_rows,
        "missing_cmc": metrics.get("missing_cmc") or 0,
        "total_cards": metrics.get("total_cards") or 0,
        "deck_colors": deck_colors,
    }


def _commander_drawer_payload(oracle_id: str | None, fallback_name: str | None) -> dict:
    placeholder_thumb = static_url("img/card-placeholder.svg")
    if not oracle_id:
        return {"name": fallback_name or "Commander", "image": placeholder_thumb, "hover": placeholder_thumb}
    try:
        sc.ensure_cache_loaded()
        prints = sc.prints_for_oracle(oracle_id) or []
    except Exception:
        prints = []
    if not prints:
        return {"name": fallback_name or "Commander", "image": placeholder_thumb, "hover": placeholder_thumb}
    pr = prints[0]
    name = fallback_name or (pr.get("name") or "Commander")
    image_uris = pr.get("image_uris") or {}
    if not image_uris:
        faces = pr.get("card_faces") or []
        if faces:
            image_uris = (faces[0] or {}).get("image_uris") or {}
    image = image_uris.get("small") or image_uris.get("normal") or image_uris.get("large")
    hover = image_uris.get("large") or image_uris.get("normal") or image_uris.get("small")
    return {
        "name": name,
        "image": image or placeholder_thumb,
        "hover": hover or image or placeholder_thumb,
    }


def _curve_rows_for_entries(entries: Iterable[BuildSessionCard]) -> list[dict]:
    bins = {"0": 0, "1": 0, "2": 0, "3": 0, "4": 0, "5": 0, "6": 0, "7+": 0}
    detail_cache: dict[str, dict] = {}
    for entry in entries or []:
        oracle_id = (entry.card_oracle_id or "").strip()
        if not oracle_id:
            continue
        qty = int(entry.quantity or 0)
        if qty <= 0:
            continue
        meta = _oracle_detail(oracle_id, detail_cache)
        type_line = (meta.get("type_line") or "").lower()
        if "land" in type_line:
            continue
        cmc_raw = meta.get("cmc")
        if cmc_raw is None:
            cmc = None
        else:
            try:
                cmc = float(cmc_raw)
            except (TypeError, ValueError):
                cmc = None
        if cmc is None:
            continue
        bucket_val = int(round(cmc))
        if bucket_val < 0:
            bucket_val = 0
        bucket = str(bucket_val) if bucket_val <= 6 else "7+"
        bins[bucket] += qty

    max_curve = max(bins.values()) if bins else 0
    rows = []
    for bucket in ["0", "1", "2", "3", "4", "5", "6", "7+"]:
        count = int(bins.get(bucket) or 0)
        if count <= 0:
            continue
        pct = 100.0 * count / max_curve if max_curve else 0.0
        rows.append({"label": bucket, "count": count, "pct": pct})
    return rows


def _normalize_sort_mode(raw: str | None) -> str:
    value = (raw or "").strip().lower()
    if value in {"role", "need"}:
        return value
    return "synergy"


def _normalize_build_view(raw: str | None) -> str:
    value = (raw or "").strip().lower()
    if value in {"list", "gallery", "type"}:
        return value
    return ""


def _edhrec_estimate_seconds(tags: list[str] | None) -> int:
    try:
        interval = float(os.getenv("EDHREC_INGEST_INTERVAL", "1.0"))
    except (TypeError, ValueError):
        interval = 1.0
    interval = max(1.0, interval)
    request_count = 1 + (1 if tags else 0)
    estimate = (request_count * interval) + 3
    return int(round(estimate))


def _slugify(label: str) -> str:
    return (label or "").strip().lower().replace(" ", "-")


def _collection_type_group(type_line: str) -> str:
    lowered = (type_line or "").lower()
    for label, tokens in _COLLECTION_TYPE_GROUPS:
        if any(token in lowered for token in tokens):
            return label
    return "Other" if lowered else ""


def _sort_collection_cards(cards: list[dict], sort_mode: str) -> list[dict]:
    mode = (sort_mode or "synergy").strip().lower()
    if mode == "role":
        key = lambda item: (-item.get("role_score", 0), -item.get("score", 0.0), item.get("name", ""))
    elif mode == "need":
        key = lambda item: (-item.get("need_score", 0), -item.get("score", 0.0), item.get("name", ""))
    else:
        key = lambda item: (
            -float(item.get("synergy_score") or 0.0),
            -float(item.get("score") or 0.0),
            item.get("synergy_rank") or 999999,
            item.get("name", ""),
        )
    return sorted(cards, key=key)


_ROLE_BUCKETS = {
    "ramp": {"Ramp", "Fixing", "Treasure"},
    "draw": {"Draw", "Selection", "Advantage"},
    "interaction": {"Removal", "Wipe", "Counter", "Bounce", "Tax", "Stax", "Hate"},
    "wincon": {"Finisher", "Payoff", "Go Wide", "Go Tall", "Voltron", "Engine"},
}

_ROLE_TARGETS = {
    "ramp": {"label": "ramp", "target": 10, "roles": _ROLE_BUCKETS["ramp"]},
    "draw": {"label": "card draw", "target": 8, "roles": _ROLE_BUCKETS["draw"]},
    "interaction": {"label": "interaction", "target": 8, "roles": _ROLE_BUCKETS["interaction"]},
    "wincon": {"label": "win conditions", "target": 2, "roles": _ROLE_BUCKETS["wincon"]},
}

_BASE_TYPES = ["Artifact", "Battle", "Creature", "Enchantment", "Instant", "Land", "Planeswalker", "Sorcery"]
_TYPE_PRIORITY = ["Land", "Creature", "Instant", "Sorcery", "Artifact", "Enchantment", "Planeswalker", "Battle"]
_DISTRIBUTION_TYPES = ["Land", "Enchantment", "Artifact", "Sorcery", "Instant", "Creature", "Planeswalker"]
_DISTRIBUTION_PRIORITY = ["Land", "Creature", "Instant", "Sorcery", "Artifact", "Enchantment", "Planeswalker"]

_COLLECTION_GROUP_ORDER = [
    "Creatures",
    "Instants",
    "Sorceries",
    "Enchantments",
    "Artifacts",
    "Planeswalkers",
    "Lands",
]

_COLLECTION_TYPE_GROUPS = [
    ("Creatures", ("creature",)),
    ("Instants", ("instant",)),
    ("Sorceries", ("sorcery",)),
    ("Enchantments", ("enchantment",)),
    ("Artifacts", ("artifact",)),
    ("Planeswalkers", ("planeswalker",)),
    ("Lands", ("land",)),
]

RE_COST_SYMBOL = re.compile(r"\{([^}]+)\}")


__all__ = [
    "add_card",
    "add_cards_bulk",
    "build_session_page",
    "remove_card",
    "start_build_session",
    "ensure_build_session_tables",
    "update_tags",
    "update_name",
    "update_quantity",
    "delete_session",
    "api_build_session_insight",
]
