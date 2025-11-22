"""Administrative routes for managing folders and Scryfall bulk data."""

from __future__ import annotations

import gzip
import json
import os
import time
import uuid
from pathlib import Path
from typing import List, Optional, Set

from flask import current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func

from extensions import db, limiter
from models import Folder, FolderShare, SiteRequest, User, AuditLog
from services import scryfall_cache as sc
from services.scryfall_cache import ensure_cache_loaded
from services.jobs import (
    enqueue_scryfall_refresh,
    run_scryfall_refresh_inline,
    enqueue_edhrec_refresh,
    run_edhrec_refresh_inline,
    enqueue_spellbook_refresh,
    run_spellbook_refresh_inline,
)
from services.live_updates import emit_job_event, latest_job_events
from services.edhrec import (
    cache_root,
    commander_cache_snapshot,
    theme_cache_snapshot,
    normalize_card_key,
)
from services.spellbook_sync import EARLY_MANA_VALUE_THRESHOLD, LATE_MANA_VALUE_THRESHOLD
from services.authz import require_admin
from services.audit import record_audit_event
from .auth import MIN_PASSWORD_LENGTH
from .base import DEFAULT_COLLECTION_FOLDERS, _safe_commit, views


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
            delete_ids: set[int] = set()
            for raw_id in delete_ids_raw:
                try:
                    delete_ids.add(int(raw_id))
                except (TypeError, ValueError):
                    flash("Invalid folder id.", "danger")
                    return redirect(url_for(target_endpoint))

            if delete_ids:
                to_delete = [f for f in folders if f.id in delete_ids]
                if not to_delete:
                    flash("Folder not found.", "warning")
                    return redirect(url_for(target_endpoint))

                deleted_info = []
                for folder_to_delete in to_delete:
                    folder_name = folder_to_delete.name or "Folder"
                    deleted_info.append((folder_to_delete.id, folder_name))
                    db.session.delete(folder_to_delete)
                _safe_commit()
                record_audit_event(
                    "folder_deleted",
                    {"folder_ids": [fid for fid, _ in deleted_info], "names": [name for _, name in deleted_info]},
                )
                if len(deleted_info) == 1:
                    flash(f'Deleted folder "{deleted_info[0][1]}" and all associated cards.', "success")
                else:
                    flash(f"Deleted {len(deleted_info)} folders and all associated cards.", "success")
                return redirect(url_for(target_endpoint))

        if bulk_action == "bulk_edit":
            raw_selected = request.form.get("bulk_folder_ids") or ""
            selected_ids: set[int] = set()
            for part in raw_selected.split(","):
                part = part.strip()
                if not part:
                    continue
                try:
                    selected_ids.add(int(part))
                except (TypeError, ValueError):
                    continue

            if not selected_ids:
                flash("Select at least one folder to bulk edit.", "warning")
                return redirect(url_for(target_endpoint))

            category_input = (request.form.get("bulk_category") or "").strip()
            owner_apply = request.form.get("bulk_owner_apply") == "1"
            owner_value = (request.form.get("bulk_owner_value") or "").strip()
            proxy_value_raw = (request.form.get("bulk_proxy") or "").strip().lower()
            notes_apply = request.form.get("bulk_notes_apply") == "1"
            notes_value = (request.form.get("bulk_notes_value") or "").strip()

            allowed_categories = {Folder.CATEGORY_DECK, Folder.CATEGORY_COLLECTION, Folder.CATEGORY_BUILD}
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
            selected_ids: set[int] = set()
            for part in raw_selected.split(","):
                part = part.strip()
                if not part:
                    continue
                try:
                    selected_ids.add(int(part))
                except (TypeError, ValueError):
                    continue

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
        allowed_categories = {Folder.CATEGORY_DECK, Folder.CATEGORY_COLLECTION, Folder.CATEGORY_BUILD}
        for folder in folders:
            submitted = request.form.get(f"category-{folder.id}", Folder.CATEGORY_DECK)
            if submitted not in allowed_categories:
                submitted = Folder.CATEGORY_DECK
            if folder.category != submitted:
                folder.category = submitted
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
                "notes": updated_notes,
            },
        )
        return redirect(url_for(target_endpoint))

    build_count = sum(1 for folder in folders if getattr(folder, "is_build", False))
    deck_count = sum(1 for folder in folders if not folder.is_collection and not getattr(folder, "is_build", False))
    collection_count = sum(1 for folder in folders if folder.is_collection)
    proxy_count = sum(1 for folder in folders if getattr(folder, "is_proxy", False))

    return render_template(
        "admin/admin_folder_categories.html",
        folders=folders,
        users=users,
        deck_category=Folder.CATEGORY_DECK,
        collection_category=Folder.CATEGORY_COLLECTION,
        build_category=Folder.CATEGORY_BUILD,
        default_collection=sorted(DEFAULT_COLLECTION_FOLDERS),
        deck_count=deck_count,
        build_count=build_count,
        collection_count=collection_count,
        proxy_count=proxy_count,
        show_owner_controls=admin_mode,
        show_owner_field=True,
        allow_delete=True,
        allow_share_controls=not admin_mode,
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


def _handle_reset_user_password(target_endpoint: str):
    """Shared handler for admin password resets."""
    redirect_target = url_for(target_endpoint)
    target_user_raw = request.form.get("target_user_id")
    new_password = (request.form.get("new_password") or "").strip()
    if not target_user_raw:
        flash("Select a user to reset.", "warning")
        return redirect(redirect_target)
    try:
        target_user_id = int(target_user_raw)
    except (TypeError, ValueError):
        flash("Invalid user id.", "danger")
        return redirect(redirect_target)
    if len(new_password) < MIN_PASSWORD_LENGTH:
        flash(f"Password must be at least {MIN_PASSWORD_LENGTH} characters long.", "warning")
        return redirect(redirect_target)
    target_user = User.query.get(target_user_id)
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


def _handle_delete_user(target_endpoint: str):
    """Shared handler for admin account deletions."""
    redirect_target = url_for(target_endpoint)
    target_user_raw = request.form.get("target_user_id")
    if not target_user_raw:
        flash("Select a user to delete.", "warning")
        return redirect(redirect_target)
    try:
        target_user_id = int(target_user_raw)
    except (TypeError, ValueError):
        flash("Invalid user id.", "danger")
        return redirect(redirect_target)
    if current_user.is_authenticated and target_user_id == current_user.id:
        flash("You cannot delete the account that is currently signed in.", "warning")
        return redirect(redirect_target)
    target_user = User.query.get(target_user_id)
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
    folders_unlinked = (
        Folder.query.filter(Folder.owner_user_id == target_user.id)
        .update({Folder.owner_user_id: None}, synchronize_session=False)
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
            "folders_unlinked": folders_unlinked,
            "audit_entries_detached": audit_entries_detached,
        },
    )
    flash(f"Deleted user {removed_email}.", "success")
    return redirect(redirect_target)


