"""Admin console action handling and page rendering."""

from __future__ import annotations

import uuid
from typing import List

from flask import current_app, flash, redirect, render_template, request, url_for
from sqlalchemy import func, text

from extensions import db
from models import Card, Folder, FolderRole, User
from core.domains.cards.services.scryfall_cache import (
    cache_exists,
    clear_cache_files,
    ensure_cache_loaded,
    load_default_cache as load_cache,
    reload_default_cache as reload_cache,
)
from core.domains.decks.services.deck_service import recompute_deck_stats
from core.domains.decks.services.deck_tags import is_valid_deck_tag
from core.domains.decks.services.edhrec_cache_service import edhrec_cache_snapshot
from core.domains.decks.services.edhrec_client import edhrec_service_enabled
from core.domains.users.services.audit import record_audit_event
from core.services.admin_system_service import build_data_ops_context, load_symbols_context, site_request_counts, scheduler_status
from core.services.admin_user_management_service import (
    handle_delete_user,
    handle_reset_user_password,
    user_management_context,
)
from shared.database.fts import reindex_fts
from shared.events.live_updates import emit_job_event
from shared.jobs.background.edhrec_sync import refresh_edhrec_synergy_cache
from shared.jobs.jobs import (
    enqueue_scryfall_refresh,
    enqueue_spellbook_refresh,
    run_scryfall_refresh_inline,
    run_spellbook_refresh_inline,
)
from worker.tasks import recompute_oracle_enrichment, recompute_oracle_deck_tags

__all__ = ["render_admin_console"]


