"""Admin request queue and background-job status helpers."""

from __future__ import annotations

from math import ceil

from flask import flash, jsonify, redirect, render_template, request, url_for

from extensions import db
from models import SiteRequest
from core.domains.users.services.audit import record_audit_event
from core.services.admin_system_service import site_request_counts
from shared.events.live_updates import latest_job_events
from shared.validation import ValidationError, log_validation_error, parse_positive_int

__all__ = [
    "admin_job_status_response",
    "legacy_imports_notice",
    "render_admin_requests",
]


def render_admin_requests():
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
            db.session.commit()
            record_audit_event(
                "site_request_status_updated",
                {"request_id": target.id, "status": raw_status, "title": target.title},
            )
            flash(f'Updated "{target.title}" to {status_labels.get(raw_status, raw_status)}.', "success")
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
        request_counts=site_request_counts(),
    )


def admin_job_status_response():
    scope = (request.args.get("scope") or "").strip()
    dataset = (request.args.get("dataset") or "").strip() or None
    events = latest_job_events(scope, dataset) if scope else []
    return jsonify({"events": events})


def legacy_imports_notice():
    return (
        jsonify(
            {
                "error": "WebSocket streaming has been replaced with HTTP polling. "
                "Please reload the page to use the latest interface."
            }
        ),
        410,
    )
