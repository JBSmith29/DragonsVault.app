"""Collection import/export routes."""

from __future__ import annotations

import csv
import os
import time
from io import BytesIO, StringIO
from pathlib import Path
import re

from flask import Response, current_app, flash, redirect, render_template, request, send_file, url_for, session
from flask_login import current_user, login_required
from sqlalchemy import case, func

from extensions import db, limiter
from models import Card, Folder
from services.csv_importer import preview_csv, HeaderValidationError
from services.jobs import enqueue_csv_import
from services.audit import record_audit_event
from services.scryfall_cache import (
    ensure_cache_loaded,
    find_by_set_cn,
    find_by_set_cn_loose,
    search_prints,
)

from werkzeug.utils import secure_filename

from .base import _collector_number_numeric, _move_folder_choices, _name_sort_expr, limiter_key_user_or_ip, views

ALLOWED_IMPORT_EXTS = {".csv", ".xlsx", ".xls"}
MAX_IMPORT_BYTES = int(os.getenv("IMPORT_MAX_BYTES", 10 * 1024 * 1024))  # 10MB default

_MANUAL_LINE_RE = re.compile(r"^\s*(\d+)\s*[xX]?\s+(.*)$")


def _parse_manual_card_list(raw: str) -> list[dict]:
    entries: list[dict] = []
    for line in (raw or "").splitlines():
        text = (line or "").strip()
        if not text:
            continue
        qty = 1
        name = text
        match = _MANUAL_LINE_RE.match(text)
        if match:
            try:
                qty = max(int(match.group(1)), 1)
            except (TypeError, ValueError):
                qty = 1
            name = match.group(2).strip()
        if not name:
            continue
        entries.append({"name": name, "quantity": qty})
    return entries


def _printing_options_for_name(name: str, limit: int = 12) -> list[dict]:
    ensure_cache_loaded()
    prints, _ = search_prints(name_q=name, limit=limit)
    options: list[dict] = []
    for pr in prints or []:
        set_code = (pr.get("set") or "").upper()
        cn = pr.get("collector_number") or ""
        lang = (pr.get("lang") or "en").upper()
        value = f"{set_code}::{cn}::{lang}"
        finishes = pr.get("finishes") or []
        options.append(
            {
                "value": value,
                "set_code": set_code,
                "collector_number": cn,
                "lang": lang,
                "set_name": pr.get("set_name") or "",
                "rarity": (pr.get("rarity") or "").title(),
                "released_at": pr.get("released_at") or "",
                "finishes": finishes or ["nonfoil"],
                "name": pr.get("name") or name,
            }
        )
    if not options:
        # Provide a fallback option so the wizard can continue even if cache misses.
        options.append(
            {
                "value": f"UNK::{name}::EN",
                "set_code": "UNK",
                "collector_number": name,
                "lang": "EN",
                "set_name": "Custom",
                "rarity": "",
                "released_at": "",
                "finishes": ["nonfoil", "foil"],
                "name": name,
            }
        )
    return options


def _uploads_dir():
    p = Path(current_app.instance_path) / "uploads"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _validate_upload_path(filepath: str) -> str:
    """Ensure the path exists and lives under the uploads directory."""
    uploads_root = _uploads_dir().resolve()
    resolved = Path(filepath).resolve()
    if not resolved.exists():
        raise ValueError("Uploaded file not found. Please re-upload.")
    try:
        if uploads_root not in resolved.parents and resolved != uploads_root:
            raise ValueError("Invalid upload path.")
    except Exception:
        raise ValueError("Invalid upload path.")
    return str(resolved)


