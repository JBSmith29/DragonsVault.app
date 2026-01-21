"""Administrative routes for managing folders and Scryfall bulk data."""

from __future__ import annotations

import gzip
import json
import os
import time
import uuid
from datetime import datetime, timezone
from math import ceil
from pathlib import Path
from typing import List, Optional, Set

from flask import current_app, flash, jsonify, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user
from sqlalchemy import func, or_, text
from sqlalchemy.orm import selectinload

from extensions import db, limiter
from models import (
    AuditLog,
    CommanderBracketCache,
    DeckStats,
    Folder,
    FolderRole,
    FolderShare,
    GameDeck,
    GameSeat,
    GameSeatAssignment,
    GameSession,
    SiteRequest,
    User,
)
from core.domains.cards.services import scryfall_cache as sc
from core.domains.cards.services.scryfall_cache import ensure_cache_loaded
from shared.jobs.jobs import (
    enqueue_scryfall_refresh,
    run_scryfall_refresh_inline,
    enqueue_spellbook_refresh,
    run_spellbook_refresh_inline,
)
from shared.events.live_updates import emit_job_event, latest_job_events
from core.domains.decks.services.spellbook_sync import EARLY_MANA_VALUE_THRESHOLD, LATE_MANA_VALUE_THRESHOLD
from shared.auth import require_admin
from core.domains.users.services.audit import record_audit_event
from shared.jobs.background.edhrec_sync import refresh_edhrec_synergy_cache
from shared.jobs.background.oracle_recompute import ORACLE_DECK_TAG_VERSION, oracle_deck_tag_source_version
from core.domains.decks.services.edhrec_cache_service import edhrec_cache_snapshot
from core.domains.decks.services.edhrec_client import edhrec_service_enabled
from core.domains.decks.services.deck_service import recompute_deck_stats
from core.domains.decks.services.deck_tags import get_all_deck_tags, is_valid_deck_tag
from shared.database.fts import reindex_fts
from shared.cache.request_cache import request_cached
from .base import limiter_key_user_or_ip
from core.domains.users.routes.auth import MIN_PASSWORD_LENGTH
from .base import DEFAULT_COLLECTION_FOLDERS, _safe_commit, views
from shared.validation import (
    ValidationError,
    log_validation_error,
    parse_optional_positive_int,
    parse_positive_int,
    parse_positive_int_list,
)
from models.card import Card
from models.role import (
    Role,
    SubRole,
    CardRole,
    CardSubRole,
    OracleRole,
    OracleCoreRoleTag,
    OracleDeckTag,
    OracleEvergreenTag,
    DeckTagCoreRoleSynergy,
    DeckTagEvergreenSynergy,
    DeckTagCardSynergy,
)
from worker.tasks import recompute_oracle_enrichment, recompute_oracle_deck_tags


