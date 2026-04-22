"""Folder detail and shared-folder view service."""

from __future__ import annotations

from flask import abort, render_template, request
from flask_login import current_user

from extensions import db
from models import Folder
from core.domains.decks.services.deck_service import deck_land_mana_sources, deck_mana_pip_dist
from core.domains.decks.services.folder_detail_analysis_service import analyze_folder_rows
from core.domains.decks.services.folder_detail_page_context_service import build_folder_detail_page_context
from shared.auth import ensure_folder_access
from shared.database import get_or_404
from shared.mtg import _commander_candidates_for_folder, _folder_id_name_map


def _folder_detail_impl(folder_id: int, *, allow_shared: bool = False, share_token: str | None = None):
    folder = get_or_404(Folder, folder_id)
    ensure_folder_access(folder, write=False, allow_shared=allow_shared, share_token=share_token)

    commander_candidates = _commander_candidates_for_folder(folder_id)
    owner_name_options = sorted(
        {
            (name or "").strip()
            for (name,) in db.session.query(Folder.owner).filter(Folder.owner.isnot(None)).all()
            if (name or "").strip()
        },
        key=lambda value: value.lower(),
    )

    current_owner_default = None
    try:
        if current_user.is_authenticated:
            current_owner_default = (current_user.username or current_user.email or "").strip() or None
    except Exception:
        current_owner_default = None
    if current_owner_default and current_owner_default not in owner_name_options:
        owner_name_options = [current_owner_default] + owner_name_options

    sort = (request.args.get("sort") or "").strip().lower()
    direction = (request.args.get("dir") or "asc").strip().lower()
    reverse = direction == "desc"

    folder_names = _folder_id_name_map()
    analysis = analyze_folder_rows(folder_id)
    mana_pip_dist = deck_mana_pip_dist(folder_id, mode="detail")
    land_mana_sources = deck_land_mana_sources(folder_id)
    page_context = build_folder_detail_page_context(
        folder,
        folder_id=folder_id,
        sort=sort,
        reverse=reverse,
        bracket_cards=analysis.bracket_cards,
    )

    return render_template(
        "decks/folder_detail.html",
        commander_candidates=commander_candidates,
        total_rows=analysis.total_rows,
        total_qty=analysis.total_qty,
        type_breakdown=analysis.type_breakdown,
        mana_pip_dist=mana_pip_dist,
        land_mana_sources=land_mana_sources,
        deck_tokens=analysis.deck_tokens,
        owner_name_options=owner_name_options,
        folder_names=folder_names,
        sort=sort,
        direction=direction,
        reverse=reverse,
        **page_context,
    )


def folder_detail(folder_id: int):
    return _folder_detail_impl(folder_id)


def shared_folder_detail(folder_id: int):
    return _folder_detail_impl(folder_id, allow_shared=True)


def shared_folder_by_token(share_token: str):
    token_hash = Folder._hash_share_token(share_token)
    folder = Folder.query.filter(Folder.share_token_hash == token_hash).first()
    if not folder:
        folder = getattr(Folder, "share_token", None) and Folder.query.filter_by(share_token=share_token).first()
    if not folder:
        abort(404)
    return _folder_detail_impl(folder.id, allow_shared=True, share_token=share_token)


__all__ = [
    "folder_detail",
    "shared_folder_detail",
    "shared_folder_by_token",
]