def _save_upload_if_present(file):
    """Return saved path or None if no file provided."""
    if not file or not getattr(file, "filename", ""):
        return None
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_IMPORT_EXTS:
        raise ValueError("Unsupported file type. Please upload a CSV or Excel file (.csv, .xlsx, .xls).")
    fname = secure_filename(file.filename) or f"import{ext}"
    ts = int(time.time())
    dest_path = _uploads_dir() / f"{ts}-{fname}"

    # size guard: rely on content_length if provided, then enforce after save
    if getattr(file, "content_length", None) and int(file.content_length or 0) > MAX_IMPORT_BYTES:
        raise ValueError(
            f"File is too large. Limit: {MAX_IMPORT_BYTES // (1024 * 1024)} MB."
        )

    file.save(dest_path)
    try:
        if dest_path.stat().st_size > MAX_IMPORT_BYTES:
            dest_path.unlink(missing_ok=True)
            raise ValueError(
                f"File is too large. Limit: {MAX_IMPORT_BYTES // (1024 * 1024)} MB."
            )
    except ValueError:
        raise
    except Exception:
        # If stat fails, remove file defensively
        try:
            dest_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise ValueError("Unable to save upload. Please try again.")

    return str(dest_path)


def _export_context() -> dict:
    """Builds folder and format metadata for the import/export UI."""
    user_id = None
    if current_user.is_authenticated:
        try:
            user_id = int(current_user.get_id())
        except (TypeError, ValueError, Exception):
            raw_id = session.get("_user_id")
            try:
                user_id = int(raw_id) if raw_id is not None else None
            except (TypeError, ValueError):
                user_id = None

    folder_category_labels = {
        Folder.CATEGORY_DECK: "Deck",
        Folder.CATEGORY_COLLECTION: "Collection",
        Folder.CATEGORY_BUILD: "In-progress build",
    }
    if user_id is None:
        user_folders = []
    else:
        user_folders = (
            Folder.query.filter(Folder.owner_user_id == user_id)
            .order_by(func.lower(Folder.name))
            .all()
        )
    folder_export_options = [
        {
            "id": folder.id,
            "name": folder.name,
            "category_label": folder_category_labels.get(folder.category or Folder.CATEGORY_DECK, "Deck"),
            "is_proxy": bool(folder.is_proxy),
            "is_public": bool(folder.is_public),
        }
        for folder in user_folders
    ]
    export_format_options = [
        {
            "value": "",
            "label": "Standard CSV",
            "description": "Includes folder name, quantity, set code, collector number, language, and foil status.",
        },
        {
            "value": "manavault",
            "label": "ManaVault",
            "description": "Matches ManaVault's CSV headers (Count, Name, Edition, Collector Number, Language, Finish).",
        },
        {
            "value": "manabox",
            "label": "ManaBox",
            "description": "Generates the ManaBox binder layout with count, name, edition, number, and finish.",
        },
        {
            "value": "dragonshield",
            "label": "Dragon Shield",
            "description": "Exports Dragon Shield-style rows with quantity, printing, condition, and language.",
        },
    ]
    return {
        "folder_export_options": folder_export_options,
        "export_format_options": export_format_options,
    }