def _folder_categories_page(admin_mode: bool):
    if admin_mode:
        require_admin()
    folders_query = Folder.query.order_by(func.lower(Folder.name))
    user_id = current_user.id if current_user.is_authenticated else None
    if not admin_mode:
        if not user_id:
            flash("You must be signed in to manage folders.", "warning")
            return redirect(url_for("views.login"))
        folders_query = folders_query.filter(Folder.owner_user_id == user_id)
    folders = folders_query.all()
    users = User.query.order_by(func.lower(User.email)).all() if admin_mode else []
    user_lookup = {str(user.id): user.id for user in users}
    target_endpoint = "views.admin_folder_categories" if admin_mode else "views.manage_folder_preferences"

    bulk_action = (request.form.get("bulk_action") or "").strip().lower()

    if request.method == "POST":
        delete_ids_raw = request.form.getlist("delete_folder_ids")
        single_delete_id = request.form.get("delete_folder_id")
        if bulk_action not in {"bulk_edit", "bulk_share"}:
            if single_delete_id:
                delete_ids_raw.append(single_delete_id)
            try:
                delete_ids_list = parse_positive_int_list(delete_ids_raw, field="folder id(s)")
            except ValidationError as exc:
                log_validation_error(exc, context="admin_folder_delete")
                flash("Invalid folder id.", "danger")
                return redirect(url_for(target_endpoint))
            delete_ids = set(delete_ids_list)

            if delete_ids:
                to_delete = [f for f in folders if f.id in delete_ids]
                if not to_delete:
                    flash("Folder not found.", "warning")
                    return redirect(url_for(target_endpoint))

                deleted_info = []
                deleted_cards = 0
                for folder_to_delete in to_delete:
                    folder_name = folder_to_delete.name or "Folder"
                    deleted_info.append((folder_to_delete.id, folder_name))
                    counts = _purge_folder(folder_to_delete)
                    deleted_cards += counts.get("cards", 0)
                _safe_commit()
                record_audit_event(
                    "folder_deleted",
                    {
                        "folder_ids": [fid for fid, _ in deleted_info],
                        "names": [name for _, name in deleted_info],
                        "cards_deleted": deleted_cards,
                    },
                )
                if len(deleted_info) == 1:
                    flash(f'Deleted folder "{deleted_info[0][1]}" and all associated cards.', "success")
                else:
                    flash(f"Deleted {len(deleted_info)} folders and all associated cards.", "success")
                return redirect(url_for(target_endpoint))

        if bulk_action == "bulk_edit":
            raw_selected = request.form.get("bulk_folder_ids") or ""
            try:
                selected_ids_list = parse_positive_int_list(raw_selected.split(","), field="folder id(s)")
            except ValidationError as exc:
                log_validation_error(exc, context="admin_folder_bulk_edit")
                flash("Invalid folder selection.", "warning")
                return redirect(url_for(target_endpoint))
            selected_ids: set[int] = set(selected_ids_list)

            if not selected_ids:
                flash("Select at least one folder to bulk edit.", "warning")
                return redirect(url_for(target_endpoint))

            category_input = (request.form.get("bulk_category") or "").strip()
            owner_apply = request.form.get("bulk_owner_apply") == "1"
            owner_value = (request.form.get("bulk_owner_value") or "").strip()
            proxy_value_raw = (request.form.get("bulk_proxy") or "").strip().lower()
            notes_apply = request.form.get("bulk_notes_apply") == "1"
            notes_value = (request.form.get("bulk_notes_value") or "").strip()

            allowed_categories = {Folder.CATEGORY_DECK, Folder.CATEGORY_COLLECTION}
            proxy_flag = None
            if proxy_value_raw in {"proxy", "on", "1", "true"}:
                proxy_flag = True
            elif proxy_value_raw in {"owned", "off", "0", "false"}:
                proxy_flag = False

            updated = {"category": 0, "owner": 0, "proxy": 0, "notes": 0}
            for folder in folders:
                if folder.id not in selected_ids:
                    continue
                if category_input in allowed_categories and folder.category != category_input:
                    folder.category = category_input
                    folder.set_primary_role(category_input)
                    updated["category"] += 1
                if owner_apply:
                    new_owner = owner_value or None
                    if folder.owner != new_owner:
                        folder.owner = new_owner
                        updated["owner"] += 1
                if proxy_flag is not None and folder.is_proxy != proxy_flag:
                    folder.is_proxy = proxy_flag
                    updated["proxy"] += 1
                if notes_apply:
                    new_notes = notes_value or None
                    if (folder.notes or None) != new_notes:
                        folder.notes = new_notes
                        updated["notes"] += 1

            _safe_commit()
            changed_fields = [name for name, count in updated.items() if count]
            if changed_fields:
                flash(
                    f"Bulk updated {len(selected_ids)} folder(s) ({', '.join(changed_fields)}).",
                    "success",
                )
            else:
                flash("No bulk updates were applied.", "info")
            record_audit_event(
                "folder_bulk_updated",
                {"selected": list(selected_ids), "changes": updated},
            )
            return redirect(url_for(target_endpoint))

        if bulk_action == "bulk_share":
            if admin_mode:
                flash("Bulk sharing is only available from your account folders.", "warning")
                return redirect(url_for(target_endpoint))

            raw_selected = request.form.get("bulk_folder_ids") or ""
            try:
                selected_ids_list = parse_positive_int_list(raw_selected.split(","), field="folder id(s)")
            except ValidationError as exc:
                log_validation_error(exc, context="admin_folder_bulk_share")
                flash("Invalid folder selection.", "warning")
                return redirect(url_for(target_endpoint))
            selected_ids: set[int] = set(selected_ids_list)

            if not selected_ids:
                flash("Select at least one folder to share.", "warning")
                return redirect(url_for(target_endpoint))

            share_identifier_raw = (request.form.get("bulk_share_identifier") or "").strip()
            share_identifier = share_identifier_raw.lower()
            if not share_identifier:
                flash("Provide an email or username to share with.", "warning")
                return redirect(url_for(target_endpoint))

            target_user = (
                User.query.filter(func.lower(User.email) == share_identifier).first()
                or User.query.filter(func.lower(User.username) == share_identifier).first()
            )
            if not target_user:
                flash("No user found with that email or username.", "warning")
                return redirect(url_for(target_endpoint))

            folder_lookup = {folder.id: folder for folder in folders}
            selected_folders = [folder_lookup[fid] for fid in selected_ids if fid in folder_lookup]
            if not selected_folders:
                flash("Selected folders were not found.", "warning")
                return redirect(url_for(target_endpoint))

            added = 0
            already_shared = 0
            skipped_owned = 0

            for folder in selected_folders:
                if folder.owner_user_id == target_user.id:
                    skipped_owned += 1
                    continue
                existing = FolderShare.query.filter_by(folder_id=folder.id, shared_user_id=target_user.id).first()
                if existing:
                    already_shared += 1
                    continue
                db.session.add(FolderShare(folder_id=folder.id, shared_user_id=target_user.id))
                added += 1

            if added:
                _safe_commit()

            message_bits = []
            if added:
                message_bits.append(
                    f"Shared {added} folder{'s' if added != 1 else ''} with {target_user.username or target_user.email}."
                )
            if already_shared:
                message_bits.append(f"{already_shared} already shared.")
            if skipped_owned:
                message_bits.append(f"{skipped_owned} already owned by the recipient.")
            if not message_bits:
                message_bits.append("No shares were created.")

            flash(" ".join(message_bits), "success" if added else "info")
            record_audit_event(
                "folder_bulk_shared",
                {
                    "selected": list(selected_ids),
                    "shared_with": target_user.id,
                    "added": added,
                    "already_shared": already_shared,
                    "owned_by_target": skipped_owned,
                },
            )
            return redirect(url_for(target_endpoint))

        updated_categories = 0
        updated_owners = 0
        updated_owner_links = 0
        updated_proxies = 0
        updated_notes = 0
        updated_public = 0
        allowed_categories = {Folder.CATEGORY_DECK, Folder.CATEGORY_COLLECTION}
        for folder in folders:
            submitted = request.form.get(f"category-{folder.id}", Folder.CATEGORY_DECK)
            if submitted not in allowed_categories:
                submitted = Folder.CATEGORY_DECK
            if folder.category != submitted:
                folder.category = submitted
                folder.set_primary_role(submitted)
                updated_categories += 1

            owner_value = (request.form.get(f"owner-{folder.id}") or "").strip() or None
            if (folder.owner or None) != owner_value:
                folder.owner = owner_value
                updated_owners += 1

            if admin_mode:
                owner_user_raw = (request.form.get(f"owner_user-{folder.id}") or "").strip()
                owner_user_id = user_lookup.get(owner_user_raw) if owner_user_raw else None
                if folder.owner_user_id != owner_user_id:
                    folder.owner_user_id = owner_user_id
                    updated_owner_links += 1

            proxy_raw = request.form.get(f"proxy-{folder.id}")
            proxy_value = proxy_raw in {"1", "on", "true", "yes"}
            if folder.is_proxy != proxy_value:
                folder.is_proxy = proxy_value
                updated_proxies += 1

            public_raw = request.form.get(f"public-{folder.id}")
            if public_raw is not None:
                public_value = public_raw in {"1", "on", "true", "yes"}
                if folder.is_public != public_value:
                    folder.is_public = public_value
                    updated_public += 1

            notes_value = (request.form.get(f"notes-{folder.id}") or "").strip() or None
            if (folder.notes or None) != notes_value:
                folder.notes = notes_value
                updated_notes += 1

        _safe_commit()
        changes = []
        if updated_categories:
            changes.append(f"{updated_categories} categor{'y' if updated_categories == 1 else 'ies'}")
        if updated_proxies:
            changes.append(f"{updated_proxies} proxy flag{'s' if updated_proxies != 1 else ''}")
        if updated_owners:
            changes.append(f"{updated_owners} owner field{'s' if updated_owners != 1 else ''}")
        if updated_owner_links:
            changes.append(f"{updated_owner_links} owner assignment{'s' if updated_owner_links != 1 else ''}")
        if updated_public:
            changes.append(f"{updated_public} public flag{'s' if updated_public != 1 else ''}")
        if updated_notes:
            changes.append(f"{updated_notes} note{'s' if updated_notes != 1 else ''}")
        if changes:
            flash(f"Updated {', '.join(changes)}.", "success")
        else:
            flash("No folder settings changed.", "info")
        record_audit_event(
            "folder_settings_updated",
            {
                "categories": updated_categories,
                "owners": updated_owners,
                "owner_links": updated_owner_links,
                "proxies": updated_proxies,
                "public": updated_public,
                "notes": updated_notes,
            },
        )
        return redirect(url_for(target_endpoint))

    deck_count = sum(1 for folder in folders if not folder.is_collection)
    collection_count = sum(1 for folder in folders if folder.is_collection)
    proxy_count = sum(1 for folder in folders if getattr(folder, "is_proxy", False))

    return render_template(
        "admin/admin_folder_categories.html",
        folders=folders,
        users=users,
        deck_category=Folder.CATEGORY_DECK,
        collection_category=Folder.CATEGORY_COLLECTION,
        default_collection=sorted(DEFAULT_COLLECTION_FOLDERS),
        deck_count=deck_count,
        collection_count=collection_count,
        proxy_count=proxy_count,
        show_owner_controls=admin_mode,
        show_owner_field=True,
        allow_delete=True,
        allow_share_controls=not admin_mode,
        show_public_toggle=admin_mode,
        back_url=url_for("views.admin_console") if admin_mode else url_for("views.account_center"),
        page_title="Folder Categories" if admin_mode else "My Folders",
    )


