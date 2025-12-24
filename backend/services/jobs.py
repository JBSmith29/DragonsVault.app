"""Background job definitions for long-running tasks."""

from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from contextlib import nullcontext
import logging
import threading
from pathlib import Path
from typing import Optional

from flask import current_app, has_app_context

from extensions import db
_jobs_disabled = os.getenv("DISABLE_BACKGROUND_JOBS", "0").lower() in {"1", "true", "yes", "on"}
if _jobs_disabled:
    get_current_job = None  # type: ignore
    get_queue = None  # type: ignore
    _jobs_available = False
else:  # pragma: no cover - optional dependency
    try:
        from rq import get_current_job
        from services.task_queue import get_queue
        _jobs_available = True
    except Exception:
        get_current_job = None  # type: ignore
        get_queue = None  # type: ignore
        _jobs_available = False
from services.live_updates import emit_job_event
from services.import_helpers import (
    purge_cards_preserve_commanders,
    restore_commander_metadata,
    delete_empty_folders,
)
from services.csv_importer import FileValidationError, process_csv, HeaderValidationError, validate_import_file
from services.scryfall_cache import ensure_cache_loaded
from services import scryfall_cache as sc
from services.spellbook_sync import (
    EARLY_MANA_VALUE_THRESHOLD,
    LATE_MANA_VALUE_THRESHOLD,
    generate_spellbook_combo_dataset,
    write_dataset_to_file,
)
from services.commander_brackets import reload_spellbook_combos
from sqlalchemy import func


def _create_app():
    from app import create_app

    return create_app()


def _get_logger():
    if has_app_context() and current_app:
        return current_app.logger
    return logging.getLogger(__name__)


def enqueue_csv_import(
    filepath: str,
    quantity_mode: str,
    overwrite: bool = False,
    *,
    owner_user_id: Optional[int] = None,
    owner_username: Optional[str] = None,
    run_async: bool = False,
) -> dict:
    # Force inline imports so users aren't blocked by a missing/idle queue.
    inline_pref = bool(current_app.config.get("IMPORT_RUN_INLINE", True))
    inline_mode = True  # always inline to avoid hangs when workers are unavailable
    job_id = uuid.uuid4().hex
    # Validate headers before queuing the job to surface errors immediately.
    validate_import_file(filepath)
    log = _get_logger()
    log.info(
        "Import enqueue requested",
        extra={
            "job_id": job_id,
            "inline_mode": inline_mode,
            "quantity_mode": quantity_mode,
            "overwrite": overwrite,
            "filepath": filepath,
            "run_async": run_async,
        },
    )

    if run_async:
        def _runner():
            try:
                run_csv_import_inline(
                    filepath=filepath,
                    quantity_mode=quantity_mode,
                    overwrite=overwrite,
                    owner_user_id=owner_user_id,
                    owner_username=owner_username,
                    job_id=job_id,
                )
            except Exception:
                _get_logger().exception("Async import failed", extra={"job_id": job_id})

        thread = threading.Thread(
            target=_runner,
            name=f"import-{job_id[:8]}",
            daemon=True,
        )
        thread.start()
        return {
            "job_id": job_id,
            "ran_inline": False,
            "stats": None,
            "per_folder": None,
        }

    if inline_mode:
        stats, per_folder = run_csv_import_inline(
            filepath=filepath,
            quantity_mode=quantity_mode,
            overwrite=overwrite,
            owner_user_id=owner_user_id,
            owner_username=owner_username,
            job_id=job_id,
        )
        return {
            "job_id": job_id,
            "ran_inline": True,
            "stats": stats,
            "per_folder": per_folder,
        }

    queue = get_queue()
    try:
        queue.enqueue(
            run_csv_import_job,
            filepath,
            quantity_mode,
            overwrite,
            job_id,
            owner_user_id,
            owner_username,
            job_id=f"import-{job_id}",
            description=f"csv-import:{os.path.basename(filepath)}",
        )
        return {
            "job_id": job_id,
            "ran_inline": False,
            "stats": None,
            "per_folder": None,
        }
    except Exception as exc:  # pragma: no cover - relies on external redis service
        # Fall back to inline if the queue/Redis is unavailable so imports don't silently hang.
        if has_app_context():
            current_app.logger.warning("Queue unavailable; running import inline: %s", exc)
        stats, per_folder = run_csv_import_inline(
            filepath=filepath,
            quantity_mode=quantity_mode,
            overwrite=overwrite,
            owner_user_id=owner_user_id,
            owner_username=owner_username,
            job_id=job_id,
        )
        return {
            "job_id": job_id,
            "ran_inline": True,
            "stats": stats,
            "per_folder": per_folder,
        }