@views.route("/admin/folder-categories", methods=["GET", "POST"])
@login_required
def admin_folder_categories():
    return _folder_categories_page(admin_mode=True)


@views.route("/account/folders", methods=["GET", "POST"])
@login_required
def manage_folder_preferences():
    return _folder_categories_page(admin_mode=False)


@views.route("/admin", methods=["GET", "POST"])
@limiter.limit("15 per minute", methods=["POST"]) if limiter else (lambda f: f)
@login_required
def admin_console():
    require_admin()
    from services.scryfall_cache import (
        DEFAULT_PATH,
        cache_exists,
        cache_stats,
        clear_cache_files,
        is_stale,
        load_default_cache as load_cache,
        reload_default_cache as reload_cache,
        rulings_bulk_path,
    )

    inline_refresh = current_app.config.get("SCRYFALL_REFRESH_INLINE", False)

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

    symbols_json_path: Optional[Path] = None
    symbols_svg_dir: Optional[Path] = None
    try:
        from services.symbols_cache import ensure_symbols_cache, SYMBOLS_JSON, SYMBOLS_DIR

        symbols_enabled = True
        symbols_json_path = SYMBOLS_JSON
        symbols_svg_dir = SYMBOLS_DIR
    except Exception:
        ensure_symbols_cache = None
        symbols_enabled = False
        symbols_json_path = None
        symbols_svg_dir = None

    data_dir = Path(os.getenv("SCRYFALL_DATA_DIR", "data"))
    rulings_path = Path(rulings_bulk_path())
    prints_path = Path(DEFAULT_PATH)
    spellbook_path = data_dir / "spellbook_combos.json"

    def _bytes_fmt(value: int) -> str:
        n = float(value)
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if n < 1024:
                return f"{n:.1f} {unit}"
            n /= 1024
        return f"{n:.1f} PB"

    if request.method == "POST":
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
            return redirect(url_for("views.admin_console"))

        if action == "create_user":
            email = (request.form.get("user_email") or "").strip().lower()
            username = (request.form.get("user_username") or "").strip().lower()
            password = (request.form.get("user_password") or "").strip()
            is_admin_flag = request.form.get("user_is_admin") in {"1", "true", "on", "yes"}
            if not email or not password or not username:
                flash("Email, username, and password are required to create a user.", "warning")
                return redirect(url_for("views.admin_console"))
            if User.query.filter(func.lower(User.email) == email).first():
                flash(f"A user with email {email} already exists.", "danger")
                return redirect(url_for("views.admin_console"))
            if User.query.filter(func.lower(User.username) == username).first():
                flash(f"A user with username {username} already exists.", "danger")
                return redirect(url_for("views.admin_console"))
            new_user = User(email=email, username=username, display_name=None, is_admin=is_admin_flag)
            new_user.set_password(password)
            db.session.add(new_user)
            db.session.commit()
            record_audit_event("admin_create_user", {"email": email, "username": username, "is_admin": is_admin_flag})
            flash(f"Created user {email} / {username}.", "success")
            return redirect(url_for("views.admin_console"))

        if action == "reset_user_password":
            return _handle_reset_user_password("views.admin_console")

        if action == "delete_user":
            return _handle_delete_user("views.admin_console")

        if action == "refresh_symbols":
            if not symbols_enabled:
                flash("Symbols module is not available.", "warning")
                return redirect(url_for("views.admin_console"))
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
            return redirect(url_for("views.admin_console"))

        def _should_run_inline(error: RuntimeError) -> bool:
            if inline_refresh:
                return True
            message = str(error or "").lower()
            return "rq is not installed" not in message

        if action == "refresh_rulings":
            if inline_refresh:
                _inline_scryfall_refresh("rulings", "Rulings", action)
                return redirect(url_for("views.admin_console"))
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
            return redirect(url_for("views.admin_console"))

        if action == "refresh_default_cards":
            if inline_refresh:
                _inline_scryfall_refresh("default_cards", "Default cards", action, force_download=True)
                return redirect(url_for("views.admin_console"))
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
            return redirect(url_for("views.admin_console"))

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
            return redirect(url_for("views.admin_console"))

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
            return redirect(url_for("views.admin_console"))

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
                if inline_refresh:
                    info = run_edhrec_refresh_inline(force_refresh=True)
                    if info.get("status") == "success":
                        detail_success.append(f"EDHREC cache update: {info.get('message')}")
                    else:
                        detail_warnings.append(f"EDHREC cache update: {info.get('message')}")
                else:
                    job_id = enqueue_edhrec_refresh(force_refresh=True)
                    summary_success.append(f"EDHREC cache (job {job_id})")
                record_audit_event("admin_action", {"action": action})
            except Exception as exc:
                current_app.logger.exception("Refresh all: edhrec refresh failed")
                summary_warnings.append(f"EDHREC refresh failed to queue: {exc}")

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
            return redirect(url_for("views.admin_console"))

        if action == "refresh_edhrec":
            force_refresh = bool(request.form.get("force_refresh"))
            try:
                if inline_refresh:
                    info = run_edhrec_refresh_inline(force_refresh=force_refresh)
                    flash(f"EDHREC cache update: {info.get('message')}", info.get("status", "info"))
                    record_audit_event("admin_action", {"action": action, "force": force_refresh, "mode": "inline"})
                else:
                    job_id = enqueue_edhrec_refresh(force_refresh=force_refresh)
                    flash(f"Queued EDHREC refresh (job {job_id}).", "info")
                    record_audit_event("admin_action", {"action": action, "force": force_refresh, "job_id": job_id})
            except Exception as exc:
                current_app.logger.exception("EDHREC refresh failed")
                flash(f"Failed to refresh EDHREC cache: {exc}", "danger")
            return redirect(url_for("views.admin_console"))

    prints_exists = prints_path.exists() and prints_path.stat().st_size > 0
    prints_size = prints_path.stat().st_size if prints_exists else 0
    prints_mtime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(prints_path.stat().st_mtime)) if prints_exists else None
    prints_stale = sc.is_stale() if hasattr(sc, "is_stale") else False

    rulings_exists = rulings_path.exists()
    rulings_size = rulings_path.stat().st_size if rulings_exists else 0
    rulings_mtime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(rulings_path.stat().st_mtime)) if rulings_exists else None

    spellbook_exists = spellbook_path.exists()
    spellbook_size = spellbook_path.stat().st_size if spellbook_exists else 0
    spellbook_mtime = (
        time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(spellbook_path.stat().st_mtime))
        if spellbook_exists
        else None
    )
    spellbook_counts = {"early": 0, "late": 0, "total": 0}
    spellbook_categories: dict[str, int] = {}
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
            symbols_stats["mtime"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(symbols_json_path.stat().st_mtime))
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

    all_folders = Folder.query.order_by(func.lower(Folder.name)).all()
    deck_folders = [folder for folder in all_folders if not folder.is_collection]
    folder_counts = {
        "deck": len(deck_folders),
        "collection": len(all_folders) - len(deck_folders),
        "total": len(all_folders),
    }
    commander_named_count = sum(1 for folder in deck_folders if (folder.commander_name or "").strip())
    tagged_count = sum(1 for folder in deck_folders if (folder.deck_tag or "").strip())
    edhrec_stats = {
        "root": str(cache_root()),
        "commanders": commander_cache_snapshot(),
        "themes": theme_cache_snapshot(),
        "deck_totals": {
            "total": len(deck_folders),
            "with_commander": commander_named_count,
            "with_tag": tagged_count,
        },
    }
    user_context = _user_management_context(include_users=False)
    request_counts = _site_request_counts()
    return render_template(
        "admin/admin.html",
        prints={
            "exists": prints_exists,
            "path": str(prints_path),
            "size": prints_size,
            "mtime": prints_mtime,
            "stale": prints_stale,
        },
        rulings={
            "exists": rulings_exists,
            "path": str(rulings_path),
            "size": rulings_size,
            "mtime": rulings_mtime,
        },
        spellbook={
            "exists": spellbook_exists,
            "path": str(spellbook_path),
            "size": spellbook_size,
            "mtime": spellbook_mtime,
            "counts": spellbook_counts,
            "categories": spellbook_categories,
            "early_threshold": EARLY_MANA_VALUE_THRESHOLD,
            "late_threshold": LATE_MANA_VALUE_THRESHOLD,
        },
        symbols=symbols_stats,
        stats=stats,
        symbols_enabled=symbols_enabled,
        folder_counts=folder_counts,
        edhrec=edhrec_stats,
        user_stats=user_context["user_stats"],
        request_counts=request_counts,
    )


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
    context = _user_management_context(include_users=True)
    return render_template(
        "admin/user_management.html",
        users=context["users"],
        folder_owner_counts=context["folder_owner_counts"],
        user_stats=context["user_stats"],
        min_password_length=MIN_PASSWORD_LENGTH,
        current_user_id=current_user.id if current_user.is_authenticated else None,
    )


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
                target_id = int(raw_id)
            except (TypeError, ValueError):
                flash("Invalid request id.", "warning")
                return redirect(url_for("views.admin_requests"))
            target = SiteRequest.query.get(target_id)
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
    items = SiteRequest.query.order_by(SiteRequest.created_at.desc()).all()
    request_counts = _site_request_counts()
    return render_template(
        "admin/requests.html",
        requests=items,
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


__all__ = ["admin_console", "admin_folder_categories", "admin_manage_users", "admin_requests"]