def _user_management_context(include_users: bool = False) -> dict:
    """Collect user counts and optionally hydrate the full listing."""
    user_stats = {"total": 0, "admins": 0}
    users: list[User] = []
    folder_owner_counts: dict[int, int] = {}
    try:
        user_stats["total"] = db.session.query(func.count(User.id)).scalar() or 0
        user_stats["admins"] = (
            db.session.query(func.count(User.id))
            .filter(User.is_admin.is_(True))
            .scalar()
            or 0
        )
    except Exception:
        current_app.logger.exception("Failed to load user stats")
        db.session.rollback()
    if include_users:
        users = User.query.order_by(func.lower(User.email)).all()
        folder_owner_counts = {
            owner_id: count
            for owner_id, count in (
                db.session.query(Folder.owner_user_id, func.count(Folder.id))
                .filter(Folder.owner_user_id.isnot(None))
                .group_by(Folder.owner_user_id)
                .all()
            )
            if owner_id is not None
        }
    return {
        "users": users,
        "folder_owner_counts": folder_owner_counts,
        "user_stats": user_stats,
    }


def _site_request_counts() -> dict[str, int]:
    """Summaries for contact submissions (bugs/features)."""
    counts = {status: 0 for status in SiteRequest.STATUSES}
    try:
        rows = (
            db.session.query(SiteRequest.status, func.count(SiteRequest.id))
            .group_by(SiteRequest.status)
            .all()
        )
        for status, total in rows:
            if status in counts:
                counts[status] = total or 0
    except Exception:
        current_app.logger.exception("Failed to load site request counts")
        db.session.rollback()
    counts["total"] = sum(counts.values())
    return counts


def _load_symbols_context() -> dict[str, Optional[object]]:
    symbols_json_path: Optional[Path] = None
    symbols_svg_dir: Optional[Path] = None
    try:
        from core.shared.utils.symbols_cache import ensure_symbols_cache, SYMBOLS_JSON, SYMBOLS_DIR

        return {
            "symbols_enabled": True,
            "ensure_symbols_cache": ensure_symbols_cache,
            "symbols_json_path": SYMBOLS_JSON,
            "symbols_svg_dir": SYMBOLS_DIR,
        }
    except Exception:
        return {
            "symbols_enabled": False,
            "ensure_symbols_cache": None,
            "symbols_json_path": symbols_json_path,
            "symbols_svg_dir": symbols_svg_dir,
        }


