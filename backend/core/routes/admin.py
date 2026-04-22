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
from core.services.admin_game_mapping_service import render_admin_game_deck_mapping
from core.services.admin_console_service import render_admin_console
from core.services.admin_folder_categories_service import render_folder_categories_page
from core.services.admin_requests_service import admin_job_status_response, legacy_imports_notice, render_admin_requests
from core.services.admin_system_service import (
    build_data_ops_context,
    load_symbols_context,
    render_admin_data_operations,
    site_request_counts,
)
from core.services.admin_oracle_tags_service import (
    render_admin_card_roles,
    render_admin_deck_tag_card_synergies,
    render_admin_deck_tag_core_role_synergies,
    render_admin_deck_tag_evergreen_synergies,
    render_admin_oracle_core_roles,
    render_admin_oracle_deck_tags,
    render_admin_oracle_evergreen_tags,
    render_admin_oracle_tags,
)
from core.services.admin_user_management_service import (
    handle_delete_user,
    handle_reset_user_password,
    purge_folder,
    render_admin_manage_users,
    stop_admin_impersonation,
    user_management_context,
)
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
    return render_folder_categories_page(admin_mode=admin_mode)


@views.route("/admin/folder-categories", methods=["GET", "POST"])
@limiter.limit("20 per minute", methods=["POST"], key_func=limiter_key_user_or_ip) if limiter else (lambda f: f)
@login_required
def admin_folder_categories():
    require_admin()
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
    return render_admin_console()


@views.route("/admin/game-deck-mapping", methods=["GET", "POST"])
@login_required
def admin_game_deck_mapping():
    require_admin()
    return render_admin_game_deck_mapping()


@views.route("/admin/data-operations")
@login_required
def admin_data_operations():
    require_admin()
    return render_admin_data_operations()


@views.route("/admin/card-roles")
@login_required
def admin_card_roles():
    require_admin()
    return render_admin_card_roles()


@views.route("/admin/oracle-tags", methods=["GET", "POST"])
@login_required
def admin_oracle_tags():
    require_admin()
    return render_admin_oracle_tags()


@views.route("/admin/oracle-tags/core-roles")
@login_required
def admin_oracle_core_roles():
    require_admin()
    return render_admin_oracle_core_roles()


@views.route("/admin/oracle-tags/evergreen")
@login_required
def admin_oracle_evergreen_tags():
    require_admin()
    return render_admin_oracle_evergreen_tags()


@views.route("/admin/oracle-tags/deck-tags")
@login_required
def admin_oracle_deck_tags():
    require_admin()
    return render_admin_oracle_deck_tags()


@views.route("/admin/oracle-tags/deck-tag-core-roles")
@login_required
def admin_deck_tag_core_role_synergies():
    require_admin()
    return render_admin_deck_tag_core_role_synergies()


@views.route("/admin/oracle-tags/deck-tag-evergreen")
@login_required
def admin_deck_tag_evergreen_synergies():
    require_admin()
    return render_admin_deck_tag_evergreen_synergies()


@views.route("/admin/oracle-tags/deck-tag-cards")
@login_required
def admin_deck_tag_card_synergies():
    require_admin()
    return render_admin_deck_tag_card_synergies()


@views.route("/admin/oracle-roles")
@login_required
def admin_oracle_roles():
    require_admin()
    return redirect(url_for("views.admin_oracle_tags"))


@views.route("/admin/users", methods=["GET", "POST"])
@login_required
def admin_manage_users():
    require_admin()
    return render_admin_manage_users()


@views.post("/admin/impersonate/stop")
@login_required
def admin_impersonate_stop():
    # Allow the currently-impersonated user to stop the session (so they can
    # exit back to the admin). The service validates the stored impersonator
    # is a real admin before restoring them.
    return stop_admin_impersonation()


@views.route("/admin/requests", methods=["GET", "POST"])
@login_required
def admin_requests():
    require_admin()
    return render_admin_requests()


@views.route("/admin/job-status")
@login_required
def admin_job_status():
    require_admin()
    return admin_job_status_response()


@views.route("/ws/imports")
def legacy_imports_ws():
    return legacy_imports_notice()


__all__ = [
    "admin_console",
    "admin_folder_categories",
    "admin_game_deck_mapping",
    "admin_manage_users",
    "admin_impersonate_stop",
    "admin_requests",
]
