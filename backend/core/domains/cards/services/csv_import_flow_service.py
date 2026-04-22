"""CSV import queueing, status, and template download services."""

from __future__ import annotations

import csv
import os
import time
from io import BytesIO, StringIO

from flask import Response, current_app, flash, jsonify, redirect, request, send_file, url_for
from flask_login import current_user

from shared.service_result import ServiceResult
from core.domains.users.services.audit import record_audit_event
from core.domains.cards.services.csv_importer import FileValidationError, HeaderValidationError
from core.domains.cards.services.import_shared_service import (
    IMPORT_UPLOAD_RETENTION_HOURS,
    _base_import_context,
    _json_error,
    _normalize_quantity_mode,
    _save_upload_if_present,
    _store_import_notification,
    _validate_upload_path,
    _wants_json_response,
)
from shared.jobs.jobs import enqueue_csv_import
from shared.events.live_updates import latest_job_events


def handle_import_csv(*, session_obj) -> ServiceResult:
    """Upload route that powers CSV/XLS collection imports."""
    if request.method == "GET":
        notification = session_obj.pop("last_import_notification", None)
        return ServiceResult(
            template="cards/import.html",
            context=_base_import_context(
                session_obj,
                quantity_mode="new_only",
                notification=notification,
            ),
        )

    action = (request.form.get("import_action") or request.form.get("action") or "").strip().lower()
    quantity_mode = _normalize_quantity_mode(request.form.get("quantity_mode"))

    if action == "retry":
        wants_json = _wants_json_response()
        last_job = session_obj.get("last_import_job") or {}
        if not last_job:
            message = "No recent import to retry."
            if wants_json:
                return ServiceResult(response=_json_error(message, status=404))
            flash(message, "warning")
            return ServiceResult(response=redirect(url_for("views.import_csv")))

        created_at = last_job.get("created_at") or 0
        max_age_seconds = IMPORT_UPLOAD_RETENTION_HOURS * 3600
        if max_age_seconds and (time.time() - float(created_at)) > max_age_seconds:
            message = "The last import upload has expired. Please re-upload the file."
            if wants_json:
                return ServiceResult(response=_json_error(message, status=410))
            flash(message, "warning")
            return ServiceResult(response=redirect(url_for("views.import_csv")))

        filepath = (last_job.get("filepath") or "").strip()
        if not filepath or not os.path.exists(filepath):
            message = "The original upload is no longer available. Please re-upload the file."
            if wants_json:
                return ServiceResult(response=_json_error(message, status=410))
            flash(message, "warning")
            return ServiceResult(response=redirect(url_for("views.import_csv")))
        try:
            filepath = _validate_upload_path(filepath)
        except Exception as exc:
            message = str(exc)
            if wants_json:
                return ServiceResult(response=_json_error(message))
            flash(message, "warning")
            return ServiceResult(response=redirect(url_for("views.import_csv")))

        overwrite = bool(last_job.get("overwrite"))
        quantity_mode = _normalize_quantity_mode(last_job.get("quantity_mode"))

        try:
            owner_name_preferred = None
            if current_user.is_authenticated:
                owner_name_preferred = (
                    (current_user.username or "").strip()
                    or (current_user.email or "").strip()
                    or None
                )
            result = enqueue_csv_import(
                filepath=filepath,
                quantity_mode=quantity_mode,
                overwrite=overwrite,
                owner_user_id=current_user.id if current_user.is_authenticated else None,
                owner_username=owner_name_preferred,
                run_async=True,
            )
        except (HeaderValidationError, FileValidationError) as err:
            if wants_json:
                return ServiceResult(response=_json_error(str(err)))
            flash(str(err), "warning")
            return ServiceResult(response=redirect(url_for("views.import_csv")))
        except Exception as exc:
            if wants_json:
                return ServiceResult(response=_json_error(f"Unable to retry import: {exc}", status=500))
            flash(f"Unable to retry import: {exc}", "danger")
            return ServiceResult(response=redirect(url_for("views.import_csv")))

        job_id = result["job_id"]
        last_job.update(
            {
                "job_id": job_id,
                "created_at": int(time.time()),
                "filepath": filepath,
                "quantity_mode": quantity_mode,
                "overwrite": overwrite,
            }
        )
        session_obj["last_import_job"] = last_job
        if wants_json:
            payload = {
                "ok": True,
                "job_id": job_id,
                "mode": quantity_mode,
                "status_url": url_for("views.import_status", job_id=job_id),
                "complete_url": url_for("views.import_csv", import_success=1),
            }
            resp = jsonify(payload)
            resp.status_code = 202
            return ServiceResult(response=resp)
        flash("Import retry queued.", "info")
        return ServiceResult(response=redirect(url_for("views.import_csv", import_success=1)))

    if action in ("confirm", "overwrite"):
        filepath = (request.form.get("filepath") or "").strip()
        file = request.files.get("file")
        wants_json = _wants_json_response()
        if file and getattr(file, "filename", ""):
            try:
                filepath = _save_upload_if_present(file)
            except Exception as exc:
                if wants_json:
                    return ServiceResult(response=_json_error(str(exc), field_errors={"file": str(exc)}))
                return ServiceResult(
                    template="cards/import.html",
                    context=_base_import_context(
                        session_obj,
                        quantity_mode=quantity_mode,
                        field_errors={"file": str(exc)},
                        form_errors=[str(exc)],
                    ),
                )
        elif not filepath:
            try:
                filepath = _save_upload_if_present(file)
            except Exception as exc:
                if wants_json:
                    return ServiceResult(response=_json_error(str(exc), field_errors={"file": str(exc)}))
                return ServiceResult(
                    template="cards/import.html",
                    context=_base_import_context(
                        session_obj,
                        quantity_mode=quantity_mode,
                        field_errors={"file": str(exc)},
                        form_errors=[str(exc)],
                    ),
                )

        if not filepath:
            message = "Please choose a CSV or Excel file."
            if wants_json:
                return ServiceResult(response=_json_error(message, field_errors={"file": message}))
            return ServiceResult(
                template="cards/import.html",
                context=_base_import_context(
                    session_obj,
                    quantity_mode=quantity_mode,
                    field_errors={"file": message},
                    form_errors=[message],
                ),
            )
        try:
            filepath = _validate_upload_path(filepath)
        except Exception as exc:
            message = str(exc)
            if wants_json:
                return ServiceResult(response=_json_error(message, field_errors={"file": message}))
            return ServiceResult(
                template="cards/import.html",
                context=_base_import_context(
                    session_obj,
                    quantity_mode=quantity_mode,
                    field_errors={"file": message},
                    form_errors=[message],
                ),
            )

        overwrite = (action == "overwrite") or (quantity_mode == "purge")
        filename = os.path.basename(filepath)
        run_async = wants_json or (request.form.get("import_async") == "1")
        try:
            owner_name_preferred = None
            if current_user.is_authenticated:
                owner_name_preferred = (
                    (current_user.username or "").strip()
                    or (current_user.email or "").strip()
                    or None
                )

            result = enqueue_csv_import(
                filepath=filepath,
                quantity_mode=quantity_mode,
                overwrite=overwrite,
                owner_user_id=current_user.id if current_user.is_authenticated else None,
                owner_username=owner_name_preferred,
                run_async=run_async,
            )
        except (HeaderValidationError, FileValidationError) as err:
            current_app.logger.warning("Import aborted due to file validation: %s", err)
            if wants_json:
                return ServiceResult(response=_json_error(str(err), field_errors={"file": str(err)}))
            return ServiceResult(
                template="cards/import.html",
                context=_base_import_context(
                    session_obj,
                    quantity_mode=quantity_mode,
                    field_errors={"file": str(err)},
                    form_errors=[str(err)],
                ),
            )
        except Exception as exc:
            current_app.logger.exception("Failed to queue import job")
            if wants_json:
                return ServiceResult(response=_json_error(f"Unable to queue import: {exc}", status=500))
            flash(f"Unable to queue import: {exc}", "danger")
            return ServiceResult(response=redirect(url_for("views.import_csv")))

        mode_note = "overwrite" if overwrite else quantity_mode
        job_id = result["job_id"]
        session_obj["last_import_job"] = {
            "job_id": job_id,
            "filepath": filepath,
            "quantity_mode": quantity_mode,
            "overwrite": overwrite,
            "created_at": int(time.time()),
            "filename": filename,
        }
        if wants_json:
            record_audit_event(
                "import_queued",
                {
                    "job_id": job_id,
                    "mode": mode_note,
                    "filename": filename,
                },
            )
            payload = {
                "ok": True,
                "job_id": job_id,
                "mode": mode_note,
                "status_url": url_for("views.import_status", job_id=job_id),
                "complete_url": url_for("views.import_csv", import_success=1),
            }
            if result.get("ran_inline") and result.get("stats"):
                stats = result.get("stats")
                payload["stats"] = {
                    "added": stats.added,
                    "updated": stats.updated,
                    "skipped": stats.skipped,
                    "errors": stats.errors,
                }
            status = 200 if result.get("ran_inline") else 202
            resp = jsonify(payload)
            resp.status_code = status
            return ServiceResult(response=resp)
        if result.get("ran_inline"):
            stats = result.get("stats")
            if stats:
                details = f"added {stats.added}, updated {stats.updated}, skipped {stats.skipped}"
                if stats.errors:
                    details += f", {stats.errors} error(s)"
                level = "warning" if stats.errors else "success"
                flash(f"Import applied immediately ({details}).", level)
                _store_import_notification(level, f"Import completed: {details}", session_obj)
                if getattr(stats, "skipped_details", None):
                    samples = stats.skipped_details[:10]
                    sample_text = "; ".join(
                        [
                            f"{(s.get('name') or s.get('row', {}).get('Name') or 'Unknown').strip()} "
                            f"[{(s.get('set_code') or s.get('row', {}).get('Set Code') or '').upper()} "
                            f"{s.get('collector_number') or s.get('row', {}).get('Collector Number') or ''}] "
                            f"- {s.get('reason', 'Skipped')}"
                            for s in samples
                        ]
                    )
                    more = "" if len(stats.skipped_details) <= len(samples) else f" (+{len(stats.skipped_details)-len(samples)} more)"
                    flash(f"Skipped details: {sample_text}{more}", "warning")
                    manual_lines = []
                    for s in stats.skipped_details:
                        row = s.get("row", {}) if isinstance(s, dict) else {}
                        name = (
                            (s.get("name") if isinstance(s, dict) else None)
                            or row.get("Name")
                            or row.get("Card Name")
                            or row.get("Card")
                            or ""
                        )
                        qty_raw = row.get("Quantity") or row.get("Qty") or row.get("Qty.") or 1
                        try:
                            qty = max(int(qty_raw), 1)
                        except Exception:
                            qty = 1
                        if name:
                            manual_lines.append(f"{qty} {name}".strip())
                    if manual_lines:
                        session_obj["manual_import_seed"] = "\n".join(manual_lines[:200])
                record_audit_event(
                    "import_completed_inline",
                    {
                        "job_id": job_id,
                        "mode": mode_note,
                        "filename": filename,
                        "added": stats.added,
                        "updated": stats.updated,
                        "skipped": stats.skipped,
                        "skipped_details": getattr(stats, "skipped_details", []),
                        "errors": stats.errors,
                    },
                )
            else:
                flash("Import applied immediately.", "success")
                _store_import_notification("success", "Import completed immediately.", session_obj)
                record_audit_event(
                    "import_completed_inline",
                    {
                        "job_id": job_id,
                        "mode": mode_note,
                        "filename": filename,
                    },
                )
        else:
            message = f"Import queued ({mode_note}). Job ID: {job_id}. Leave this page open for completion updates."
            flash(message, "info")
            _store_import_notification("info", message, session_obj)
            record_audit_event(
                "import_queued",
                {
                    "job_id": job_id,
                    "mode": mode_note,
                    "filename": filename,
                },
            )
        return ServiceResult(response=redirect(url_for("views.import_csv", import_success=1)))

    flash("Unsupported action.", "warning")
    return ServiceResult(response=redirect(url_for("views.import_csv")))