def _build_data_ops_context(
    *,
    symbols_json_path: Optional[Path],
    symbols_svg_dir: Optional[Path],
    symbols_enabled: bool,
) -> dict[str, object]:
    from core.domains.cards.services.scryfall_cache import (
        DEFAULT_PATH,
        cache_stats,
        is_stale,
        rulings_bulk_path,
    )

    data_dir = Path(DEFAULT_PATH).parent
    rulings_path = Path(rulings_bulk_path())
    prints_path = Path(DEFAULT_PATH)
    spellbook_path = data_dir / "spellbook_combos.json"

    prints_exists = prints_path.exists()
    prints_size = prints_path.stat().st_size if prints_exists else 0
    prints_mtime = (
        time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(prints_path.stat().st_mtime))
        if prints_exists
        else None
    )
    prints_stale = is_stale(prints_path) if prints_exists else False

    rulings_exists = rulings_path.exists()
    rulings_size = rulings_path.stat().st_size if rulings_exists else 0
    rulings_mtime = (
        time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(rulings_path.stat().st_mtime))
        if rulings_exists
        else None
    )

    spellbook_exists = spellbook_path.exists()
    spellbook_size = spellbook_path.stat().st_size if spellbook_exists else 0
    spellbook_mtime = (
        time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(spellbook_path.stat().st_mtime))
        if spellbook_exists
        else None
    )
    spellbook_counts = {"early": 0, "late": 0, "total": 0}
    spellbook_categories = {}
    if spellbook_exists:
        try:
            payload = json.loads(spellbook_path.read_text(encoding="utf-8"))
            spellbook_counts["early"] = len(payload.get("early_game", []))
            spellbook_counts["late"] = len(payload.get("late_game", []))
            spellbook_counts["total"] = spellbook_counts["early"] + spellbook_counts["late"]
            raw_counts = payload.get("counts") or {}
            spellbook_categories = {
                key.replace("category_", ""): value
                for key, value in raw_counts.items()
                if key.startswith("category_")
            }
        except Exception as exc:
            current_app.logger.warning("Failed to read spellbook dataset info: %s", exc)
            spellbook_counts = {"early": 0, "late": 0, "total": 0}
            spellbook_categories = {}

    fallback_svg_dir = Path(current_app.static_folder or "static") / "symbols"
    symbols_stats = {
        "exists": False,
        "path": str(symbols_json_path) if symbols_json_path else "N/A",
        "size": 0,
        "mtime": None,
        "entries": 0,
        "svg_dir": str(symbols_svg_dir or fallback_svg_dir),
        "svg_exists": False,
        "svg_count": 0,
    }
    if symbols_json_path:
        symbols_stats["path"] = str(symbols_json_path)
        if symbols_json_path.exists():
            symbols_stats["exists"] = True
            symbols_stats["size"] = symbols_json_path.stat().st_size
            symbols_stats["mtime"] = time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime(symbols_json_path.stat().st_mtime)
            )
            try:
                payload = json.loads(symbols_json_path.read_text(encoding="utf-8"))
                symbols_stats["entries"] = len(payload.get("data") or [])
            except Exception as exc:
                current_app.logger.warning("Failed to read symbols dataset info: %s", exc)
    svg_dir = symbols_svg_dir or fallback_svg_dir
    symbols_stats["svg_dir"] = str(svg_dir)
    if svg_dir.exists():
        symbols_stats["svg_exists"] = True
        symbols_stats["svg_count"] = sum(1 for entry in svg_dir.glob("*.svg") if entry.is_file())

    try:
        stats = sc.cache_stats() if hasattr(sc, "cache_stats") else {}
    except Exception as exc:
        current_app.logger.warning("cache_stats unavailable: %s", exc)
        stats = {"prints": None, "unique_oracle": None, "rulings": None}

    return {
        "prints": {
            "exists": prints_exists,
            "path": str(prints_path),
            "size": prints_size,
            "mtime": prints_mtime,
            "stale": prints_stale,
        },
        "rulings": {
            "exists": rulings_exists,
            "path": str(rulings_path),
            "size": rulings_size,
            "mtime": rulings_mtime,
        },
        "spellbook": {
            "exists": spellbook_exists,
            "path": str(spellbook_path),
            "size": spellbook_size,
            "mtime": spellbook_mtime,
            "counts": spellbook_counts,
            "categories": spellbook_categories,
            "early_threshold": EARLY_MANA_VALUE_THRESHOLD,
            "late_threshold": LATE_MANA_VALUE_THRESHOLD,
        },
        "symbols": symbols_stats,
        "stats": stats,
        "symbols_enabled": symbols_enabled,
    }


def _format_job_timestamp(raw: str | None) -> str | None:
    if not raw:
        return None
    token = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(token)
    except ValueError:
        return raw
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)
    return parsed.strftime("%Y-%m-%d %H:%M UTC")


def _job_badge(event_type: str, status: str | None) -> tuple[str, str]:
    normalized = (status or "").strip().lower()
    if event_type == "completed":
        if normalized == "skipped":
            return "Skipped", "secondary"
        if normalized in {"warning", "warn"}:
            return "Warning", "warning"
        if normalized in {"error", "failed"}:
            return "Error", "danger"
        return "Complete", "success"
    if event_type == "failed":
        return "Failed", "danger"
    if event_type == "started":
        return "Running", "warning"
    if event_type == "queued":
        return "Queued", "info"
    return "Unknown", "secondary"


def _job_last_run(scope: str, dataset: str | None, fallback_stamp: str | None = None) -> dict[str, str]:
    events = latest_job_events(scope, dataset)
    if not events:
        if fallback_stamp:
            return {"stamp": fallback_stamp, "label": "Cached", "tone": "secondary"}
        return {"stamp": "Never", "label": "Never", "tone": "secondary"}
    chosen = None
    for event in reversed(events):
        if event.get("type") in {"completed", "failed"}:
            chosen = event
            break
    if chosen is None:
        chosen = events[-1]
    event_type = (chosen.get("type") or "unknown").lower()
    status = chosen.get("status") or chosen.get("download_status")
    label, tone = _job_badge(event_type, status)
    stamp = _format_job_timestamp(chosen.get("recorded_at")) or "Unknown"
    return {"stamp": stamp, "label": label, "tone": tone}


def _handle_reset_user_password(target_endpoint: str):
    """Shared handler for admin password resets."""
    redirect_target = url_for(target_endpoint)
    target_user_raw = request.form.get("target_user_id")
    new_password = (request.form.get("new_password") or "").strip()
    if not target_user_raw:
        flash("Select a user to reset.", "warning")
        return redirect(redirect_target)
    try:
        target_user_id = parse_positive_int(target_user_raw, field="user id")
    except ValidationError as exc:
        log_validation_error(exc, context="admin_reset_password")
        flash("Invalid user id.", "danger")
        return redirect(redirect_target)
    if len(new_password) < MIN_PASSWORD_LENGTH:
        flash(f"Password must be at least {MIN_PASSWORD_LENGTH} characters long.", "warning")
        return redirect(redirect_target)
    target_user = db.session.get(User, target_user_id)
    if not target_user:
        flash("User not found.", "warning")
        return redirect(redirect_target)
    target_user.set_password(new_password)
    db.session.commit()
    record_audit_event(
        "admin_reset_user_password",
        {"target_id": target_user.id, "email": target_user.email},
    )
    flash(f"Password updated for {target_user.email}.", "success")
    return redirect(redirect_target)