def run_csv_import_job(
    filepath: str,
    quantity_mode: str,
    overwrite: bool,
    import_job_id: str,
    owner_user_id: Optional[int],
    owner_username: Optional[str],
):
    app = _create_app()
    with app.app_context():
        job = get_current_job()
        log = _get_logger()
        log.info(
            "Import job started",
            extra={
                "job_id": import_job_id,
                "quantity_mode": quantity_mode,
                "overwrite": overwrite,
                "owner_user_id": owner_user_id,
                "owner_username": owner_username,
                "filepath": filepath,
            },
        )
        try:
            _process_csv_import(
                filepath=filepath,
                quantity_mode=quantity_mode,
                overwrite=overwrite,
                import_job_id=import_job_id,
                owner_user_id=owner_user_id,
                owner_username=owner_username,
                job_ref=job,
            )
            log.info(
                "Import job completed",
                extra={"job_id": import_job_id, "quantity_mode": quantity_mode, "overwrite": overwrite},
            )
        finally:
            _cleanup_temp_file(filepath, app.logger)


def run_csv_import_inline(
    filepath: str,
    quantity_mode: str,
    overwrite: bool,
    *,
    owner_user_id: Optional[int] = None,
    owner_username: Optional[str] = None,
    job_id: Optional[str] = None,
):
    job_id = job_id or f"inline-{uuid.uuid4().hex[:8]}"
    if has_app_context():
        ctx = nullcontext()
        app_logger = current_app.logger
    else:
        app = _create_app()
        ctx = app.app_context()
        app_logger = app.logger
    with ctx:
        try:
            app_logger.info(
                "Import inline start",
                extra={
                    "job_id": job_id,
                    "quantity_mode": quantity_mode,
                    "overwrite": overwrite,
                    "owner_user_id": owner_user_id,
                    "owner_username": owner_username,
                    "filepath": filepath,
                },
            )
            stats, per_folder = _process_csv_import(
                filepath=filepath,
                quantity_mode=quantity_mode,
                overwrite=overwrite,
                import_job_id=job_id,
                owner_user_id=owner_user_id,
                owner_username=owner_username,
                job_ref=None,
            )
            app_logger.info(
                "Import inline complete",
                extra={
                    "job_id": job_id,
                    "quantity_mode": quantity_mode,
                    "overwrite": overwrite,
                    "added": getattr(stats, "added", None) if stats else None,
                    "updated": getattr(stats, "updated", None) if stats else None,
                    "skipped": getattr(stats, "skipped", None) if stats else None,
                    "errors": getattr(stats, "errors", None) if stats else None,
                },
            )
            return stats, per_folder
        finally:
            _cleanup_temp_file(filepath, app_logger)


