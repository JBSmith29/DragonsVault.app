"""Folder (deck) detail views and commander management endpoints."""

from __future__ import annotations

from flask_login import login_required

from extensions import limiter
from services import folder_service
from .base import limiter_key_user_or_ip, views


@views.get("/commander-brackets")
@limiter.limit("30 per minute", key_func=limiter_key_user_or_ip) if limiter else (lambda f: f)
def commander_brackets_info():
    return folder_service.commander_brackets_info()


@views.get("/commander-spellbook-combos")
def commander_spellbook_combos():
    return folder_service.commander_spellbook_combos()


@views.post("/folders/<int:folder_id>/tag/set")
@login_required
def set_folder_tag(folder_id: int):
    return folder_service.set_folder_tag(folder_id)


@views.post("/folders/<int:folder_id>/tag/clear")
@login_required
def clear_folder_tag(folder_id: int):
    return folder_service.clear_folder_tag(folder_id)


@views.post("/folders/<int:folder_id>/owner/set")
@login_required
def set_folder_owner(folder_id: int):
    return folder_service.set_folder_owner(folder_id)


@views.post("/folders/<int:folder_id>/proxy/set")
@login_required
def set_folder_proxy(folder_id: int):
    return folder_service.set_folder_proxy(folder_id)


@views.post("/folders/<int:folder_id>/rename")
@login_required
def rename_proxy_deck(folder_id: int):
    return folder_service.rename_proxy_deck(folder_id)


@views.get("/folders/<int:folder_id>/cards.json")
@login_required
def folder_cards_json(folder_id):
    return folder_service.folder_cards_json(folder_id)


@views.get("/api/folders/<int:folder_id>/commander-candidates")
@login_required
def api_folder_commander_candidates(folder_id: int):
    return folder_service.api_folder_commander_candidates(folder_id)


@views.post("/folders/<int:folder_id>/set_commander")
@login_required
def set_folder_commander(folder_id: int):
    return folder_service.set_folder_commander(folder_id)


@views.post("/folders/<int:folder_id>/clear_commander")
@login_required
def clear_folder_commander(folder_id: int):
    return folder_service.clear_folder_commander(folder_id)


@views.post("/folders/<int:folder_id>/commander/set")
@login_required
def set_commander(folder_id):
    return folder_service.set_commander(folder_id)


@views.post("/folders/<int:folder_id>/commander/clear")
@login_required
def clear_commander(folder_id):
    return folder_service.clear_commander(folder_id)


@views.route("/folders/<int:folder_id>/sharing", methods=["GET", "POST"])
@limiter.limit("30 per minute", methods=["POST"], key_func=limiter_key_user_or_ip) if limiter else (lambda f: f)
@login_required
def folder_sharing(folder_id: int):
    return folder_service.folder_sharing(folder_id)


@views.route("/folders/<int:folder_id>")
@login_required
def folder_detail(folder_id):
    return folder_service.folder_detail(folder_id)


@views.get("/api/folder/<int:folder_id>/counts")
@login_required
def folder_counts(folder_id: int):
    return folder_service.folder_counts(folder_id)


@views.route("/shared/folder/<int:folder_id>")
@login_required
def shared_folder_detail(folder_id):
    return folder_service.shared_folder_detail(folder_id)


@views.route("/shared/<string:share_token>")
@login_required
def shared_folder_by_token(share_token: str):
    return folder_service.shared_folder_by_token(share_token)


__all__ = [
    "api_folder_commander_candidates",
    "clear_commander",
    "clear_folder_commander",
    "clear_folder_tag",
    "commander_brackets_info",
    "commander_spellbook_combos",
    "folder_cards_json",
    "folder_detail",
    "folder_sharing",
    "rename_proxy_deck",
    "set_commander",
    "set_folder_commander",
    "set_folder_owner",
    "set_folder_proxy",
    "set_folder_tag",
]
