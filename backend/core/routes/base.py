"""Shared blueprint and helper utilities for DragonsVault routes."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from flask import Blueprint, redirect, render_template, request, url_for, flash, jsonify, Response
from flask_login import current_user
try:
    from flask_limiter.util import get_remote_address  # type: ignore
except Exception:  # pragma: no cover
    get_remote_address = None  # type: ignore
from extensions import db
from models import SiteRequest
from core.domains.cards.services.pricing import (
    format_price_text as _format_price_text,
    prices_for_print as _prices_for_print,
    prices_for_print_exact as _prices_for_print_exact,
)
from shared.database import safe_commit as _safe_commit
from shared.mtg import (
    API_PAGE_SIZE,
    DEFAULT_COLLECTION_FOLDERS,
    WUBRG_ORDER,
    _bulk_print_lookup,
    _collector_number_numeric,
    _collection_folder_ids,
    _collection_folder_lower_names,
    _collection_folder_names,
    _collection_metadata,
    _collection_rows_with_fallback,
    _commander_candidates_for_folder,
    _folder_id_name_map,
    _img_url_for_print,
    _lookup_print_data,
    _move_folder_choices,
    _name_sort_expr,
    _normalize_name,
    _small_thumb_for_print,
    _unique_art_variants,
    color_identity_name,
    compute_folder_color_identity,
)
from shared.wishlist import ALLOWED_WISHLIST_STATUSES
from .api import api_bp

views = Blueprint("views", __name__)


def limiter_key_user_or_ip() -> str:
    """Use the authenticated user id when present; otherwise fall back to IP."""
    user_id = getattr(current_user, "id", None) or current_user.get_id()
    if user_id:
        return f"user:{user_id}"
    addr = None
    if get_remote_address:
        try:
            addr = get_remote_address()
        except Exception:
            addr = None
    addr = addr or request.remote_addr or "unknown"
    return f"ip:{addr}"


def _safe_next_url(target: str | None) -> str | None:
    if not target:
        return None
    try:
        parts = urlsplit(target)
    except Exception:
        return None
    if parts.scheme or parts.netloc:
        return None
    if not target.startswith("/") or target.startswith("//"):
        return None
    return target


def _norm_folder(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")


def _available_folder_ids() -> set[int]:
    return _collection_folder_ids()


@views.app_template_filter("ci_name")
def jinja_ci_name(ci):
    return color_identity_name(ci)


__all__ = [
    "ALLOWED_WISHLIST_STATUSES",
    "API_PAGE_SIZE",
    "DEFAULT_COLLECTION_FOLDERS",
    "views",
    "_collection_rows_with_fallback",
    "_available_folder_ids",
    "_bulk_print_lookup",
    "_collection_folder_ids",
    "_collection_folder_lower_names",
    "_collection_folder_names",
    "_collection_metadata",
    "_move_folder_choices",
    "_commander_candidates_for_folder",
    "_format_price_text",
    "_folder_id_name_map",
    "_img_url_for_print",
    "_lookup_print_data",
    "_name_sort_expr",
    "_normalize_name",
    "_collector_number_numeric",
    "_prices_for_print",
    "_prices_for_print_exact",
    "_safe_commit",
    "_small_thumb_for_print",
    "_unique_art_variants",
    "color_identity_name",
    "compute_folder_color_identity",
    "jinja_ci_name",
]
@views.route("/", methods=["GET"])
def landing_page():
    from flask_login import current_user
    from flask import request, url_for, redirect, render_template

    if current_user.is_authenticated:
        dest = _safe_next_url(request.args.get("next")) or url_for("views.dashboard")
        return redirect(dest)
    return render_template("landing.html")


LAST_UPDATED_TEXT = "November 17, 2025"


@views.route("/legal/terms")
def terms_of_service():
    return render_template("legal/terms.html", last_updated=LAST_UPDATED_TEXT)


@views.route("/legal/privacy")
def privacy_policy():
    return render_template("legal/privacy.html", last_updated=LAST_UPDATED_TEXT)


@views.route("/legal/accessibility")
def accessibility_statement():
    return render_template("legal/accessibility.html", last_updated=LAST_UPDATED_TEXT)


@views.route("/legal/disclaimer")
def legal_disclaimer():
    return render_template("legal/disclaimer.html", last_updated=LAST_UPDATED_TEXT)


@views.route("/legal/coppa")
def coppa_notice():
    return render_template("legal/coppa.html", last_updated=LAST_UPDATED_TEXT)


@views.route("/legal/cookie-policy")
def cookie_policy():
    return render_template("legal/cookie.html", last_updated=LAST_UPDATED_TEXT)


@views.route("/legal/terms-of-use")
def terms_of_use():
    return render_template("legal/terms_use.html", last_updated=LAST_UPDATED_TEXT)


@views.route("/legal/shipping-policy")
def shipping_policy():
    return render_template("legal/shipping.html", last_updated=LAST_UPDATED_TEXT)


@views.route("/legal/returns-policy")
def returns_policy():
    return render_template("legal/returns.html", last_updated=LAST_UPDATED_TEXT)


@views.route("/legal/do-not-sell", methods=["GET", "POST"])
def do_not_sell():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        email = (request.form.get("email") or "").strip()
        flash("Your opt-out request has been received. We will process it within 45 days.", "success")
        return redirect(url_for("views.do_not_sell"))
    return render_template("legal/do_not_sell.html", last_updated=LAST_UPDATED_TEXT)


@views.route("/legal/do-not-share", methods=["GET", "POST"])
def do_not_share():
    if request.method == "POST":
        flash("Your request to limit sharing has been received. We will confirm via email.", "success")
        return redirect(url_for("views.do_not_share"))
    return render_template("legal/do_not_share.html", last_updated=LAST_UPDATED_TEXT)


@views.route("/about")
def about_page():
    return render_template("site/about.html")


@views.route("/rules/magic")
def magic_rules():
    import json
    from core.shared.utils.rules_cache import magic_rules_metadata, magic_rules_workbook

    meta = magic_rules_metadata()
    workbook = magic_rules_workbook()
    return render_template(
        "site/magic_rules.html",
        rules_meta=meta,
        rules_workbook_json=json.dumps(workbook),
        rules_workbook=workbook,
    )


@api_bp.get("/rules/search")
def api_rules_search():
    from core.shared.utils.rules_cache import search_magic_rules

    query = (request.args.get("q") or "").strip()
    limit_raw = request.args.get("limit")
    try:
        limit = int(limit_raw) if limit_raw is not None else 20
    except (TypeError, ValueError):
        limit = 20
    limit = max(1, min(limit, 100))
    matches = search_magic_rules(query, limit=limit) if query else []
    return jsonify({"ok": True, "query": query, "matches": matches})


@api_bp.get("/rules/lookup")
def api_rules_lookup():
    from core.shared.utils.rules_cache import lookup_magic_rule

    rule_number = (request.args.get("rule") or "").strip()
    if not rule_number:
        return jsonify({"ok": False, "error": "Missing rule."}), 400
    line = lookup_magic_rule(rule_number)
    if not line:
        return jsonify({"ok": False, "error": "Rule not found."}), 404
    return jsonify({"ok": True, "rule": rule_number, "text": line})


@api_bp.get("/rules/text")
def api_rules_text():
    from core.shared.utils.rules_cache import magic_rules_text

    text = magic_rules_text()
    if not text:
        return jsonify({"ok": False, "error": "Rules text unavailable."}), 404
    return Response(text, mimetype="text/plain")


@api_bp.get("/rules/workbook")
def api_rules_workbook():
    from core.shared.utils.rules_cache import magic_rules_workbook

    workbook = magic_rules_workbook()
    if not workbook:
        return jsonify({"ok": False, "error": "Rules workbook unavailable."}), 404
    return jsonify({"ok": True, "workbook": workbook})


@views.route("/contact", methods=["GET", "POST"])
def contact_page():
    if request.method == "POST":
        form_kind = (request.form.get("form_kind") or "").strip().lower()
        if form_kind == "site_request":
            title = (request.form.get("request_title") or "").strip()
            details = (request.form.get("request_details") or "").strip()
            requester_name = (request.form.get("requester_name") or "").strip() or None
            requester_email = (request.form.get("requester_email") or "").strip() or None
            raw_type = (request.form.get("request_type") or SiteRequest.TYPE_BUG).strip().lower()
            request_type = raw_type if raw_type in SiteRequest.TYPES else SiteRequest.TYPE_BUG
            if not title or not details or not requester_email:
                flash("Please add a title, details, and contact email for your request.", "warning")
            else:
                new_request = SiteRequest(
                    title=title,
                    details=details,
                    request_type=request_type,
                    requester_name=requester_name,
                    requester_email=requester_email,
                    status=SiteRequest.STATUS_NOT_STARTED,
                )
                db.session.add(new_request)
                _safe_commit()
                flash("Thanks! Your request is now queued for the admin team.", "success")
            return redirect(url_for("views.contact_page"))

        name = (request.form.get("name") or "").strip()
        email = (request.form.get("email") or "").strip()
        message = (request.form.get("message") or "").strip()
        if not name or not email or not message:
            flash("Please fill out all fields before submitting.", "warning")
        else:
            flash("Thanks for reaching out! We'll reply soon.", "success")
        return redirect(url_for("views.contact_page"))
    return render_template("site/contact.html")
