"""Folder sharing management endpoints."""

from __future__ import annotations

from flask import flash, redirect, render_template, request, session, url_for
from sqlalchemy import func

from extensions import db
from models import Folder, FolderShare, User
from shared.auth import ensure_folder_access
from shared.database import get_or_404
from shared.validation import ValidationError, log_validation_error, parse_positive_int
from core.domains.decks.viewmodels.folder_vm import FolderVM


def folder_sharing(folder_id: int):
    folder = get_or_404(Folder, folder_id)
    ensure_folder_access(folder, write=True)

    if request.method == "POST":
        action = (request.form.get("action") or "").strip().lower()
        if action == "toggle_public":
            target_state = request.form.get("state")
            if target_state is not None:
                folder.is_public = target_state in {"1", "true", "yes", "on"}
            else:
                folder.is_public = not folder.is_public
            db.session.commit()
            flash("Public sharing enabled." if folder.is_public else "Public sharing disabled.", "success")
            return redirect(url_for("views.folder_sharing", folder_id=folder_id))
        if action == "regenerate_token":
            token = folder.ensure_share_token()
            db.session.commit()
            session["share_token_preview"] = token
            flash("Share link updated.", "success")
            return redirect(url_for("views.folder_sharing", folder_id=folder_id))
        if action == "clear_token":
            folder.revoke_share_token()
            db.session.commit()
            flash("Share link disabled.", "info")
            return redirect(url_for("views.folder_sharing", folder_id=folder_id))
        if action == "add_share":
            identifier = (request.form.get("share_identifier") or "").strip().lower()
            if not identifier:
                flash("Provide an email or username.", "warning")
            else:
                target = (
                    User.query.filter(func.lower(User.email) == identifier).first()
                    or User.query.filter(func.lower(User.username) == identifier).first()
                )
                if not target:
                    flash("No user found with that email or username.", "warning")
                elif target.id == folder.owner_user_id:
                    flash("You already own this folder.", "info")
                else:
                    existing = FolderShare.query.filter_by(folder_id=folder.id, shared_user_id=target.id).first()
                    if existing:
                        flash("That user already has access.", "info")
                    else:
                        share = FolderShare(folder_id=folder.id, shared_user_id=target.id)
                        db.session.add(share)
                        db.session.commit()
                        flash(f"Shared with {target.username or target.email}.", "success")
            return redirect(url_for("views.folder_sharing", folder_id=folder_id))
        if action == "remove_share":
            share_id = request.form.get("share_id")
            if share_id:
                try:
                    share_id_val = parse_positive_int(share_id, field="share id")
                except ValidationError as exc:
                    log_validation_error(exc, context="folder_sharing")
                    flash("Invalid share id.", "warning")
                    return redirect(url_for("views.folder_sharing", folder_id=folder_id))
                share = FolderShare.query.filter_by(id=share_id_val, folder_id=folder.id).first()
                if share:
                    db.session.delete(share)
                    db.session.commit()
                    flash("Removed access.", "info")
            return redirect(url_for("views.folder_sharing", folder_id=folder_id))

    share_entries = (
        FolderShare.query.filter(FolderShare.folder_id == folder.id)
        .join(User, User.id == FolderShare.shared_user_id)
        .order_by(func.lower(User.email))
        .all()
    )
    token = session.pop("share_token_preview", None)
    share_link = url_for("views.shared_folder_by_token", share_token=token, _external=True) if token else None
    category_labels = {
        Folder.CATEGORY_DECK: "Deck",
        Folder.CATEGORY_COLLECTION: "Collection",
    }
    folder_vm = FolderVM(
        id=folder.id,
        name=folder.name,
        category=folder.category,
        category_label=category_labels.get(folder.category or Folder.CATEGORY_DECK, "Deck"),
        owner=folder.owner,
        owner_label=folder.owner,
        owner_user_id=folder.owner_user_id,
        is_collection=bool(folder.is_collection),
        is_deck=bool(folder.is_deck),
        is_proxy=bool(getattr(folder, "is_proxy", False)),
        is_public=bool(getattr(folder, "is_public", False)),
        deck_tag=folder.deck_tag,
        deck_tag_label=folder.deck_tag,
        commander_name=folder.commander_name,
        commander_oracle_id=folder.commander_oracle_id,
        commander_slot_count=len(folder.commander_name.split("//")) if folder.commander_name else 0,
    )
    return render_template(
        "decks/folder_sharing.html",
        folder=folder_vm,
        shares=share_entries,
        share_link=share_link,
    )


__all__ = ["folder_sharing"]