def _bytes_fmt(value: int) -> str:
    n = float(value)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def render_admin_console():
    inline_refresh = current_app.config.get("SCRYFALL_REFRESH_INLINE", False)
    symbols_context = load_symbols_context()
    symbols_enabled = bool(symbols_context.get("symbols_enabled"))
    ensure_symbols_cache = symbols_context.get("ensure_symbols_cache")
    symbols_json_path = symbols_context.get("symbols_json_path")
    symbols_svg_dir = symbols_context.get("symbols_svg_dir")

    def _inline_scryfall_refresh(kind: str, label: str, action_name: str, force_download: bool = False):
        try:
            info = run_scryfall_refresh_inline(kind, force_download=force_download)
            size = _bytes_fmt(info.get("bytes_downloaded") or info.get("size") or 0)
            flash(f"{label} refreshed immediately ({size}).", "success")
            record_audit_event(
                "admin_action",
                {
                    "action": f"{action_name}_inline",
                    "dataset": kind,
                    "bytes": info.get("bytes_downloaded"),
                },
            )
        except Exception as inline_exc:
            current_app.logger.exception("Inline %s refresh failed", kind)
            flash(f"Unable to refresh {label.lower()}: {inline_exc}", "danger")

    def _fallback_inline(kind: str, label: str, action: str, *, force_download: bool = False) -> bool:
        try:
            _inline_scryfall_refresh(kind, label, action, force_download=force_download)
            return True
        except Exception:
            current_app.logger.exception("Inline %s fallback failed", kind)
            return False

    def _refresh_symbols_dataset() -> dict:
        if not symbols_enabled or not ensure_symbols_cache:
            raise RuntimeError("Symbols module is not available.")
        job_id = f"symbols-{uuid.uuid4().hex[:8]}"
        emit_job_event("symbols", "queued", job_id=job_id, dataset="symbology")
        emit_job_event("symbols", "started", job_id=job_id, dataset="symbology", rq_id=None)
        try:
            sym_map, fetched_remote = ensure_symbols_cache(force=True, return_status=True)
            size = 0
            if symbols_json_path and symbols_json_path.exists():
                size = symbols_json_path.stat().st_size
            emit_job_event(
                "symbols",
                "completed",
                job_id=job_id,
                dataset="symbology",
                status="downloaded" if fetched_remote else "cached",
                bytes=size,
            )
            return {"map": sym_map, "job_id": job_id, "fetched": fetched_remote, "size": size}
        except Exception as exc:
            emit_job_event("symbols", "failed", job_id=job_id, dataset="symbology", error=str(exc))
            raise

    def _refresh_edhrec(force_refresh: bool, scope: str) -> dict:
        job_id = f"inline-{uuid.uuid4().hex[:8]}"
        emit_job_event(
            "edhrec",
            "queued",
            job_id=job_id,
            dataset="synergy",
            force=int(force_refresh),
            refresh_scope=scope,
        )
        emit_job_event(
            "edhrec",
            "started",
            job_id=job_id,
            dataset="synergy",
            rq_id=None,
            refresh_scope=scope,
        )
        result = refresh_edhrec_synergy_cache(force_refresh=force_refresh, scope=scope)
        status = result.get("status") or "error"
        message = result.get("message") or "EDHREC refresh failed."
        if status == "error":
            emit_job_event("edhrec", "failed", job_id=job_id, dataset="synergy", error=message)
            return result
        if status == "info":
            emit_job_event("edhrec", "completed", job_id=job_id, dataset="synergy", status="info", message=message)
            return result
        emit_job_event(
            "edhrec",
            "completed",
            job_id=job_id,
            dataset="synergy",
            status=status,
            message=message,
            commanders=result.get("commanders") or {},
            themes=result.get("themes") or {},
        )
        return result

    def _recompute_all_deck_stats() -> dict:
        job_id = f"deckstats-{uuid.uuid4().hex[:8]}"
        emit_job_event("deck_stats", "queued", job_id=job_id, dataset="recompute")
        emit_job_event("deck_stats", "started", job_id=job_id, dataset="recompute", rq_id=None)

        deck_ids = (
            db.session.query(Folder.id)
            .filter(Folder.role_entries.any(FolderRole.role.in_(FolderRole.DECK_ROLES)))
            .all()
        )
        total = len(deck_ids)
        updated = 0
        errors = 0

        for idx, (folder_id,) in enumerate(deck_ids, start=1):
            try:
                recompute_deck_stats(folder_id)
                db.session.commit()
                updated += 1
            except Exception as exc:
                db.session.rollback()
                errors += 1
                current_app.logger.warning("Deck stats recompute failed for folder %s: %s", folder_id, exc)

            if total and (idx == total or idx % 10 == 0):
                percent = int(round((idx / total) * 100))
                emit_job_event(
                    "deck_stats",
                    "progress",
                    job_id=job_id,
                    dataset="recompute",
                    percent=percent,
                    progress_text=f"{idx}/{total} decks",
                )

        status = "success" if errors == 0 else "warning"
        message = f"Recomputed deck stats for {updated}/{total} decks."
        if errors:
            message = f"{message} {errors} deck(s) failed."
        emit_job_event(
            "deck_stats",
            "completed",
            job_id=job_id,
            dataset="recompute",
            status=status,
            message=message,
        )
        return {"job_id": job_id, "total": total, "updated": updated, "errors": errors}

    def _rebuild_search_index() -> dict:
        job_id = f"search-{uuid.uuid4().hex[:8]}"
        emit_job_event("search_index", "queued", job_id=job_id, dataset="cards")
        emit_job_event("search_index", "started", job_id=job_id, dataset="cards", rq_id=None)

        if db.engine.dialect.name != "sqlite":
            message = "Search index rebuild is only required for SQLite deployments."
            emit_job_event(
                "search_index",
                "completed",
                job_id=job_id,
                dataset="cards",
                status="skipped",
                message=message,
            )
            return {"job_id": job_id, "status": "skipped", "message": message}

        try:
            reindex_fts()
            emit_job_event(
                "search_index",
                "completed",
                job_id=job_id,
                dataset="cards",
                status="ok",
                message="Search index rebuilt.",
            )
            return {"job_id": job_id, "status": "success"}
        except Exception as exc:
            emit_job_event(
                "search_index",
                "failed",
                job_id=job_id,
                dataset="cards",
                error="Search index rebuild failed.",
            )
            raise exc

    def _resolve_redirect_endpoint() -> str:
        requested = (request.form.get("redirect_to") or "").strip()
        if not requested:
            return "views.admin_console"
        try:
            url_for(requested)
        except Exception:
            return "views.admin_console"
        return requested

    def _should_run_inline(error: RuntimeError) -> bool:
        if inline_refresh:
            return True
        message = str(error or "").lower()
        return "rq is not installed" not in message

    if request.method == "POST":
        redirect_endpoint = _resolve_redirect_endpoint()
        redirect_url = url_for(redirect_endpoint)
        action = (request.form.get("action") or "").lower()

        if action == "clear_cache":
            try:
                count = clear_cache_files(include_default_cards=True)
                flash(f"Deleted {count} Scryfall cache file(s).", "success")
            except Exception as exc:
                current_app.logger.exception("Clear cache failed")
                flash(f"Failed to clear cache: {exc}", "danger")
            finally:
                record_audit_event("admin_action", {"action": action})
            return redirect(redirect_url)

        if action == "create_user":
            email = (request.form.get("user_email") or "").strip().lower()
            username = (request.form.get("user_username") or "").strip().lower()
            password = (request.form.get("user_password") or "").strip()
            is_admin_flag = request.form.get("user_is_admin") in {"1", "true", "on", "yes"}
            if not email or not password or not username:
                flash("Email, username, and password are required to create a user.", "warning")
                return redirect(redirect_url)
            if User.query.filter(func.lower(User.email) == email).first():
                flash(f"A user with email {email} already exists.", "danger")
                return redirect(redirect_url)
            if User.query.filter(func.lower(User.username) == username).first():
                flash(f"A user with username {username} already exists.", "danger")
                return redirect(redirect_url)
            new_user = User(email=email, username=username, display_name=None, is_admin=is_admin_flag)
            new_user.set_password(password)
            db.session.add(new_user)
            db.session.commit()
            record_audit_event("admin_create_user", {"email": email, "username": username, "is_admin": is_admin_flag})
            flash(f"Created user {email} / {username}.", "success")
            return redirect(redirect_url)

        if action == "reset_user_password":
            return handle_reset_user_password(redirect_endpoint)

        if action == "delete_user":
            return handle_delete_user(redirect_endpoint)

        if action == "refresh_symbols":
            if not symbols_enabled:
                flash("Symbols module is not available.", "warning")
                return redirect(redirect_url)
            job_id = None
            try:
                result = _refresh_symbols_dataset()
                job_id = result.get("job_id")
                sym_map = result.get("map") or {}
                fetched_remote = result.get("fetched")
                if fetched_remote:
                    flash(f"Refreshed {len(sym_map)} Scryfall symbols.", "success")
                else:
                    flash(
                        f"Used existing Scryfall symbols cache ({len(sym_map)} entries). Unable to reach api.scryfall.com right now.",
                        "warning",
                    )
            except Exception as exc:
                current_app.logger.exception("Symbol refresh failed")
                flash(f"Failed to refresh symbols: {exc}", "danger")
            finally:
                payload = {"action": action}
                if job_id:
                    payload["job_id"] = job_id
                record_audit_event("admin_action", payload)
            return redirect(redirect_url)

        if action == "refresh_rulings":
            if inline_refresh:
                _inline_scryfall_refresh("rulings", "Rulings", action)
                return redirect(redirect_url)
            try:
                job_id = enqueue_scryfall_refresh("rulings")
                flash(f"Queued rulings refresh (job {job_id}). Track progress below.", "info")
                record_audit_event("admin_action", {"action": action, "job_id": job_id, "mode": "queued"})
            except Exception as exc:
                if not _should_run_inline(exc):
                    flash(str(exc), "warning")
                else:
                    current_app.logger.warning("Unable to queue rulings refresh, running inline: %s", exc)
                    if not _fallback_inline("rulings", "Rulings", action):
                        flash(str(exc), "warning")
            return redirect(redirect_url)

        if action == "refresh_default_cards":
            if inline_refresh:
                _inline_scryfall_refresh("default_cards", "Default cards", action, force_download=True)
                return redirect(redirect_url)
            try:
                job_id = enqueue_scryfall_refresh("default_cards", force_download=True)
                flash(f"Queued default cards refresh (job {job_id}).", "info")
                record_audit_event("admin_action", {"action": action, "job_id": job_id})
            except RuntimeError as exc:
                if not _should_run_inline(exc):
                    flash(str(exc), "warning")
                else:
                    current_app.logger.warning("Unable to queue default cards refresh, running inline: %s", exc)
                    _inline_scryfall_refresh("default_cards", "Default cards", action, force_download=True)
            return redirect(redirect_url)

        if action == "reload_default_cache":
            try:
                ok = reload_cache()
                ensure_cache_loaded(force=True)
                if ok:
                    flash("In-memory Scryfall cache reloaded.", "success")
                else:
                    flash("Reload completed but the cache is empty. Download the default cards first.", "warning")
            except Exception as exc:
                current_app.logger.exception("Scryfall cache reload failed")
                flash(f"Failed to reload Scryfall cache: {exc}", "danger")
            finally:
                record_audit_event("admin_action", {"action": action})
            return redirect(redirect_url)

        if action == "refresh_spellbook_combos":
            try:
                if inline_refresh:
                    info = run_spellbook_refresh_inline()
                    flash(info.get("message"), info.get("status", "info"))
                    record_audit_event("admin_action", {"action": action, "mode": "inline"})
                else:
                    job_id = enqueue_spellbook_refresh()
                    flash(f"Queued Commander Spellbook refresh (job {job_id}). Track progress below.", "info")
                    record_audit_event("admin_action", {"action": action, "job_id": job_id, "mode": "queued"})
            except Exception as exc:
                current_app.logger.exception("Spellbook combo refresh failed")
                if not inline_refresh:
                    try:
                        info = run_spellbook_refresh_inline()
                        flash(info.get("message"), info.get("status", "info"))
                        record_audit_event("admin_action", {"action": action, "mode": "inline-fallback"})
                    except Exception:
                        flash(f"Failed to refresh Commander Spellbook combos: {exc}", "danger")
                else:
                    flash(f"Failed to refresh Commander Spellbook combos: {exc}", "danger")
            return redirect(redirect_url)

        if action == "refresh_oracle_tags":
            if not (cache_exists() and load_cache()):
                flash("No Scryfall bulk cache found. Download default cards first.", "warning")
                return redirect(redirect_url)
            try:
                recompute_oracle_deck_tags()
                flash("Oracle core roles and evergreen tags refreshed.", "success")
            except Exception as exc:
                current_app.logger.exception("Oracle tag refresh failed")
                flash(f"Failed to refresh oracle tags: {exc}", "danger")
            finally:
                record_audit_event("admin_action", {"action": action})
            return redirect(redirect_url)

        if action == "refresh_oracle_enrichment_full":
            if not (cache_exists() and load_cache()):
                flash("No Scryfall bulk cache found. Download default cards first.", "warning")
                return redirect(redirect_url)
            try:
                recompute_oracle_enrichment()
                flash("Oracle roles, keywords, typal tags, core roles, deck tags, and evergreen tags refreshed.", "success")
            except Exception as exc:
                current_app.logger.exception("Oracle enrichment refresh failed")
                flash(f"Failed to refresh oracle enrichment: {exc}", "danger")
            finally:
                record_audit_event("admin_action", {"action": action})
            return redirect(redirect_url)

        if action == "refresh_all":
            summary_success: List[str] = []
            detail_success: List[str] = []
            summary_warnings: List[str] = []
            detail_warnings: List[str] = []

            try:
                if inline_refresh:
                    _inline_scryfall_refresh("default_cards", "Default cards", action, force_download=True)
                    summary_success.append("default cards (inline)")
                else:
                    job_id = enqueue_scryfall_refresh("default_cards", force_download=True)
                    summary_success.append(f"default cards (job {job_id})")
            except Exception as exc:
                current_app.logger.exception("Refresh all: default cards refresh failed")
                if not inline_refresh and _fallback_inline("default_cards", "Default cards", action, force_download=True):
                    summary_success.append("default cards (inline fallback)")
                else:
                    summary_warnings.append(f"Default cards refresh failed to queue: {exc}")
            try:
                if inline_refresh:
                    _inline_scryfall_refresh("rulings", "Rulings", action)
                    summary_success.append("rulings (inline)")
                else:
                    job_id = enqueue_scryfall_refresh("rulings")
                    summary_success.append(f"rulings (job {job_id})")
            except Exception as exc:
                current_app.logger.exception("Refresh all: rulings refresh failed")
                if not inline_refresh and _fallback_inline("rulings", "Rulings", action):
                    summary_success.append("rulings (inline fallback)")
                else:
                    summary_warnings.append(f"Rulings refresh failed to queue: {exc}")

            if symbols_enabled:
                try:
                    result = _refresh_symbols_dataset()
                    sym_map = result.get("map") or {}
                    if result.get("fetched"):
                        summary_success.append(f"Scryfall symbols ({len(sym_map)})")
                    else:
                        summary_warnings.append(
                            "Scryfall symbols skipped (using cached data; api.scryfall.com unreachable)."
                        )
                except Exception as exc:
                    current_app.logger.exception("Refresh all: symbol refresh failed")
                    summary_warnings.append(f"Symbols refresh failed: {exc}")

            try:
                if inline_refresh:
                    info = run_spellbook_refresh_inline()
                    if info.get("status") == "success":
                        summary_success.append(info.get("message", "Commander Spellbook refreshed"))
                    else:
                        summary_warnings.append(info.get("message", "Commander Spellbook refresh had warnings."))
                else:
                    job_id = enqueue_spellbook_refresh()
                    summary_success.append(f"Commander Spellbook combos (job {job_id})")
            except Exception as exc:
                current_app.logger.exception("Refresh all: spellbook combo refresh failed")
                if not inline_refresh:
                    try:
                        info = run_spellbook_refresh_inline()
                        if info.get("status") == "success":
                            summary_success.append(info.get("message", "Commander Spellbook refreshed"))
                        else:
                            summary_warnings.append(info.get("message", "Commander Spellbook refresh had warnings."))
                    except Exception:
                        summary_warnings.append(f"Commander Spellbook combos failed: {exc}")
                else:
                    summary_warnings.append(f"Commander Spellbook combos failed: {exc}")

            try:
                edhrec_info = _refresh_edhrec(force_refresh=True, scope="full")
                status = edhrec_info.get("status")
                message = edhrec_info.get("message")
                if status == "success":
                    summary_success.append("EDHREC cache")
                    if message:
                        detail_success.append(message)
                elif status in {"warning", "info"}:
                    summary_warnings.append("EDHREC cache")
                    if message:
                        detail_warnings.append(message)
                else:
                    summary_warnings.append(message or "EDHREC refresh failed.")
                for err in edhrec_info.get("errors") or []:
                    detail_warnings.append(err)
            except Exception as exc:
                current_app.logger.exception("Refresh all: EDHREC refresh failed")
                summary_warnings.append(f"EDHREC refresh failed to start: {exc}")

            if summary_success:
                flash("Refresh all completed for: " + ", ".join(summary_success) + ".", "success")
            for msg in detail_success:
                flash(msg, "success")
            if summary_warnings:
                warning_html = "<ul>" + "".join(f"<li>{entry}</li>" for entry in summary_warnings) + "</ul>"
                flash("Refresh all encountered warnings:" + warning_html, "warning")
            for msg in detail_warnings:
                flash(msg, "warning")
            if not summary_success and not detail_success and not summary_warnings and not detail_warnings:
                flash("Refresh all completed without any actions.", "info")
            record_audit_event("admin_action", {"action": action})
            return redirect(redirect_url)

        if action == "refresh_edhrec":
            force_refresh = bool(request.form.get("force_refresh"))
            refresh_scope = (request.form.get("refresh_scope") or "delta").strip().lower()
            try:
                info = _refresh_edhrec(force_refresh=force_refresh, scope=refresh_scope)
                status = info.get("status", "info")
                message = info.get("message", "EDHREC refresh completed.")
                if status == "error":
                    flash(message, "danger")
                elif status == "warning":
                    flash(message, "warning")
                else:
                    flash(message, "success")
                for err in info.get("errors") or []:
                    flash(err, "warning")
                record_audit_event(
                    "admin_action",
                    {"action": action, "force": force_refresh, "scope": refresh_scope},
                )
            except Exception as exc:
                current_app.logger.exception("EDHREC refresh failed")
                flash(f"Failed to refresh EDHREC cache: {exc}", "danger")
            return redirect(redirect_url)

        if action == "recompute_deck_stats":
            try:
                info = _recompute_all_deck_stats()
                updated = info.get("updated", 0)
                total = info.get("total", 0)
                errors = info.get("errors", 0)
                if errors:
                    flash(f"Recomputed deck stats for {updated}/{total} decks with {errors} error(s).", "warning")
                else:
                    flash(f"Recomputed deck stats for {updated}/{total} decks.", "success")
                record_audit_event("admin_action", {"action": action, "updated": updated, "errors": errors})
            except Exception as exc:
                current_app.logger.exception("Deck stats recompute failed")
                flash(f"Failed to recompute deck stats: {exc}", "danger")
            return redirect(redirect_url)

        if action == "rebuild_search_index":
            try:
                info = _rebuild_search_index()
                status = info.get("status")
                message = info.get("message")
                if status == "skipped":
                    flash(message or "Search index rebuild skipped.", "warning")
                else:
                    flash("Search index rebuilt successfully.", "success")
                record_audit_event("admin_action", {"action": action, "status": status})
            except Exception:
                current_app.logger.exception("Search index rebuild failed")
                flash("Failed to rebuild search index. Check logs for details.", "danger")
            return redirect(redirect_url)

        if action == "validate_database":
            try:
                if db.engine.dialect.name == "sqlite":
                    result = db.session.execute(text("PRAGMA integrity_check;")).scalar()
                    if str(result).strip().lower() == "ok":
                        flash("Database integrity check passed.", "success")
                    else:
                        flash("Database integrity check returned warnings.", "warning")
                else:
                    db.session.execute(text("SELECT 1"))
                    flash("Database connectivity check passed.", "success")
                record_audit_event("admin_action", {"action": action})
            except Exception:
                db.session.rollback()
                current_app.logger.exception("Database validation failed")
                flash("Database validation failed. Check logs for details.", "danger")
            return redirect(redirect_url)

        if action == "check_orphaned_decks":
            try:
                orphaned = (
                    db.session.query(Folder.id)
                    .outerjoin(Card, Card.folder_id == Folder.id)
                    .filter(Folder.role_entries.any(FolderRole.role.in_(FolderRole.DECK_ROLES)))
                    .group_by(Folder.id)
                    .having(func.count(Card.id) == 0)
                    .all()
                )
                count = len(orphaned)
                if count:
                    flash(f"Found {count} deck(s) with no cards.", "warning")
                else:
                    flash("No orphaned decks found.", "success")
                record_audit_event("admin_action", {"action": action, "count": count})
            except Exception:
                db.session.rollback()
                current_app.logger.exception("Orphaned deck check failed")
                flash("Failed to check orphaned decks. Check logs for details.", "danger")
            return redirect(redirect_url)

        if action == "verify_tag_integrity":
            try:
                deck_folders = Folder.query.filter(
                    Folder.role_entries.any(FolderRole.role.in_(FolderRole.DECK_ROLES))
                ).all()
                invalid = [folder for folder in deck_folders if folder.deck_tag and not is_valid_deck_tag(folder.deck_tag)]
                if invalid:
                    flash(f"Found {len(invalid)} deck(s) with unknown tags.", "warning")
                else:
                    flash("All deck tags match the current vocabulary.", "success")
                record_audit_event("admin_action", {"action": action, "count": len(invalid)})
            except Exception:
                db.session.rollback()
                current_app.logger.exception("Tag integrity check failed")
                flash("Failed to verify tag integrity. Check logs for details.", "danger")
            return redirect(redirect_url)

    data_ops = build_data_ops_context(
        symbols_json_path=symbols_json_path,
        symbols_svg_dir=symbols_svg_dir,
        symbols_enabled=symbols_enabled,
    )

    all_folders = Folder.query.order_by(func.lower(Folder.name)).all()
    deck_folders = [folder for folder in all_folders if not folder.is_collection]
    folder_counts = {
        "deck": len(deck_folders),
        "collection": len(all_folders) - len(deck_folders),
        "total": len(all_folders),
    }
    edhrec_snapshot = edhrec_cache_snapshot() if edhrec_service_enabled() else {"status": "disabled"}
    edhrec_error = None
    if edhrec_snapshot.get("status") == "error":
        edhrec_error = edhrec_snapshot.get("error") or "EDHREC service unavailable."
    elif edhrec_snapshot.get("status") == "disabled":
        edhrec_error = "EDHREC service is not configured."
    edhrec = {
        "enabled": edhrec_service_enabled(),
        "error": edhrec_error,
        "commanders": edhrec_snapshot.get("commanders", {}),
        "themes": edhrec_snapshot.get("tags", {}),
        "metadata": edhrec_snapshot.get("metadata", {}) or {},
    }
    user_context = user_management_context(include_users=False)
    request_counts = site_request_counts()
    sched = scheduler_status()
    return render_template(
        "admin/admin.html",
        prints=data_ops["prints"],
        rulings=data_ops["rulings"],
        spellbook=data_ops["spellbook"],
        symbols=data_ops["symbols"],
        stats=data_ops["stats"],
        symbols_enabled=data_ops["symbols_enabled"],
        folder_counts=folder_counts,
        edhrec=edhrec,
        user_stats=user_context["user_stats"],
        request_counts=request_counts,
        scheduler=sched,
    )