def _purge_folder(folder: Folder) -> dict[str, int]:
    folder_id = folder.id
    deleted_cards = (
        db.session.query(Card)
        .filter(Card.folder_id == folder_id)
        .delete(synchronize_session=False)
    )
    deleted_roles = (
        db.session.query(FolderRole)
        .filter(FolderRole.folder_id == folder_id)
        .delete(synchronize_session=False)
    )
    deleted_shares = (
        db.session.query(FolderShare)
        .filter(FolderShare.folder_id == folder_id)
        .delete(synchronize_session=False)
    )
    deleted_stats = (
        db.session.query(DeckStats)
        .filter(DeckStats.folder_id == folder_id)
        .delete(synchronize_session=False)
    )
    deleted_bracket_cache = (
        db.session.query(CommanderBracketCache)
        .filter(CommanderBracketCache.folder_id == folder_id)
        .delete(synchronize_session=False)
    )
    db.session.delete(folder)
    return {
        "cards": deleted_cards,
        "roles": deleted_roles,
        "shares": deleted_shares,
        "stats": deleted_stats,
        "bracket_cache": deleted_bracket_cache,
    }


def _handle_delete_user(target_endpoint: str):
    """Shared handler for admin account deletions."""
    redirect_target = url_for(target_endpoint)
    target_user_raw = request.form.get("target_user_id")
    if not target_user_raw:
        flash("Select a user to delete.", "warning")
        return redirect(redirect_target)
    try:
        target_user_id = parse_positive_int(target_user_raw, field="user id")
    except ValidationError as exc:
        log_validation_error(exc, context="admin_delete_user")
        flash("Invalid user id.", "danger")
        return redirect(redirect_target)
    if current_user.is_authenticated and target_user_id == current_user.id:
        flash("You cannot delete the account that is currently signed in.", "warning")
        return redirect(redirect_target)
    target_user = db.session.get(User, target_user_id)
    if not target_user:
        flash("User not found.", "warning")
        return redirect(redirect_target)
    if target_user.is_admin:
        remaining_admins = (
            User.query.filter(User.is_admin.is_(True), User.id != target_user.id).count()
        )
        if remaining_admins == 0:
            flash("Cannot delete the last administrator.", "warning")
            return redirect(redirect_target)
    owned_folders = Folder.query.filter(Folder.owner_user_id == target_user.id).all()
    deleted_folders = 0
    deleted_cards = 0
    deleted_folder_names: list[str] = []
    for folder in owned_folders:
        deleted_folders += 1
        deleted_folder_names.append(folder.name or "Folder")
        counts = _purge_folder(folder)
        deleted_cards += counts.get("cards", 0)
    removed_shares = (
        db.session.query(FolderShare)
        .filter(FolderShare.shared_user_id == target_user.id)
        .delete(synchronize_session=False)
    )
    audit_entries_detached = (
        AuditLog.query.filter(AuditLog.user_id == target_user.id)
        .update({AuditLog.user_id: None}, synchronize_session=False)
    )
    removed_email = target_user.email
    removed_username = target_user.username
    db.session.delete(target_user)
    db.session.commit()
    record_audit_event(
        "admin_delete_user",
        {
            "target_id": target_user_id,
            "email": removed_email,
            "username": removed_username,
            "folders_deleted": deleted_folders,
            "folder_names": deleted_folder_names,
            "cards_deleted": deleted_cards,
            "shares_removed": removed_shares,
            "audit_entries_detached": audit_entries_detached,
        },
    )
    if deleted_folders:
        flash(
            f"Deleted user {removed_email} along with {deleted_folders} folder(s) and {deleted_cards} card(s).",
            "success",
        )
    else:
        flash(f"Deleted user {removed_email}.", "success")
    return redirect(redirect_target)


def _handle_impersonate_user(target_endpoint: str):
    """Start an admin impersonation session for a target user."""
    redirect_target = url_for(target_endpoint)
    target_user_raw = request.form.get("target_user_id")
    if not target_user_raw:
        flash("Select a user to impersonate.", "warning")
        return redirect(redirect_target)
    try:
        target_user_id = parse_positive_int(target_user_raw, field="user id")
    except ValidationError as exc:
        log_validation_error(exc, context="admin_impersonate")
        flash("Invalid user id.", "danger")
        return redirect(redirect_target)
    if current_user.is_authenticated and target_user_id == current_user.id:
        flash("You are already signed in as this user.", "info")
        return redirect(redirect_target)
    if session.get("impersonator_id"):
        flash("Stop the current impersonation before starting another.", "warning")
        return redirect(redirect_target)
    target_user = db.session.get(User, target_user_id)
    if not target_user:
        flash("User not found.", "warning")
        return redirect(redirect_target)

    session["impersonator_id"] = current_user.id if current_user.is_authenticated else None
    session["impersonated_user_id"] = target_user.id

    record_audit_event(
        "admin_impersonate_start",
        {
            "admin_id": current_user.id if current_user.is_authenticated else None,
            "target_id": target_user.id,
            "target_email": target_user.email,
            "target_username": target_user.username,
        },
    )
    login_user(target_user, remember=False, fresh=True)
    session["user_is_admin"] = bool(target_user.is_admin)
    flash(f"Now impersonating {target_user.username or target_user.email}.", "info")
    return redirect(url_for("views.dashboard"))


@views.route("/admin/folder-categories", methods=["GET", "POST"])
@limiter.limit("20 per minute", methods=["POST"], key_func=limiter_key_user_or_ip) if limiter else (lambda f: f)
@login_required
def admin_folder_categories():
    return _folder_categories_page(admin_mode=True)


@views.route("/account/folders", methods=["GET", "POST"])
@limiter.limit("20 per minute", methods=["POST"], key_func=limiter_key_user_or_ip) if limiter else (lambda f: f)
@login_required
def manage_folder_preferences():
    return _folder_categories_page(admin_mode=False)


