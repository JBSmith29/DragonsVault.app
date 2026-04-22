"""Mutation and session-management helpers for build sessions."""

from __future__ import annotations

import logging
import threading
import uuid
from collections import defaultdict
from typing import Iterable

from flask import abort, flash, jsonify, redirect, request, url_for
from flask_login import current_user
from sqlalchemy import inspect, text

from extensions import db
from models import BuildSession, BuildSessionCard
from core.domains.cards.services import scryfall_cache as sc
from core.domains.decks.services.edhrec.edhrec_ingestion_service import ingest_commander_tag_data
from core.domains.decks.services.proxy_decks import parse_decklist
from shared.events.live_updates import emit_job_event, latest_job_events

_LOG = logging.getLogger(__name__)


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


def add_cards_manual(session_id: int):
    session = _get_session(session_id)
    if session is None:
        abort(404)

    raw_list = (request.form.get("card_list") or "").strip()
    single_name = (request.form.get("card_name") or "").strip()
    single_qty_raw = request.form.get("card_quantity")

    entries: list[tuple[str, int]] = []
    if raw_list:
        entries = parse_decklist(raw_list.splitlines())
    elif single_name:
        parsed = parse_decklist([single_name])
        if parsed:
            name, qty = parsed[0]
            override_qty = None
            if single_qty_raw not in (None, ""):
                try:
                    override_qty = int(single_qty_raw)
                except (TypeError, ValueError):
                    override_qty = None
            if override_qty is not None:
                if not (qty != 1 and override_qty == 1):
                    qty = max(override_qty, 1)
            entries = [(name, max(int(qty or 1), 1))]

    if not entries:
        flash("Enter at least one card name to add.", "warning")
        return _redirect_session(session_id)

    try:
        sc.ensure_cache_loaded()
    except Exception as exc:
        _LOG.error("Manual build add failed to load card cache: %s", exc)
        flash("Card cache unavailable. Please try again shortly.", "danger")
        return _redirect_session(session_id)

    unresolved: list[str] = []
    resolved_counts: dict[str, int] = defaultdict(int)
    for raw_name, qty in entries:
        oracle_id = sc.unique_oracle_by_name(raw_name)
        if not oracle_id:
            unresolved.append(raw_name)
            continue
        resolved_counts[oracle_id] += max(int(qty or 1), 1)

    if not resolved_counts:
        if unresolved:
            _flash_unresolved_manual_add(unresolved)
        else:
            flash("No cards resolved from the list.", "warning")
        return _redirect_session(session_id)

    for oracle_id, qty in resolved_counts.items():
        entry = BuildSessionCard.query.filter_by(session_id=session.id, card_oracle_id=oracle_id).first()
        if entry:
            entry.quantity = int(entry.quantity or 0) + qty
        else:
            db.session.add(BuildSessionCard(session_id=session.id, card_oracle_id=oracle_id, quantity=qty))

    try:
        db.session.commit()
        total_qty = sum(resolved_counts.values())
        flash(
            f"Added {total_qty} card{'s' if total_qty != 1 else ''} to the build.",
            "success",
        )
    except Exception:
        db.session.rollback()
        flash("Unable to add cards to the build session.", "danger")

    if unresolved:
        _flash_unresolved_manual_add(unresolved)

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
    return BuildSession.query.filter_by(id=session_id, owner_user_id=current_user.id).first()


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


def _flash_unresolved_manual_add(names: list[str]) -> None:
    if not names:
        return
    unique: list[str] = []
    seen: set[str] = set()
    for name in names:
        key = (name or "").casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(name)
    if not unique:
        return
    preview = ", ".join(unique[:5])
    if len(unique) > 5:
        preview = f"{preview} (+{len(unique) - 5} more)"
    flash(f"Could not resolve: {preview}.", "warning")


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