@views.route("/import", methods=["GET", "POST"])
@limiter.limit("5 per minute", methods=["POST"]) if limiter else (lambda f: f)
@limiter.limit("20 per hour", methods=["POST"], key_func=limiter_key_user_or_ip) if limiter else (lambda f: f)
@login_required
def import_csv():
    """Upload route that powers CSV/XLS collection imports and dry-run previews."""

    def _store_import_notification(level: str, message: str) -> None:
        session["last_import_notification"] = {"level": level, "message": message}

    def _normalize_quantity_mode(raw: str | None) -> str:
        value = (raw or "delta").strip().lower()
        if value in {"absolute", "replace", "overwrite"}:
            return "absolute"
        if value in {"delta", "add", "increment"}:
            return "delta"
        if value in {"new_only", "new", "add_new"}:
            return "new_only"
        if value in {"purge", "clear", "reset"}:
            return "purge"
        return "delta"

    if request.method == "GET":
        notification = session.pop("last_import_notification", None)
        return render_template(
            "cards/import.html",
            quantity_mode="delta",
            notification=notification,
            **_export_context(),
        )

    action = (request.form.get("action") or "").strip().lower()

    # PREVIEW
    if action == "preview":
        file = request.files.get("file")
        saved = None
        quantity_mode = _normalize_quantity_mode(request.form.get("quantity_mode"))
        try:
            saved = _save_upload_if_present(file)
            if not saved:
                flash("Please choose a CSV or Excel file.", "warning")
                return redirect(request.referrer or url_for("views.import_csv"))

            saved = _validate_upload_path(saved)
            pv = preview_csv(saved, default_folder="Unsorted", max_rows=100)
            return render_template(
                "cards/import.html",
                preview=pv,
                filepath=saved,
                quantity_mode=quantity_mode,
                **_export_context(),
            )

        except Exception as e:
            current_app.logger.exception("Preview failed")
            db.session.rollback()
            flash(f"Could not read file: {e}", "danger")
            if saved:
                try:
                    os.remove(saved)
                except Exception:
                    pass
            return redirect(url_for("views.import_csv"))

    # CONFIRM / OVERWRITE
    elif action in ("confirm", "overwrite"):
        filepath = (request.form.get("filepath") or "").strip()
        file = request.files.get("file")
        if not filepath:
            try:
                filepath = _save_upload_if_present(file)
            except Exception as e:
                flash(str(e), "warning")
                return redirect(url_for("views.import_csv"))

        if not filepath:
            flash("Please choose a CSV or Excel file.", "warning")
            return redirect(url_for("views.import_csv"))
        try:
            filepath = _validate_upload_path(filepath)
        except Exception as exc:
            flash(str(exc), "warning")
            return redirect(url_for("views.import_csv"))

        quantity_mode = _normalize_quantity_mode(request.form.get("quantity_mode"))
        overwrite = (action == "overwrite") or (quantity_mode == "purge")
        filename = os.path.basename(filepath)

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
            )
        except HeaderValidationError as err:
            current_app.logger.warning("Import aborted due to header validation: %s", err)
            flash(str(err), "warning")
            return redirect(url_for("views.import_csv"))
        except Exception as exc:
            current_app.logger.exception("Failed to queue import job")
            flash(f"Unable to queue import: {exc}", "danger")
            return redirect(url_for("views.import_csv"))

        mode_note = "overwrite" if overwrite else quantity_mode
        job_id = result["job_id"]
        if result.get("ran_inline"):
            stats = result.get("stats")
            if stats:
                details = f"added {stats.added}, updated {stats.updated}, skipped {stats.skipped}"
                if stats.errors:
                    details += f", {stats.errors} error(s)"
                level = "warning" if stats.errors else "success"
                flash(f"Import applied immediately ({details}).", level)
                _store_import_notification(level, f"Import completed: {details}")
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
                        name = (s.get("name") if isinstance(s, dict) else None) or row.get("Name") or row.get("Card Name") or row.get("Card") or ""
                        qty_raw = row.get("Quantity") or row.get("Qty") or row.get("Qty.") or 1
                        try:
                            qty = max(int(qty_raw), 1)
                        except Exception:
                            qty = 1
                        if name:
                            manual_lines.append(f"{qty} {name}".strip())
                    if manual_lines:
                        session["manual_import_seed"] = "\n".join(manual_lines[:200])
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
                _store_import_notification("success", "Import completed immediately.")
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
            _store_import_notification("info", message)
            record_audit_event(
                "import_queued",
                {
                    "job_id": job_id,
                    "mode": mode_note,
                    "filename": filename,
                },
            )
        return redirect(url_for("views.import_csv", import_success=1))

    else:
        flash("Unsupported action.", "warning")
        return redirect(url_for("views.import_csv"))


@views.route("/import/template.csv", methods=["GET"])
@login_required
def import_template_csv():
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

    # Build CSV text and encode with BOM so Excel opens it cleanly
    sio = StringIO()
    writer = csv.DictWriter(sio, fieldnames=headers, lineterminator="\r\n")
    writer.writeheader()
    for row in example_rows:
        writer.writerow(row)
    data = ("\ufeff" + sio.getvalue()).encode("utf-8-sig")  # BOM for Excel

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