@views.route("/admin", methods=["GET", "POST"])
@limiter.limit("8 per minute", methods=["POST"], key_func=limiter_key_user_or_ip) if limiter else (lambda f: f)
@limiter.limit("30 per hour", methods=["POST"], key_func=limiter_key_user_or_ip) if limiter else (lambda f: f)
@limiter.limit("15 per minute", methods=["POST"]) if limiter else (lambda f: f)
@login_required
def admin_console():
    require_admin()
    from core.domains.cards.services.scryfall_cache import (
        cache_exists,
        clear_cache_files,
        load_default_cache as load_cache,
        reload_default_cache as reload_cache,
    )

    inline_refresh = current_app.config.get("SCRYFALL_REFRESH_INLINE", False)
    symbols_context = _load_symbols_context()
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
        """Try inline refresh when queuing fails; return True if inline ran."""
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

    def _bytes_fmt(value: int) -> str:
        n = float(value)
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if n < 1024:
                return f"{n:.1f} {unit}"
            n /= 1024
        return f"{n:.1f} PB"

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

    if request.method == "POST":
        def _resolve_redirect_endpoint() -> str:
            requested = (request.form.get("redirect_to") or "").strip()
            if not requested:
                return "views.admin_console"
            try:
                url_for(requested)
            except Exception:
                return "views.admin_console"
            return requested

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
            return _handle_reset_user_password(redirect_endpoint)

        if action == "delete_user":
            return _handle_delete_user(redirect_endpoint)

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

        def _should_run_inline(error: RuntimeError) -> bool:
            if inline_refresh:
                return True
            message = str(error or "").lower()
            return "rq is not installed" not in message

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
                # Attempt inline fallback if queueing failed
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
            except Exception as exc:
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
                invalid = [
                    folder for folder in deck_folders
                    if folder.deck_tag and not is_valid_deck_tag(folder.deck_tag)
                ]
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
    data_ops = _build_data_ops_context(
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
    user_context = _user_management_context(include_users=False)
    request_counts = _site_request_counts()
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
    )


def _admin_deck_options() -> list[dict[str, str]]:
    rows = (
        db.session.query(
            Folder.id,
            Folder.name,
            Folder.commander_name,
            Folder.owner,
            User.display_name,
            User.username,
            User.email,
        )
        .join(FolderRole, FolderRole.folder_id == Folder.id)
        .outerjoin(User, User.id == Folder.owner_user_id)
        .filter(FolderRole.role.in_(FolderRole.DECK_ROLES))
        .group_by(
            Folder.id,
            Folder.name,
            Folder.commander_name,
            Folder.owner,
            User.display_name,
            User.username,
            User.email,
        )
        .order_by(func.lower(Folder.name), Folder.id.asc())
        .all()
    )
    options: list[dict[str, str]] = []
    for row in rows:
        label = row.name or f"Deck {row.id}"
        if row.commander_name:
            label = f"{label} · {row.commander_name}"
        owner_label = row.owner or row.display_name or row.username or row.email
        if owner_label:
            label = f"{label} · {owner_label}"
        options.append({"id": str(row.id), "label": label})
    return options


def _admin_manual_game_ids() -> list[int]:
    rows = (
        db.session.query(GameSession.id)
        .join(GameDeck, GameDeck.session_id == GameSession.id)
        .filter(GameDeck.folder_id.is_(None))
        .distinct()
        .order_by(GameSession.played_at.desc().nullslast(), GameSession.created_at.desc())
        .all()
    )
    return [row[0] for row in rows if row and row[0]]


def _admin_snapshot_deck(folder: Folder) -> dict[str, object]:
    bracket_level = None
    bracket_label = None
    bracket_score = None
    power_score = None
    cache = CommanderBracketCache.query.filter_by(folder_id=folder.id).first()
    if cache and cache.payload:
        bracket_level = cache.payload.get("level")
        bracket_label = cache.payload.get("label")
        bracket_score = cache.payload.get("score")
        power_score = cache.payload.get("score")
    return {
        "folder_id": folder.id,
        "deck_name": folder.name or f"Deck {folder.id}",
        "commander_name": folder.commander_name,
        "commander_oracle_id": folder.commander_oracle_id,
        "bracket_level": bracket_level,
        "bracket_label": bracket_label,
        "bracket_score": bracket_score,
        "power_score": power_score,
    }