def _process_csv_import(
    *,
    filepath: str,
    quantity_mode: str,
    overwrite: bool,
    import_job_id: str,
    owner_user_id: Optional[int],
    owner_username: Optional[str],
    job_ref,
):
    emit_job_event(
        "import",
        "queued",
        job_id=import_job_id,
        rq_id=getattr(job_ref, "id", None),
        file=os.path.basename(filepath),
        overwrite=overwrite,
        user_id=owner_user_id,
    )
    preserved: Optional[dict] = None
    removed = 0
    try:
        with db.session.begin():
            if overwrite or quantity_mode in {"absolute", "purge"}:
                preserved = purge_cards_preserve_commanders(commit=False)
            stats, per_folder = process_csv(
                filepath,
                default_folder="Unsorted",
                dry_run=False,
                quantity_mode=quantity_mode,
                job_id=import_job_id,
                owner_user_id=owner_user_id,
                owner_username=owner_username,
                commit=False,
            )
            if preserved:
                restore_commander_metadata(preserved, commit=False)
                removed = delete_empty_folders(commit=False)
        emit_job_event(
            "import",
            "completed",
            job_id=stats.job_id,
            added=stats.added,
            updated=stats.updated,
            skipped=stats.skipped,
            errors=stats.errors,
            removed_folders=removed,
            user_id=owner_user_id,
        )
        return stats, per_folder
    except (HeaderValidationError, FileValidationError) as exc:
        emit_job_event(
            "import",
            "failed",
            job_id=import_job_id,
            error=str(exc),
            user_id=owner_user_id,
        )
        raise
    except Exception as exc:
        emit_job_event(
            "import",
            "failed",
            job_id=import_job_id,
            error=str(exc),
            user_id=owner_user_id,
        )
        raise


def _cleanup_temp_file(filepath: str, logger) -> None:
    if filepath and os.path.exists(filepath):
        try:
            os.remove(filepath)
        except Exception:
            logger.warning("Failed to remove temp import file %s", filepath, exc_info=True)


def enqueue_scryfall_refresh(kind: str, *, force_download: bool = False) -> str:
    if not _jobs_available:
        raise RuntimeError("RQ is not installed; unable to queue Scryfall refresh jobs.")
    job_id = uuid.uuid4().hex
    queue = get_queue()
    try:
        queue.enqueue(
            run_scryfall_refresh_job,
            kind,
            job_id,
            force_download,
            job_id=f"scryfall-{kind}-{job_id}",
            description=f"scryfall-refresh:{kind}",
        )
    except Exception as exc:  # pragma: no cover - depends on redis availability
        raise RuntimeError(f"Unable to queue {kind} refresh: {exc}") from exc
    emit_job_event("scryfall", "queued", job_id=job_id, dataset=kind)
    return job_id


def run_scryfall_refresh_inline(kind: str, force_download: bool = False) -> dict:
    """Execute a Scryfall refresh synchronously inside the current request."""
    job_id = f"inline-{uuid.uuid4().hex[:8]}"
    emit_job_event("scryfall", "queued", job_id=job_id, dataset=kind)
    emit_job_event("scryfall", "started", job_id=job_id, dataset=kind, rq_id=None)
    try:
        info = _download_bulk_to(kind, force=force_download, job_id=job_id)
        if kind == "default_cards":
            ensure_cache_loaded(force=True)
        emit_job_event(
            "scryfall",
            "completed",
            job_id=job_id,
            dataset=kind,
            bytes=info.get("bytes_downloaded"),
            status=info.get("download_status"),
        )
        return info
    except Exception as exc:
        emit_job_event("scryfall", "failed", job_id=job_id, dataset=kind, error=str(exc))
        raise


def run_scryfall_refresh_job(kind: str, job_id: str, force_download: bool = False):
    app = _create_app()
    with app.app_context():
        job = get_current_job()
        emit_job_event(
            "scryfall",
            "started",
            job_id=job_id,
            dataset=kind,
            rq_id=job.id if job else None,
        )
        try:
            info = _download_bulk_to(kind, force=force_download, job_id=job_id)
            if kind == "default_cards":
                ensure_cache_loaded(force=True)
            emit_job_event(
                "scryfall",
                "completed",
                job_id=job_id,
                dataset=kind,
                bytes=info.get("bytes_downloaded"),
                status=info.get("download_status"),
            )
        except Exception as exc:
            emit_job_event(
                "scryfall",
                "failed",
                job_id=job_id,
                dataset=kind,
                error=str(exc),
            )
            raise




