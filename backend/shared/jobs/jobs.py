"""Background job definitions for long-running tasks."""

from __future__ import annotations

import json
import os
import time
import uuid
import logging
from pathlib import Path
from typing import Optional

from flask import current_app, has_app_context

_jobs_disabled = os.getenv("DISABLE_BACKGROUND_JOBS", "0").lower() in {"1", "true", "yes", "on"}
if _jobs_disabled:
    get_current_job = None  # type: ignore
    get_queue = None  # type: ignore
    _jobs_available = False
else:  # pragma: no cover - optional dependency
    try:
        from rq import get_current_job
        from shared.jobs.task_queue import get_queue
        _jobs_available = True
    except Exception:
        get_current_job = None  # type: ignore
        get_queue = None  # type: ignore
        _jobs_available = False
from shared.jobs.background.imports import run_csv_import
from core.domains.cards.services.csv_importer import FileValidationError, HeaderValidationError, validate_import_file
from shared.events.live_updates import emit_job_event
from shared.jobs.csv_import_jobs_service import (
    cleanup_temp_file as _cleanup_temp_file_service,
    enqueue_csv_import as _enqueue_csv_import_service,
    process_csv_import as _process_csv_import_service,
    run_csv_import_inline as _run_csv_import_inline_service,
    run_csv_import_job as _run_csv_import_job_service,
    start_inline_import_thread as _start_inline_import_thread_service,
)
from core.domains.cards.services.scryfall_cache import ensure_cache_loaded
from core.domains.cards.services import scryfall_cache as sc
from core.domains.decks.services.spellbook_sync import (
    EARLY_MANA_VALUE_THRESHOLD,
    LATE_MANA_VALUE_THRESHOLD,
    generate_spellbook_combo_dataset,
    write_dataset_to_file,
)
from shared.jobs.background.edhrec_sync import refresh_edhrec_synergy_cache
from core.domains.decks.services.commander_brackets import reload_spellbook_combos
from sqlalchemy import func


def _create_app():
    from app import create_app

    return create_app()


def _get_logger():
    if has_app_context() and current_app:
        return current_app.logger
    return logging.getLogger(__name__)

KEEP_FAILED_IMPORT_UPLOADS = os.getenv("IMPORT_KEEP_FAILED_UPLOADS", "1").lower() in {"1", "true", "yes", "on"}


def _start_inline_import_thread(
    *,
    filepath: str,
    quantity_mode: str,
    overwrite: bool,
    owner_user_id: Optional[int],
    owner_username: Optional[str],
    job_id: str,
    mapping_override: Optional[dict],
) -> None:
    _start_inline_import_thread_service(
        filepath=filepath,
        quantity_mode=quantity_mode,
        overwrite=overwrite,
        owner_user_id=owner_user_id,
        owner_username=owner_username,
        job_id=job_id,
        mapping_override=mapping_override,
        run_csv_import_inline=run_csv_import_inline,
        get_logger=_get_logger,
    )


def enqueue_csv_import(
    filepath: str,
    quantity_mode: str,
    overwrite: bool = False,
    *,
    owner_user_id: Optional[int] = None,
    owner_username: Optional[str] = None,
    mapping_override: Optional[dict] = None,
    run_async: bool = False,
) -> dict:
    return _enqueue_csv_import_service(
        filepath,
        quantity_mode,
        overwrite,
        owner_user_id=owner_user_id,
        owner_username=owner_username,
        mapping_override=mapping_override,
        run_async=run_async,
        inline_pref=bool(current_app.config.get("IMPORT_RUN_INLINE", True)),
        jobs_available=_jobs_available,
        validate_import_file=validate_import_file,
        get_logger=_get_logger,
        get_queue=get_queue,
        run_csv_import_inline=run_csv_import_inline,
        run_csv_import_job=run_csv_import_job,
        start_inline_import_thread=_start_inline_import_thread,
        has_app_context=has_app_context,
        current_app=current_app,
    )


def run_csv_import_job(
    filepath: str,
    quantity_mode: str,
    overwrite: bool,
    import_job_id: str,
    owner_user_id: Optional[int],
    owner_username: Optional[str],
    mapping_override: Optional[dict] = None,
):
    return _run_csv_import_job_service(
        filepath,
        quantity_mode,
        overwrite,
        import_job_id,
        owner_user_id,
        owner_username,
        mapping_override,
        create_app=_create_app,
        get_current_job=get_current_job,
        get_logger=_get_logger,
        process_csv_import=_process_csv_import,
        cleanup_temp_file=_cleanup_temp_file,
        keep_failed_import_uploads=KEEP_FAILED_IMPORT_UPLOADS,
    )