@views.route("/admin/game-deck-mapping", methods=["GET", "POST"])
@login_required
def admin_game_deck_mapping():
    require_admin()
    manual_game_ids = _admin_manual_game_ids()
    if request.method == "POST":
        raw_game_id = request.form.get("game_id") or ""
        try:
            game_id = parse_positive_int(raw_game_id, field="game")
        except ValidationError as exc:
            log_validation_error(exc, context="admin_game_deck_mapping")
            flash("Select a valid game to update.", "warning")
            return redirect(url_for("views.admin_game_deck_mapping"))

        updated = 0
        for key, value in request.form.items():
            if not key.startswith("deck_map_"):
                continue
            try:
                deck_id = parse_positive_int(key.replace("deck_map_", ""), field="deck")
            except ValidationError:
                continue
            folder_id = parse_optional_positive_int(value, field="registered deck")
            if not folder_id:
                continue
            deck = GameDeck.query.filter_by(id=deck_id, session_id=game_id).first()
            if not deck:
                continue
            folder = (
                Folder.query.join(FolderRole, FolderRole.folder_id == Folder.id)
                .filter(Folder.id == folder_id, FolderRole.role.in_(FolderRole.DECK_ROLES))
                .first()
            )
            if not folder:
                continue
            snapshot = _admin_snapshot_deck(folder)
            deck.folder_id = snapshot["folder_id"]
            deck.deck_name = snapshot["deck_name"]
            deck.commander_name = snapshot["commander_name"]
            deck.commander_oracle_id = snapshot["commander_oracle_id"]
            deck.bracket_level = snapshot["bracket_level"]
            deck.bracket_label = snapshot["bracket_label"]
            deck.bracket_score = snapshot["bracket_score"]
            deck.power_score = snapshot["power_score"]
            updated += 1

        if updated:
            try:
                db.session.commit()
                flash(f"Updated {updated} deck mapping{'s' if updated != 1 else ''}.", "success")
            except Exception:
                db.session.rollback()
                flash("Unable to update deck mappings right now.", "danger")
        else:
            flash("No deck mappings were selected.", "info")

        manual_game_ids = _admin_manual_game_ids()
        action = request.form.get("action") or "save"
        if manual_game_ids:
            if game_id in manual_game_ids:
                index = manual_game_ids.index(game_id)
                next_id = manual_game_ids[index + 1] if index + 1 < len(manual_game_ids) else manual_game_ids[0]
            else:
                next_id = manual_game_ids[0]
            if action == "save_next":
                return redirect(url_for("views.admin_game_deck_mapping", game_id=next_id))
            return redirect(url_for("views.admin_game_deck_mapping", game_id=game_id))
        return redirect(url_for("views.admin_console"))

    if not manual_game_ids:
        return render_template("admin/game_deck_mapping.html", game=None, deck_options=[], total_games=0)

    raw_game_id = request.args.get("game_id")
    try:
        selected_id = parse_positive_int(raw_game_id, field="game") if raw_game_id else manual_game_ids[0]
    except ValidationError:
        selected_id = manual_game_ids[0]
    if selected_id not in manual_game_ids:
        selected_id = manual_game_ids[0]

    session = (
        GameSession.query.options(
            selectinload(GameSession.seats)
            .selectinload(GameSeat.assignment)
            .selectinload(GameSeatAssignment.player),
            selectinload(GameSession.seats)
            .selectinload(GameSeat.assignment)
            .selectinload(GameSeatAssignment.deck),
        )
        .filter(GameSession.id == selected_id)
        .first()
    )

    if not session:
        flash("Game session not found.", "warning")
        return redirect(url_for("views.admin_game_deck_mapping"))

    seats_payload = []
    winner_label = None
    seats_sorted = sorted(session.seats or [], key=lambda s: s.seat_number or 0)
    for seat in seats_sorted:
        assignment = seat.assignment
        player = assignment.player if assignment else None
        deck = assignment.deck if assignment else None
        if session.winner_seat_id and seat.id == session.winner_seat_id:
            winner_label = (player.display_name if player else None) or winner_label
        seats_payload.append(
            {
                "seat_number": seat.seat_number,
                "turn_order": seat.turn_order,
                "player_label": (player.display_name if player else None) or "Unknown",
                "deck_name": (deck.deck_name if deck else None) or "Unknown deck",
                "commander_name": deck.commander_name if deck else None,
                "deck_id": deck.id if deck else None,
                "folder_id": deck.folder_id if deck else None,
                "is_manual": bool(deck and not deck.folder_id),
            }
        )

    played_at = session.played_at or session.created_at
    played_label = played_at.strftime("%Y-%m-%d") if played_at else "Unknown"
    notes = session.notes or ""
    total_games = len(manual_game_ids)
    current_index = manual_game_ids.index(selected_id) if selected_id in manual_game_ids else 0
    prev_id = manual_game_ids[current_index - 1] if current_index > 0 else None
    next_id = manual_game_ids[current_index + 1] if current_index + 1 < len(manual_game_ids) else None

    game_payload = {
        "id": session.id,
        "played_at": played_label,
        "notes": notes,
        "winner_label": winner_label,
        "seats": seats_payload,
    }

    return render_template(
        "admin/game_deck_mapping.html",
        game=game_payload,
        deck_options=_admin_deck_options(),
        total_games=total_games,
        current_index=current_index + 1,
        prev_id=prev_id,
        next_id=next_id,
    )


@views.route("/admin/data-operations")
@login_required
def admin_data_operations():
    require_admin()
    symbols_context = _load_symbols_context()
    data_ops = _build_data_ops_context(
        symbols_json_path=symbols_context.get("symbols_json_path"),
        symbols_svg_dir=symbols_context.get("symbols_svg_dir"),
        symbols_enabled=bool(symbols_context.get("symbols_enabled")),
    )

    db_status = {"label": "OK", "tone": "success"}
    try:
        db.session.execute(text("SELECT 1"))
    except Exception:
        db.session.rollback()
        db_status = {"label": "Warning", "tone": "warning"}

    card_count = db.session.query(func.count(Card.id)).scalar() or 0
    deck_count = (
        db.session.query(Folder.id)
        .filter(Folder.role_entries.any(FolderRole.role.in_(FolderRole.DECK_ROLES)))
        .count()
    )
    last_import = data_ops.get("prints", {}).get("mtime") or "N/A"
    tag_count = len(get_all_deck_tags())
    tag_status = "Loaded" if tag_count else "Missing"

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
    maintenance_runs = {
        "scryfall": _job_last_run("scryfall", "default_cards", data_ops.get("prints", {}).get("mtime")),
        "spellbook": _job_last_run("spellbook", "spellbook", data_ops.get("spellbook", {}).get("mtime")),
        "fts": _job_last_run("search_index", "cards"),
    }
    return render_template(
        "admin/data_operations.html",
        system_health={
            "database": db_status,
            "card_count": card_count,
            "deck_count": deck_count,
            "last_import": last_import,
            "tag_status": tag_status,
            "tag_count": tag_count,
        },
        edhrec=edhrec,
        maintenance_runs=maintenance_runs,
    )


@views.route("/admin/card-roles")
@login_required
def admin_card_roles():
    require_admin()
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


_ADMIN_TABLE_PAGE_SIZE = 200


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


@views.route("/admin/oracle-tags", methods=["GET", "POST"])
@login_required
def admin_oracle_tags():
    require_admin()
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
    evergreen_rows = evergreen_query.order_by(OracleEvergreenTag.keyword, OracleEvergreenTag.oracle_id).limit(500).all()

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


@views.route("/admin/oracle-tags/core-roles")
@login_required
def admin_oracle_core_roles():
    require_admin()
    q = (request.args.get("q") or "").strip()
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1
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


@views.route("/admin/oracle-tags/evergreen")
@login_required
def admin_oracle_evergreen_tags():
    require_admin()
    q = (request.args.get("q") or "").strip()
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1
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


@views.route("/admin/oracle-tags/deck-tags")
@login_required
def admin_oracle_deck_tags():
    require_admin()
    q = (request.args.get("q") or "").strip()
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1
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


@views.route("/admin/oracle-tags/deck-tag-core-roles")
@login_required
def admin_deck_tag_core_role_synergies():
    require_admin()
    q = (request.args.get("q") or "").strip()
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1
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


@views.route("/admin/oracle-tags/deck-tag-evergreen")
@login_required
def admin_deck_tag_evergreen_synergies():
    require_admin()
    q = (request.args.get("q") or "").strip()
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1
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


@views.route("/admin/oracle-tags/deck-tag-cards")
@login_required
def admin_deck_tag_card_synergies():
    require_admin()
    q = (request.args.get("q") or "").strip()
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1
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


