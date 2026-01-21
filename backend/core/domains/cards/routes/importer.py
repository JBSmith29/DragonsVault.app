"""Collection import/export routes."""

from __future__ import annotations

from flask import render_template, session
from flask_login import login_required

from extensions import limiter
from core.domains.cards.services import import_service
from core.routes.base import limiter_key_user_or_ip, views


@views.route("/import", methods=["GET", "POST"])
@limiter.limit("5 per minute", methods=["POST"]) if limiter else (lambda f: f)
@limiter.limit("20 per hour", methods=["POST"], key_func=limiter_key_user_or_ip) if limiter else (lambda f: f)
@login_required
def import_csv():
    """Upload route that powers CSV/XLS collection imports and dry-run previews."""
    result = import_service.handle_import_csv(session_obj=session)
    if result.response is not None:
        return result.response
    return render_template(result.template or "cards/import.html", **(result.context or {}))


@views.route("/import/status", methods=["GET"])
@login_required
def import_status():
    return import_service.import_status()


@views.route("/import/template.csv", methods=["GET"])
@login_required
def import_template_csv():
    """Serve a CSV template as a forced download."""
    return import_service.import_template_csv()


@views.route("/cards/export")
def export_cards():
    """Export the current card selection as CSV."""
    return import_service.export_cards()


@views.route("/import/manual", methods=["GET", "POST"])
@login_required
def manual_import():
    """Manual import wizard for pasted decklists."""
    result = import_service.manual_import(session_obj=session)
    if result.response is not None:
        return result.response
    return render_template(result.template or "cards/manual_import.html", **(result.context or {}))


@views.post("/api/folders/categories")
@login_required
def api_update_folder_categories():
    """Update folder categories for the current user (used post-import)."""
    return import_service.api_update_folder_categories()


__all__ = [
    "export_cards",
    "import_csv",
    "import_status",
    "import_template_csv",
    "api_update_folder_categories",
]