def run_csv_import_inline(
    filepath: str,
    quantity_mode: str,
    overwrite: bool,
    *,
    owner_user_id: Optional[int] = None,
    owner_username: Optional[str] = None,
    job_id: Optional[str] = None,
    mapping_override: Optional[dict] = None,
):
    return _run_csv_import_inline_service(
        filepath,
        quantity_mode,
        overwrite,
        owner_user_id=owner_user_id,
        owner_username=owner_username,
        job_id=job_id,
        mapping_override=mapping_override,
        has_app_context=has_app_context,
        current_app=current_app,
        create_app=_create_app,
        process_csv_import=_process_csv_import,
        cleanup_temp_file=_cleanup_temp_file,
        keep_failed_import_uploads=KEEP_FAILED_IMPORT_UPLOADS,
    )


def _process_csv_import(
    *,
    filepath: str,
    quantity_mode: str,
    overwrite: bool,
    import_job_id: str,
    owner_user_id: Optional[int],
    owner_username: Optional[str],
    job_ref,
    mapping_override: Optional[dict] = None,
):
    return _process_csv_import_service(
        filepath=filepath,
        quantity_mode=quantity_mode,
        overwrite=overwrite,
        import_job_id=import_job_id,
        owner_user_id=owner_user_id,
        owner_username=owner_username,
        job_ref=job_ref,
        mapping_override=mapping_override,
        emit_job_event=emit_job_event,
        run_csv_import=run_csv_import,
        header_validation_error_cls=HeaderValidationError,
        file_validation_error_cls=FileValidationError,
    )


def _cleanup_temp_file(filepath: str, logger) -> None:
    _cleanup_temp_file_service(filepath, logger)


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
    log = _get_logger()
    log.info("Scryfall refresh started (inline): kind=%s force=%s job_id=%s", kind, force_download, job_id)
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
        log.info("Scryfall refresh completed (inline): kind=%s job_id=%s", kind, job_id)
        return info
    except Exception as exc:
        log.error("Scryfall refresh failed (inline): kind=%s job_id=%s error=%s", kind, job_id, exc, exc_info=True)
        emit_job_event("scryfall", "failed", job_id=job_id, dataset=kind, error=str(exc))
        raise


def run_scryfall_refresh_job(kind: str, job_id: str, force_download: bool = False):
    app = _create_app()
    with app.app_context():
        job = get_current_job()
        log = _get_logger()
        log.info("Scryfall refresh started (job): kind=%s job_id=%s", kind, job_id)
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
            log.info("Scryfall refresh completed (job): kind=%s job_id=%s", kind, job_id)
        except Exception as exc:
            log.error("Scryfall refresh failed (job): kind=%s job_id=%s error=%s", kind, job_id, exc, exc_info=True)
            emit_job_event(
                "scryfall",
                "failed",
                job_id=job_id,
                dataset=kind,
                error=str(exc),
            )
            raise




def _refresh_spellbook_dataset(force_download: bool = False) -> dict:
    data_dir = Path(sc.default_cards_path()).parent
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
        log = _get_logger()
        log.info("Spellbook refresh started (job): job_id=%s force=%s", job_id, force_download)
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
            if info.get("status") == "warning":
                log.warning("Spellbook refresh completed with warnings: job_id=%s", job_id)
            else:
                log.info("Spellbook refresh completed (job): job_id=%s", job_id)
            return info
        except Exception as exc:
            log.error("Spellbook refresh failed (job): job_id=%s error=%s", job_id, exc, exc_info=True)
            emit_job_event("spellbook", "failed", job_id=job_id, dataset="spellbook", error=str(exc))
            raise


def run_spellbook_refresh_inline(force_download: bool = False) -> dict:
    """Inline fallback with job events for progress tracking."""
    job_id = f"inline-{uuid.uuid4().hex[:8]}"
    log = _get_logger()
    log.info("Spellbook refresh started (inline): job_id=%s force=%s", job_id, force_download)
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
        if info.get("status") == "warning":
            log.warning("Spellbook refresh completed with warnings: job_id=%s", job_id)
        else:
            log.info("Spellbook refresh completed (inline): job_id=%s", job_id)
        return info
    except Exception as exc:
        log.error("Spellbook refresh failed (inline): job_id=%s error=%s", job_id, exc, exc_info=True)
        emit_job_event("spellbook", "failed", job_id=job_id, dataset="spellbook", error=str(exc))
        raise