def import_status() -> Response:
    job_id = (request.args.get("job_id") or "").strip()
    if not job_id:
        return jsonify({"ok": False, "error": "job_id is required"}), 400
    events = latest_job_events("import")
    user_id = current_user.id if current_user.is_authenticated else None
    filtered = []
    for event in events:
        if event.get("job_id") != job_id:
            continue
        event_user_id = event.get("user_id")
        if user_id is not None and event_user_id not in (None, user_id):
            continue
        filtered.append(event)
    return jsonify({"ok": True, "job_id": job_id, "events": filtered})


def import_template_csv() -> Response:
    """Serve a CSV template as a forced download."""
    headers = ["name", "set", "collector_number", "lang", "quantity", "foil", "folder"]
    example_rows = [
        {
            "name": "Sol Ring",
            "set": "2XM",
            "collector_number": "229",
            "lang": "en",
            "quantity": 1,
            "foil": 0,
            "folder": "Collection",
        },
        {
            "name": "Lightning Bolt",
            "set": "M10",
            "collector_number": "146",
            "lang": "en",
            "quantity": 4,
            "foil": 0,
            "folder": "Red Burn",
        },
    ]

    sio = StringIO()
    writer = csv.DictWriter(sio, fieldnames=headers, lineterminator="\r\n")
    writer.writeheader()
    for row in example_rows:
        writer.writerow(row)
    data = ("\ufeff" + sio.getvalue()).encode("utf-8-sig")

    bio = BytesIO(data)
    bio.seek(0)

    resp = send_file(
        bio,
        mimetype="text/csv; charset=utf-8",
        as_attachment=True,
        download_name="dragonsvault-import-template.csv",
    )
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    return resp


__all__ = [
    "handle_import_csv",
    "import_status",
    "import_template_csv",
]
