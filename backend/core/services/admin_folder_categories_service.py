"""Admin and account folder category management."""

from __future__ import annotations

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import func

from extensions import db
from models import Folder, FolderShare, User
from core.domains.users.services.audit import record_audit_event
from shared.auth import require_admin
from shared.validation import ValidationError, log_validation_error, parse_positive_int_list
from core.services.admin_user_management_service import purge_folder
from core.routes.base import DEFAULT_COLLECTION_FOLDERS, _safe_commit

__all__ = ["render_folder_categories_page"]


def render_folder_categories_page(admin_mode: bool):
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
                    counts = purge_folder(folder_to_delete)
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
            sleeve_color_apply = request.form.get("bulk_sleeve_color_apply") == "1"
            sleeve_color_value = (request.form.get("bulk_sleeve_color_value") or "").strip()

            allowed_categories = {Folder.CATEGORY_DECK, Folder.CATEGORY_COLLECTION}
            proxy_flag = None
            if proxy_value_raw in {"proxy", "on", "1", "true"}:
                proxy_flag = True
            elif proxy_value_raw in {"owned", "off", "0", "false"}:
                proxy_flag = False

            updated = {"category": 0, "owner": 0, "proxy": 0, "sleeve_color": 0}
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
                if sleeve_color_apply:
                    new_sleeve_color = sleeve_color_value or None
                    if (folder.sleeve_color or None) != new_sleeve_color:
                        folder.sleeve_color = new_sleeve_color
                        updated["sleeve_color"] += 1

            _safe_commit()
            field_labels = {"category": "category", "owner": "owner", "proxy": "proxy", "sleeve_color": "sleeve color"}
            changed_fields = [field_labels[name] for name, count in updated.items() if count]
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
        updated_sleeve_colors = 0
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
            sleeve_color_value = (request.form.get(f"sleeve-color-{folder.id}") or "").strip() or None
            if (folder.sleeve_color or None) != sleeve_color_value:
                folder.sleeve_color = sleeve_color_value
                updated_sleeve_colors += 1

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
        if updated_sleeve_colors:
            changes.append(f"{updated_sleeve_colors} sleeve color{'s' if updated_sleeve_colors != 1 else ''}")
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
                "sleeve_color": updated_sleeve_colors,
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