@views.route("/admin/oracle-roles")
@login_required
def admin_oracle_roles():
    require_admin()
    return redirect(url_for("views.admin_oracle_tags"))


@views.route("/admin/users", methods=["GET", "POST"])
@login_required
def admin_manage_users():
    require_admin()
    if request.method == "POST":
        action = (request.form.get("action") or "").lower()
        if action == "reset_user_password":
            return _handle_reset_user_password("views.admin_manage_users")
        if action == "delete_user":
            return _handle_delete_user("views.admin_manage_users")
        if action == "impersonate_user":
            return _handle_impersonate_user("views.admin_manage_users")
    context = _user_management_context(include_users=True)
    return render_template(
        "admin/user_management.html",
        users=context["users"],
        folder_owner_counts=context["folder_owner_counts"],
        user_stats=context["user_stats"],
        min_password_length=MIN_PASSWORD_LENGTH,
        current_user_id=current_user.id if current_user.is_authenticated else None,
    )


@views.post("/admin/impersonate/stop")
@login_required
def admin_impersonate_stop():
    impersonator_id = session.get("impersonator_id")
    impersonated_id = session.get("impersonated_user_id")
    if not impersonator_id:
        flash("No impersonation session is active.", "info")
        return redirect(url_for("views.dashboard"))
    admin_user = db.session.get(User, impersonator_id)
    if not admin_user or not admin_user.is_admin:
        session.pop("impersonator_id", None)
        session.pop("impersonated_user_id", None)
        flash("Impersonation ended. Admin account unavailable.", "warning")
        return redirect(url_for("views.dashboard"))

    login_user(admin_user, remember=False, fresh=True)
    session["user_is_admin"] = bool(admin_user.is_admin)
    session.pop("impersonator_id", None)
    session.pop("impersonated_user_id", None)
    record_audit_event(
        "admin_impersonate_stop",
        {
            "admin_id": admin_user.id,
            "impersonated_id": impersonated_id,
        },
    )
    flash("Returned to your admin session.", "success")
    return redirect(url_for("views.admin_manage_users"))


@views.route("/admin/requests", methods=["GET", "POST"])
@login_required
def admin_requests():
    require_admin()
    status_choices = [
        (SiteRequest.STATUS_NOT_STARTED, "Not started"),
        (SiteRequest.STATUS_WORKING, "Working"),
        (SiteRequest.STATUS_COMPLETED, "Completed"),
    ]
    status_labels = dict(status_choices)
    if request.method == "POST":
        action = (request.form.get("action") or "").strip().lower()
        if action == "update_request_status":
            raw_id = request.form.get("request_id")
            raw_status = (request.form.get("status") or "").strip().lower()
            try:
                target_id = parse_positive_int(raw_id, field="request id")
            except ValidationError as exc:
                log_validation_error(exc, context="admin_requests")
                flash("Invalid request id.", "warning")
                return redirect(url_for("views.admin_requests"))
            target = db.session.get(SiteRequest, target_id)
            if not target:
                flash("Request not found.", "warning")
                return redirect(url_for("views.admin_requests"))
            if raw_status not in SiteRequest.STATUSES:
                flash("Pick a valid status.", "warning")
                return redirect(url_for("views.admin_requests"))
            if target.status == raw_status:
                flash("No changes made; status was already up to date.", "info")
                return redirect(url_for("views.admin_requests"))
            target.status = raw_status
            _safe_commit()
            record_audit_event(
                "site_request_status_updated",
                {"request_id": target.id, "status": raw_status, "title": target.title},
            )
            flash(
                f'Updated "{target.title}" to {status_labels.get(raw_status, raw_status)}.',
                "success",
            )
            return redirect(url_for("views.admin_requests"))
        return redirect(url_for("views.admin_requests"))
    try:
        page = int(request.args.get("page") or 1)
    except (TypeError, ValueError):
        page = 1
    try:
        per = int(request.args.get("per") or request.args.get("per_page") or 50)
    except (TypeError, ValueError):
        per = 50

    page = max(page, 1)
    per = max(1, min(per, 200))

    base_query = SiteRequest.query.order_by(SiteRequest.created_at.desc())
    total = base_query.order_by(None).count()
    pages = max(1, ceil(total / per)) if per else 1
    page = min(page, pages) if total else 1
    start = (page - 1) * per + 1 if total else 0
    end = min(start + per - 1, total) if total else 0

    items = base_query.limit(per).offset((page - 1) * per).all()

    def _url_with(page_num: int):
        args = request.args.to_dict(flat=False)
        args["page"] = [str(page_num)]
        if "per" not in args and "per_page" not in args:
            args["per"] = [str(per)]
        return url_for("views.admin_requests", **{k: v if len(v) > 1 else v[0] for k, v in args.items()})

    prev_url = _url_with(page - 1) if page > 1 else None
    next_url = _url_with(page + 1) if page < pages else None
    page_urls = [(n, _url_with(n)) for n in range(1, pages + 1)]
    request_counts = _site_request_counts()
    return render_template(
        "admin/requests.html",
        requests=items,
        page=page,
        pages=pages,
        per_page=per,
        prev_url=prev_url,
        next_url=next_url,
        page_urls=page_urls,
        start=start,
        end=end,
        total=total,
        status_choices=status_choices,
        status_labels=status_labels,
        request_counts=request_counts,
    )


@views.route("/admin/job-status")
@login_required
def admin_job_status():
    require_admin()
    scope = (request.args.get("scope") or "").strip()
    dataset = (request.args.get("dataset") or "").strip() or None
    events = latest_job_events(scope, dataset) if scope else []
    return jsonify({"events": events})


@views.route("/ws/imports")
def legacy_imports_ws():
    """Gracefully handle old WebSocket clients by returning a JSON notice."""
    return (
        jsonify(
            {
                "error": "WebSocket streaming has been replaced with HTTP polling. "
                "Please reload the page to use the latest interface."
            }
        ),
        410,
    )


__all__ = [
    "admin_console",
    "admin_folder_categories",
    "admin_game_deck_mapping",
    "admin_manage_users",
    "admin_impersonate_stop",
    "admin_requests",
]