def enqueue_edhrec_refresh(*, force_refresh: bool = False, scope: str = "all") -> str:
    if not _jobs_available:
        raise RuntimeError("RQ is not installed; unable to queue EDHREC refresh.")
    job_id = uuid.uuid4().hex
    queue = get_queue()
    try:
        queue.enqueue(
            run_edhrec_refresh_job,
            force_refresh,
            scope,
            job_id,
            job_id=f"edhrec-{job_id}",
            description=f"edhrec-refresh:{scope}",
        )
    except Exception as exc:
        raise RuntimeError(f"Unable to queue EDHREC refresh: {exc}") from exc
    emit_job_event(
        "edhrec",
        "queued",
        job_id=job_id,
        dataset="synergy",
        force=int(force_refresh),
        refresh_scope=scope,
    )
    return job_id


def run_edhrec_refresh_job(force_refresh: bool, scope: str, job_id: str) -> dict:
    app = _create_app()
    with app.app_context():
        job = get_current_job()
        log = _get_logger()
        log.info("EDHREC refresh started (job): job_id=%s force=%s scope=%s", job_id, force_refresh, scope)
        emit_job_event(
            "edhrec",
            "started",
            job_id=job_id,
            dataset="synergy",
            rq_id=getattr(job, "id", None),
            refresh_scope=scope,
        )
        try:
            result = refresh_edhrec_synergy_cache(force_refresh=force_refresh, scope=scope)
        except Exception as exc:
            log.error("EDHREC refresh failed (job): job_id=%s error=%s", job_id, exc, exc_info=True)
            emit_job_event("edhrec", "failed", job_id=job_id, dataset="synergy", error=str(exc))
            raise
        status = result.get("status") or "error"
        message = result.get("message") or "EDHREC refresh failed."
        if status == "error":
            emit_job_event("edhrec", "failed", job_id=job_id, dataset="synergy", error=message)
            return result
        if status == "info":
            emit_job_event("edhrec", "completed", job_id=job_id, dataset="synergy", status="info", message=message)
            return result
        emit_job_event(
            "edhrec",
            "completed",
            job_id=job_id,
            dataset="synergy",
            status=status,
            message=message,
            commanders=result.get("commanders") or {},
            themes=result.get("themes") or {},
        )
        log.info("EDHREC refresh completed (job): job_id=%s status=%s", job_id, status)
        return result


def run_edhrec_refresh_inline(*, force_refresh: bool = False, scope: str = "all") -> dict:
    job_id = f"inline-{uuid.uuid4().hex[:8]}"
    log = _get_logger()
    log.info("EDHREC refresh started (inline): job_id=%s force=%s scope=%s", job_id, force_refresh, scope)
    emit_job_event(
        "edhrec",
        "queued",
        job_id=job_id,
        dataset="synergy",
        force=int(force_refresh),
        refresh_scope=scope,
    )
    emit_job_event(
        "edhrec",
        "started",
        job_id=job_id,
        dataset="synergy",
        rq_id=None,
        refresh_scope=scope,
    )
    try:
        result = refresh_edhrec_synergy_cache(force_refresh=force_refresh, scope=scope)
    except Exception as exc:
        log.error("EDHREC refresh failed (inline): job_id=%s error=%s", job_id, exc, exc_info=True)
        emit_job_event("edhrec", "failed", job_id=job_id, dataset="synergy", error=str(exc))
        raise
    status = result.get("status") or "error"
    message = result.get("message") or "EDHREC refresh failed."
    if status == "error":
        emit_job_event("edhrec", "failed", job_id=job_id, dataset="synergy", error=message)
        return result
    if status == "info":
        emit_job_event("edhrec", "completed", job_id=job_id, dataset="synergy", status="info", message=message)
        return result
    emit_job_event(
        "edhrec",
        "completed",
        job_id=job_id,
        dataset="synergy",
        status=status,
        message=message,
        commanders=result.get("commanders") or {},
        themes=result.get("themes") or {},
    )
    log.info("EDHREC refresh completed (inline): job_id=%s status=%s", job_id, status)
    return result


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