def _refresh_spellbook_dataset(force_download: bool = False) -> dict:
    data_dir = Path(os.getenv("SCRYFALL_DATA_DIR", "data"))
    spellbook_path = data_dir / "spellbook_combos.json"
    existing_combo_total = None
    if spellbook_path.exists():
        try:
            existing_payload = json.loads(spellbook_path.read_text(encoding="utf-8"))
            existing_combo_total = len(existing_payload.get("early_game", [])) + len(existing_payload.get("late_game", []))
        except Exception:
            existing_combo_total = None

    dataset = generate_spellbook_combo_dataset(
        early_threshold=EARLY_MANA_VALUE_THRESHOLD,
        late_threshold=LATE_MANA_VALUE_THRESHOLD,
        card_count_targets=(2, 3),
    )
    early = len(dataset.get("early_game", []))
    late = len(dataset.get("late_game", []))
    total = early + late

    if total == 0 and (existing_combo_total or 0) > 0:
        return {
            "status": "warning",
            "message": "Commander Spellbook refresh returned no combos; kept the previous dataset.",
            "early": early,
            "late": late,
            "total": total,
        }

    write_dataset_to_file(dataset, spellbook_path)
    reloaded = reload_spellbook_combos()
    return {
        "status": "success" if reloaded else "warning",
        "message": f"Commander Spellbook combos refreshed ({early} early, {late} late).",
        "early": early,
        "late": late,
        "total": total,
    }


def enqueue_spellbook_refresh(force_download: bool = False) -> str:
    if not _jobs_available:
        raise RuntimeError("RQ is not installed; unable to queue Commander Spellbook refresh.")
    job_id = uuid.uuid4().hex
    queue = get_queue()
    try:
        queue.enqueue(
            run_spellbook_refresh_job,
            force_download,
            job_id,
            job_id=f"spellbook-{job_id}",
            description="spellbook-refresh",
        )
    except Exception as exc:
        raise RuntimeError(f"Unable to queue Commander Spellbook refresh: {exc}") from exc
    emit_job_event("spellbook", "queued", job_id=job_id, dataset="spellbook", force=int(force_download))
    return job_id


def run_spellbook_refresh_job(force_download: bool, job_id: str):
    app = _create_app()
    with app.app_context():
        job = get_current_job()
        emit_job_event("spellbook", "started", job_id=job_id, dataset="spellbook", rq_id=getattr(job, "id", None))
        try:
            info = _refresh_spellbook_dataset(force_download=force_download)
            emit_job_event(
                "spellbook",
                "completed",
                job_id=job_id,
                dataset="spellbook",
                status=info.get("status"),
                total=info.get("total"),
            )
            return info
        except Exception as exc:
            emit_job_event("spellbook", "failed", job_id=job_id, dataset="spellbook", error=str(exc))
            raise


def run_spellbook_refresh_inline(force_download: bool = False) -> dict:
    """Inline fallback with job events for progress tracking."""
    job_id = f"inline-{uuid.uuid4().hex[:8]}"
    emit_job_event("spellbook", "queued", job_id=job_id, dataset="spellbook")
    emit_job_event("spellbook", "started", job_id=job_id, dataset="spellbook", rq_id=None)
    try:
        info = _refresh_spellbook_dataset(force_download=force_download)
        emit_job_event(
            "spellbook",
            "completed",
            job_id=job_id,
            dataset="spellbook",
            status=info.get("status"),
            total=info.get("total"),
        )
        return info
    except Exception as exc:
        emit_job_event("spellbook", "failed", job_id=job_id, dataset="spellbook", error=str(exc))
        raise