@views.route("/cards/export")
def export_cards():
    """Export the current card selection as CSV."""
    q = (request.args.get("q") or "").strip()
    folder_id_raw = (request.args.get("folder") or "").strip()
    set_code = (request.args.get("set") or "").strip().lower()
    lang = (request.args.get("lang") or "").strip().lower()
    foil_arg = (request.args.get("foil_only") or request.args.get("foil") or "").strip().lower()
    foil_only = foil_arg in {"1", "true", "yes", "on", "y"}
    folder_filters: set[int] = set()
    folder_args = request.args.getlist("folder_ids") or request.args.getlist("folders")
    for item in folder_args:
        if not item:
            continue
        chunks = [part.strip() for part in str(item).split(",")]
        for chunk in chunks:
            if chunk.isdigit():
                folder_filters.add(int(chunk))
    if folder_id_raw.isdigit():
        folder_filters.add(int(folder_id_raw))

    include_all_folders = (request.args.get("all_folders") or "").strip().lower() in {"1", "true", "yes", "on"}

    query = Card.query
    if q:
        for tok in [t for t in q.split() if t]:
            query = query.filter(Card.name.ilike(f"%{tok}%"))
    if folder_filters and not include_all_folders:
        query = query.filter(Card.folder_id.in_(folder_filters))
    if set_code:
        query = query.filter(Card.set_code.ilike(set_code))
    if lang:
        query = query.filter(Card.lang.ilike(lang))
    if foil_only:
        query = query.filter(Card.is_foil.is_(True))

    # Natural order
    name_col = _name_sort_expr()
    cn_num = _collector_number_numeric()
    cn_numeric_last = case((cn_num.is_(None), 1), else_=0)
    rows = (
        query.order_by(
            name_col.asc(),
            Card.set_code.asc(),
            cn_numeric_last.asc(),
            cn_num.asc(),
            Card.collector_number.asc(),
        ).all()
    )

    export_format = (request.args.get("format") or request.args.get("style") or "").strip().lower()
    si = StringIO()
    writer = csv.writer(si)
    filename = "cards_export.csv"

    if export_format == "manavault":
        filename = "dragonsvault-manavault.csv"
        writer.writerow(["Count", "Name", "Edition", "Collector Number", "Language", "Finish"])
        for c in rows:
            writer.writerow(
                [
                    c.quantity or 1,
                    c.name,
                    (c.set_code or "").upper(),
                    c.collector_number or "",
                    (c.lang or "en").upper(),
                    "Foil" if c.is_foil else "Nonfoil",
                ]
            )
    elif export_format == "manabox":
        filename = "dragonsvault-manabox.csv"
        writer.writerow(["Count", "Name", "Edition", "Collector Number", "Finish"])
        for c in rows:
            writer.writerow(
                [
                    c.quantity or 1,
                    c.name,
                    (c.set_code or "").upper(),
                    c.collector_number or "",
                    "Foil" if c.is_foil else "Nonfoil",
                ]
            )
    elif export_format == "dragonshield":
        filename = "dragonsvault-dragonshield.csv"
        writer.writerow(["Quantity", "Name", "Set Code", "Collector Number", "Printing", "Condition", "Language"])
        for c in rows:
            writer.writerow(
                [
                    c.quantity or 1,
                    c.name,
                    (c.set_code or "").upper(),
                    c.collector_number or "",
                    "Foil" if c.is_foil else "Normal",
                    "Near Mint",
                    (c.lang or "English"),
                ]
            )
    else:
        writer.writerow(["Folder Name", "Quantity", "Card Name", "Set Code", "Collector Number", "Language", "Printing"])
        for c in rows:
            writer.writerow(
                [
                    c.folder.name if c.folder else "",
                    c.quantity or 1,
                    c.name,
                    c.set_code,
                    c.collector_number,
                    c.lang or "en",
                    "Foil" if c.is_foil else "Nonfoil",
                ]
            )

    out = si.getvalue()
    return Response(
        out,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@views.route("/import/manual", methods=["GET", "POST"])
@login_required
def manual_import():
    """Manual import wizard for pasted decklists."""
    folder_options = _move_folder_choices()
    folder_lookup = {str(option["id"]): option["name"] for option in folder_options}
    card_list = request.form.get("card_list") or session.pop("manual_import_seed", "") or ""
    parsed_entries: list[dict] = []
    step = "input"
    entry_errors: list[str] = []

    default_folder_id = (request.form.get("default_folder_id") or "").strip()
    default_folder_name = (request.form.get("default_folder_name") or "").strip()
    default_folder_category = (request.form.get("default_folder_category") or Folder.CATEGORY_DECK).strip().lower()
    if default_folder_category not in {Folder.CATEGORY_DECK, Folder.CATEGORY_COLLECTION}:
        default_folder_category = Folder.CATEGORY_DECK

    default_folder_label = "None (choose per card)"
    if default_folder_id and default_folder_id in folder_lookup:
        default_folder_label = folder_lookup.get(default_folder_id) or default_folder_label
    elif default_folder_name:
        default_folder_label = f'Create "{default_folder_name}"'

    def resolve_target_folder(folder_id_value: str | None, folder_name_value: str | None) -> Folder:
        folder: Folder | None = None
        if folder_id_value and folder_id_value.isdigit():
            folder = Folder.query.filter(
                Folder.id == int(folder_id_value),
                Folder.owner_user_id == current_user.id,
            ).first()
        if folder:
            return folder
        fallback_name = (folder_name_value or default_folder_name or "Manual Import").strip()
        if not fallback_name:
            fallback_name = "Manual Import"
        folder = (
            Folder.query.filter(
                func.lower(Folder.name) == fallback_name.lower(),
                Folder.owner_user_id == current_user.id,
            ).first()
        )
        if not folder:
            folder = Folder(
                name=fallback_name,
                owner_user_id=current_user.id,
                category=default_folder_category,
            )
            db.session.add(folder)
            db.session.flush()
        return folder

    if request.method == "POST":
        action = (request.form.get("action") or "").strip().lower()
        if action == "parse":
            raw_entries = _parse_manual_card_list(card_list)
            if not raw_entries:
                flash("Please enter at least one card (e.g., '3 Sol Ring').", "warning")
            else:
                parsed_entries = []
                idx_counter = 0
                prefill_folder_name = default_folder_name
                if not prefill_folder_name and not default_folder_id:
                    prefill_folder_name = "Manual Import"
                for raw_entry in raw_entries:
                    qty = max(raw_entry["quantity"], 1)
                    for _ in range(qty):
                        options = _printing_options_for_name(raw_entry["name"])
                        parsed_entries.append(
                            {
                                "index": idx_counter,
                                "name": raw_entry["name"],
                                "quantity": 1,
                                "options": options,
                                "prefill_folder_id": default_folder_id,
                                "prefill_folder_name": prefill_folder_name or "",
                            }
                        )
                        idx_counter += 1
                step = "review"
        elif action == "quick_upload":
            raw_entries = _parse_manual_card_list(card_list)
            if not raw_entries:
                flash("Please enter at least one card (e.g., '3 Sol Ring').", "warning")
            else:
                folder = resolve_target_folder(default_folder_id, default_folder_name)

                merged: dict[str, int] = {}
                for entry in raw_entries:
                    name = entry.get("name") or ""
                    qty = int(entry.get("quantity") or 0) or 1
                    if not name:
                        continue
                    merged[name] = merged.get(name, 0) + max(qty, 1)

                created = 0
                for name, qty in merged.items():
                    card = Card(
                        name=name,
                        set_code="",
                        collector_number="",
                        lang="EN",
                        folder_id=folder.id,
                        quantity=qty,
                        is_foil=False,
                    )
                    db.session.add(card)
                    created += 1

                if created:
                    db.session.commit()
                    total_qty = sum(merged.values())
                    flash(
                        f"Quick uploaded {total_qty} card{'s' if total_qty != 1 else ''} into \"{folder.name}\". "
                        "Edit printings and details later from that folder.",
                        "success",
                    )
                    return redirect(url_for("views.list_cards"))
                else:
                    db.session.rollback()
                    flash("Unable to quick upload the provided entries.", "warning")
        elif action == "import":
            entry_ids_raw = (request.form.get("entry_ids") or "").strip()
            entry_ids = [chunk.strip() for chunk in entry_ids_raw.split(",") if chunk.strip()]
            if not entry_ids:
                flash("No entries were selected for import.", "warning")
            else:
                created = 0
                ensure_cache_loaded()
                for entry_id in entry_ids:
                    name = (request.form.get(f"entry-{entry_id}-name") or "").strip()
                    if not name:
                        entry_errors.append(f"Entry {entry_id}: missing card name.")
                        continue
                    qty_raw = request.form.get(f"entry-{entry_id}-quantity")
                    try:
                        quantity = max(int(qty_raw or 1), 1)
                    except (TypeError, ValueError):
                        quantity = 1

                    printing_value = (request.form.get(f"entry-{entry_id}-printing") or "").strip()
                    set_code = collector_number = lang = None
                    if printing_value and "::" in printing_value:
                        pieces = printing_value.split("::")
                        if len(pieces) >= 3:
                            set_code, collector_number, lang = pieces[:3]
                    set_code = (set_code or "").upper()
                    lang = (lang or "EN").upper()

                    finish = (request.form.get(f"entry-{entry_id}-finish") or "nonfoil").lower()
                    is_foil = finish == "foil"

                    folder_id_raw = (request.form.get(f"entry-{entry_id}-folder_id") or "").strip()
                    folder_name = (request.form.get(f"entry-{entry_id}-folder_name") or "").strip()

                    folder = resolve_target_folder(folder_id_raw, folder_name)

                    scryfall_data = None
                    if set_code and collector_number:
                        scryfall_data = find_by_set_cn(set_code, collector_number, name)
                        if not scryfall_data:
                            scryfall_data = find_by_set_cn_loose(set_code, collector_number, name)

                    card_kwargs = {
                        "name": (scryfall_data or {}).get("name") or name,
                        "set_code": (scryfall_data or {}).get("set") or set_code or "",
                        "collector_number": (scryfall_data or {}).get("collector_number") or collector_number or "",
                        "lang": (scryfall_data or {}).get("lang") or lang or "EN",
                        "folder_id": folder.id,
                        "quantity": quantity,
                        "is_foil": is_foil,
                        "rarity": (scryfall_data or {}).get("rarity"),
                        "oracle_id": (scryfall_data or {}).get("oracle_id"),
                    }

                    new_card = Card(**card_kwargs)
                    db.session.add(new_card)
                    created += 1

                if created:
                    db.session.commit()
                    flash(f"Added {created} card{'s' if created != 1 else ''} via manual import.", "success")
                    return redirect(url_for("views.list_cards"))
                else:
                    db.session.rollback()
                    if not entry_errors:
                        flash("Unable to import the provided entries.", "warning")

    return render_template(
        "cards/manual_import.html",
        folder_options=folder_options,
        folder_lookup=folder_lookup,
        card_list=card_list,
        entries=parsed_entries,
        step=step,
        entry_errors=entry_errors,
        default_folder_id=default_folder_id,
        default_folder_name=default_folder_name,
        default_folder_category=default_folder_category,
        default_folder_label=default_folder_label,
        deck_category=Folder.CATEGORY_DECK,
        collection_category=Folder.CATEGORY_COLLECTION,
    )


__all__ = ["export_cards", "import_csv", "import_template_csv"]
