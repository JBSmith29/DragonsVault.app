"""Shared folders and friendship management service."""

from __future__ import annotations

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import and_, func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from extensions import db
from models import Folder, FolderShare, FriendCardRequest, User, UserFriend, UserFriendRequest
from core.domains.decks.viewmodels.folder_vm import FolderVM, SharedFolderEntryVM
from shared.validation import ValidationError, log_validation_error, parse_positive_int

_CATEGORY_LABELS = {
    Folder.CATEGORY_DECK: "Deck",
    Folder.CATEGORY_COLLECTION: "Collection",
}


def _shared_folders_redirect():
    return redirect(url_for("views.shared_folders"))


def _user_label(user: User | None) -> str:
    if not user:
        return "Unknown"
    return user.display_name or user.username or user.email or "Unknown"


def _folder_owner_label(folder: Folder) -> str:
    if folder.owner_user:
        return folder.owner_user.display_name or folder.owner_user.username or folder.owner_user.email or folder.owner or "Unknown"
    return folder.owner or "Unknown"


def _folder_vm(folder: Folder) -> FolderVM:
    owner_label = _folder_owner_label(folder)
    return FolderVM(
        id=folder.id,
        name=folder.name,
        category=folder.category,
        category_label=_CATEGORY_LABELS.get(folder.category or Folder.CATEGORY_DECK, "Deck"),
        owner=folder.owner,
        owner_label=owner_label,
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


def _shared_entry(folder: Folder) -> SharedFolderEntryVM:
    owner_label = _folder_owner_label(folder)
    return SharedFolderEntryVM(folder=_folder_vm(folder), owner_label=owner_label)


def _ensure_friendship(user_id: int, friend_id: int) -> None:
    if not UserFriend.query.filter_by(user_id=user_id, friend_user_id=friend_id).first():
        db.session.add(UserFriend(user_id=user_id, friend_user_id=friend_id))
    if not UserFriend.query.filter_by(user_id=friend_id, friend_user_id=user_id).first():
        db.session.add(UserFriend(user_id=friend_id, friend_user_id=user_id))


def shared_folders():
    friend_rows = (
        UserFriend.query.options(selectinload(UserFriend.friend))
        .join(User, User.id == UserFriend.friend_user_id)
        .filter(UserFriend.user_id == current_user.id)
        .order_by(func.lower(User.username))
        .all()
    )
    friends = []
    friend_ids: list[int] = []
    for friendship in friend_rows:
        user = friendship.friend
        if not user:
            continue
        friends.append(
            {
                "user_id": user.id,
                "label": _user_label(user),
                "email": user.email,
            }
        )
        friend_ids.append(user.id)

    incoming_requests = []
    incoming_rows = (
        UserFriendRequest.query.options(selectinload(UserFriendRequest.requester))
        .join(User, User.id == UserFriendRequest.requester_user_id)
        .filter(UserFriendRequest.recipient_user_id == current_user.id)
        .order_by(UserFriendRequest.created_at.desc())
        .all()
    )
    for friend_request in incoming_rows:
        user = friend_request.requester
        if not user:
            continue
        incoming_requests.append(
            {
                "id": friend_request.id,
                "user_id": user.id,
                "label": _user_label(user),
                "email": user.email,
            }
        )

    outgoing_requests = []
    outgoing_rows = (
        UserFriendRequest.query.options(selectinload(UserFriendRequest.recipient))
        .join(User, User.id == UserFriendRequest.recipient_user_id)
        .filter(UserFriendRequest.requester_user_id == current_user.id)
        .order_by(UserFriendRequest.created_at.desc())
        .all()
    )
    for friend_request in outgoing_rows:
        user = friend_request.recipient
        if not user:
            continue
        outgoing_requests.append(
            {
                "id": friend_request.id,
                "user_id": user.id,
                "label": _user_label(user),
                "email": user.email,
            }
        )

    incoming_card_requests = []
    incoming_card_rows = (
        FriendCardRequest.query.options(
            selectinload(FriendCardRequest.requester),
            selectinload(FriendCardRequest.wishlist_item),
        )
        .join(User, User.id == FriendCardRequest.requester_user_id)
        .filter(FriendCardRequest.recipient_user_id == current_user.id)
        .filter(FriendCardRequest.status == "pending")
        .order_by(FriendCardRequest.created_at.desc())
        .all()
    )
    for card_request in incoming_card_rows:
        user = card_request.requester
        item = card_request.wishlist_item
        incoming_card_requests.append(
            {
                "id": card_request.id,
                "label": _user_label(user),
                "email": user.email if user else None,
                "card_name": item.name if item else "Unknown card",
                "qty": card_request.requested_qty,
            }
        )

    outgoing_card_requests = []
    outgoing_card_rows = (
        FriendCardRequest.query.options(
            selectinload(FriendCardRequest.recipient),
            selectinload(FriendCardRequest.wishlist_item),
        )
        .join(User, User.id == FriendCardRequest.recipient_user_id)
        .filter(FriendCardRequest.requester_user_id == current_user.id)
        .order_by(FriendCardRequest.created_at.desc())
        .all()
    )
    for card_request in outgoing_card_rows:
        user = card_request.recipient
        item = card_request.wishlist_item
        outgoing_card_requests.append(
            {
                "id": card_request.id,
                "label": _user_label(user),
                "email": user.email if user else None,
                "card_name": item.name if item else "Unknown card",
                "qty": card_request.requested_qty,
                "status": card_request.status,
            }
        )

    friend_entries: list[SharedFolderEntryVM] = []
    friend_folder_ids: set[int] = set()
    if friend_ids:
        friend_folders = (
            Folder.query.options(selectinload(Folder.owner_user))
            .filter(Folder.owner_user_id.in_(friend_ids))
            .order_by(func.lower(Folder.name))
            .all()
        )
        for folder in friend_folders:
            friend_entries.append(_shared_entry(folder))
            if folder.id is not None:
                friend_folder_ids.add(folder.id)

    shared_with_me: list[SharedFolderEntryVM] = []
    shared_rows = (
        FolderShare.query.options(
            selectinload(FolderShare.folder).selectinload(Folder.owner_user),
        )
        .join(Folder, Folder.id == FolderShare.folder_id)
        .filter(FolderShare.shared_user_id == current_user.id)
        .order_by(func.lower(Folder.name))
        .all()
    )
    for share in shared_rows:
        folder = share.folder
        if not folder or folder.id in friend_folder_ids:
            continue
        shared_with_me.append(_shared_entry(folder))
    shared_ids = {entry.folder.id for entry in shared_with_me}

    my_public = []
    other_public = []
    public_folders = (
        Folder.query.options(selectinload(Folder.owner_user))
        .filter(Folder.is_public.is_(True))
        .order_by(func.lower(Folder.name))
        .all()
    )
    for folder in public_folders:
        folder_vm = _folder_vm(folder)
        if folder.owner_user_id == current_user.id:
            my_public.append(folder_vm)
            continue
        if folder.id in shared_ids or folder.id in friend_folder_ids:
            continue
        other_public.append(folder_vm)

    return render_template(
        "cards/shared_folders.html",
        shared_with_me=shared_with_me,
        friend_folders=friend_entries,
        friends=friends,
        incoming_requests=incoming_requests,
        outgoing_requests=outgoing_requests,
        incoming_card_requests=incoming_card_requests,
        outgoing_card_requests=outgoing_card_requests,
        my_public_folders=my_public,
        other_public_folders=other_public,
    )


def shared_follow():
    action = (request.form.get("action") or "").strip().lower()

    if action == "request":
        identifier = (request.form.get("friend_identifier") or "").strip().lower()
        if not identifier:
            flash("Enter a username or email to send a request.", "warning")
            return _shared_folders_redirect()

        target = (
            User.query.filter(func.lower(User.username) == identifier).first()
            or User.query.filter(func.lower(User.email) == identifier).first()
        )
        if not target:
            flash("No user found with that username or email.", "warning")
            return _shared_folders_redirect()
        if target.id == current_user.id:
            flash("You cannot friend yourself.", "warning")
            return _shared_folders_redirect()
        if UserFriend.query.filter_by(user_id=current_user.id, friend_user_id=target.id).first():
            flash("You are already friends.", "info")
            return _shared_folders_redirect()

        incoming = UserFriendRequest.query.filter_by(
            requester_user_id=target.id,
            recipient_user_id=current_user.id,
        ).first()
        if incoming:
            _ensure_friendship(target.id, current_user.id)
            db.session.delete(incoming)
            try:
                db.session.commit()
                flash(f"Friend request accepted for {target.username or target.email}.", "success")
            except IntegrityError:
                db.session.rollback()
                flash("Unable to accept the friend request right now.", "danger")
            return _shared_folders_redirect()

        existing = UserFriendRequest.query.filter_by(
            requester_user_id=current_user.id,
            recipient_user_id=target.id,
        ).first()
        if existing:
            flash("Friend request already sent.", "info")
            return _shared_folders_redirect()

        db.session.add(UserFriendRequest(requester_user_id=current_user.id, recipient_user_id=target.id))
        try:
            db.session.commit()
            flash(f"Friend request sent to {target.username or target.email}.", "success")
        except IntegrityError:
            db.session.rollback()
            flash("Unable to send that request right now.", "danger")
        return _shared_folders_redirect()

    if action == "accept":
        request_id_raw = request.form.get("request_id")
        try:
            request_id = parse_positive_int(request_id_raw, field="request id")
        except ValidationError as exc:
            log_validation_error(exc, context="shared_friend_accept")
            flash("Invalid request selection.", "warning")
            return _shared_folders_redirect()

        friend_request = UserFriendRequest.query.filter_by(
            id=request_id,
            recipient_user_id=current_user.id,
        ).first()
        if not friend_request:
            flash("Friend request not found.", "warning")
            return _shared_folders_redirect()

        _ensure_friendship(friend_request.requester_user_id, friend_request.recipient_user_id)
        db.session.delete(friend_request)
        try:
            db.session.commit()
            flash("Friend request accepted.", "success")
        except IntegrityError:
            db.session.rollback()
            flash("Unable to accept that request right now.", "danger")
        return _shared_folders_redirect()

    if action == "reject":
        request_id_raw = request.form.get("request_id")
        try:
            request_id = parse_positive_int(request_id_raw, field="request id")
        except ValidationError as exc:
            log_validation_error(exc, context="shared_friend_reject")
            flash("Invalid request selection.", "warning")
            return _shared_folders_redirect()

        friend_request = UserFriendRequest.query.filter_by(
            id=request_id,
            recipient_user_id=current_user.id,
        ).first()
        if friend_request:
            db.session.delete(friend_request)
            db.session.commit()
            flash("Friend request declined.", "info")
        return _shared_folders_redirect()

    if action == "cancel":
        request_id_raw = request.form.get("request_id")
        try:
            request_id = parse_positive_int(request_id_raw, field="request id")
        except ValidationError as exc:
            log_validation_error(exc, context="shared_friend_cancel")
            flash("Invalid request selection.", "warning")
            return _shared_folders_redirect()

        friend_request = UserFriendRequest.query.filter_by(
            id=request_id,
            requester_user_id=current_user.id,
        ).first()
        if friend_request:
            db.session.delete(friend_request)
            db.session.commit()
            flash("Friend request canceled.", "info")
        return _shared_folders_redirect()

    if action == "remove":
        friend_id_raw = request.form.get("friend_user_id")
        try:
            friend_id = parse_positive_int(friend_id_raw, field="friend id")
        except ValidationError as exc:
            log_validation_error(exc, context="shared_friend_remove")
            flash("Invalid friend selection.", "warning")
            return _shared_folders_redirect()

        friendships = UserFriend.query.filter(
            or_(
                and_(UserFriend.user_id == current_user.id, UserFriend.friend_user_id == friend_id),
                and_(UserFriend.user_id == friend_id, UserFriend.friend_user_id == current_user.id),
            )
        ).all()
        if friendships:
            for friendship in friendships:
                db.session.delete(friendship)
            db.session.commit()
            flash("Friend removed.", "info")
        return _shared_folders_redirect()

    flash("Unknown friend action.", "warning")
    return _shared_folders_redirect()


__all__ = [
    "shared_folders",
    "shared_follow",
]