def _download_bulk_to(kind: str, force: bool = False, *, job_id: str | None = None) -> dict:
    target = sc.get_bulk_metadata(kind)
    if not target:
        raise RuntimeError(f"Bulk dataset '{kind}' not found on Scryfall.")
    dl = target.get("download_uri")
    if not dl:
        raise RuntimeError(f"No download_uri for bulk dataset '{kind}'.")

    out_path = _dataset_output_path(kind)

    if current_app.config.get("TESTING"):
        placeholder = [] if kind == "default_cards" else {}
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(placeholder, fh)
        size = os.path.getsize(out_path)
        return {
            "name": target.get("name"),
            "description": target.get("description"),
            "updated_at": target.get("updated_at"),
            "size": size,
            "path": out_path,
            "download_status": "testing",
            "bytes_downloaded": size,
        }

    progress_cb = None
    if job_id:
        last_emit = {"ts": 0.0, "bytes": 0, "percent": -1}

        def _emit_progress(written: int, total: int) -> None:
            now = time.monotonic()
            percent = int((written / total) * 100) if total else None
            bytes_delta = written - last_emit["bytes"]
            if percent is not None:
                if (
                    percent <= last_emit["percent"]
                    and bytes_delta < 5 * 1024 * 1024
                    and (now - last_emit["ts"]) < 2.0
                ):
                    return
            else:
                if bytes_delta < 5 * 1024 * 1024 and (now - last_emit["ts"]) < 2.0:
                    return

            last_emit["ts"] = now
            last_emit["bytes"] = written
            if percent is not None:
                last_emit["percent"] = percent

            emit_job_event(
                "scryfall",
                "progress",
                job_id=job_id,
                dataset=kind,
                bytes=written,
                total=total,
                percent=percent,
            )

        progress_cb = _emit_progress

    download_result = sc.stream_download_to(out_path, dl, force_download=force, progress_cb=progress_cb)
    if download_result.get("status") == "not_modified" and not os.path.exists(out_path):
        # ETag matched but we lack a local file; force full download once.
        download_result = sc.stream_download_to(out_path, dl, force_download=True)
    size = os.path.getsize(out_path) if os.path.exists(out_path) else download_result.get("bytes", 0)

    if kind == "default_cards":
        ok = False
        try:
            ok = bool(sc.load_cache())
        except Exception:
            ok = False
        if not ok:
            if not os.path.exists(out_path):
                raise RuntimeError(
                    f"Scryfall download failed to produce {out_path}. "
                    f"Status: {download_result.get('status')}"
                )
            with open(out_path, "rb") as fh:
                header = fh.read(2)
            is_gz = header == b"\x1f\x8b" or str(dl).lower().endswith(".gz")
            if is_gz:
                import gzip

                with open(out_path, "rb") as fin, open(out_path + ".tmp", "wb") as fout:
                    fin.seek(0)
                    with gzip.GzipFile(fileobj=fin, mode="rb") as gz:
                        while True:
                            block = gz.read(1024 * 1024)
                            if not block:
                                break
                            fout.write(block)
                os.replace(out_path + ".tmp", out_path)
                size = os.path.getsize(out_path)
                try:
                    ok = bool(sc.load_cache())
                except Exception:
                    ok = False

    return {
        "name": target.get("name"),
        "description": target.get("description"),
        "updated_at": target.get("updated_at"),
        "size": size,
        "path": out_path,
        "download_status": download_result.get("status"),
        "bytes_downloaded": download_result.get("bytes"),
    }


def _dataset_output_path(kind: str) -> str:
    """
    Resolve the on-disk path for a bulk dataset using the same rules as the cache.
    Moves legacy files stored in ./data into the canonical instance/data directory once.
    """
    if kind == "default_cards":
        target = Path(sc.default_cards_path())
        legacy_name = "scryfall_default_cards.json"
    elif kind == "rulings":
        target = Path(sc.rulings_bulk_path())
        legacy_name = "rulings_by_oracle.json"
    else:
        base = current_app.config.get("SCRYFALL_DATA_DIR")
        if base:
            target_dir = Path(base)
        else:
            try:
                target_dir = Path(current_app.instance_path) / "data"
            except Exception:
                instance_env = os.getenv("INSTANCE_DIR")
                if instance_env:
                    target_dir = Path(instance_env) / "data"
                else:
                    target_dir = Path(__file__).resolve().parents[2] / "instance" / "data"
        target = target_dir / f"{kind}.json"
        legacy_name = f"{kind}.json"

    target = target.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)

    try:
        root_path = Path(current_app.root_path)
    except Exception:
        root_path = Path(".")
    legacy_dir = root_path / "data"
    legacy_path = legacy_dir / legacy_name
    if not target.exists() and legacy_path.exists():
        try:
            legacy_path.replace(target)
        except Exception:
            pass

    return str(target)
