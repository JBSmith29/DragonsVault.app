"""CSV import job orchestration helpers."""

from __future__ import annotations

import os
import threading
import uuid
from contextlib import nullcontext
from typing import Callable, Optional


def start_inline_import_thread(
    *,
    filepath: str,
    quantity_mode: str,
    overwrite: bool,
    owner_user_id: Optional[int],
    owner_username: Optional[str],
    job_id: str,
    mapping_override: Optional[dict],
    run_csv_import_inline: Callable,
    get_logger: Callable,
) -> None:
    def _runner():
        try:
            run_csv_import_inline(
                filepath=filepath,
                quantity_mode=quantity_mode,
                overwrite=overwrite,
                owner_user_id=owner_user_id,
                owner_username=owner_username,
                job_id=job_id,
                mapping_override=mapping_override,
            )
        except Exception:
            get_logger().exception("Async import failed", extra={"job_id": job_id})

    thread = threading.Thread(
        target=_runner,
        name=f"import-{job_id[:8]}",
        daemon=True,
    )
    thread.start()


def enqueue_csv_import(
    filepath: str,
    quantity_mode: str,
    overwrite: bool = False,
    *,
    owner_user_id: Optional[int] = None,
    owner_username: Optional[str] = None,
    mapping_override: Optional[dict] = None,
    run_async: bool = False,
    inline_pref: bool,
    jobs_available: bool,
    validate_import_file: Callable,
    get_logger: Callable,
    get_queue: Callable | None,
    run_csv_import_inline: Callable,
    run_csv_import_job: Callable,
    start_inline_import_thread: Callable,
    has_app_context: Callable,
    current_app,
) -> dict:
    inline_mode = bool(inline_pref) or not jobs_available
    job_id = uuid.uuid4().hex
    validate_import_file(filepath, mapping_override)
    log = get_logger()
    log.info(
        "Import enqueue requested",
        extra={
            "job_id": job_id,
            "inline_mode": inline_mode,
            "quantity_mode": quantity_mode,
            "overwrite": overwrite,
            "filepath": filepath,
            "mapping_override": mapping_override,
            "run_async": run_async,
        },
    )

    if run_async and inline_mode:
        start_inline_import_thread(
            filepath=filepath,
            quantity_mode=quantity_mode,
            overwrite=overwrite,
            owner_user_id=owner_user_id,
            owner_username=owner_username,
            job_id=job_id,
            mapping_override=mapping_override,
        )
        return {"job_id": job_id, "ran_inline": False, "stats": None, "per_folder": None}

    if inline_mode:
        stats, per_folder = run_csv_import_inline(
            filepath=filepath,
            quantity_mode=quantity_mode,
            overwrite=overwrite,
            owner_user_id=owner_user_id,
            owner_username=owner_username,
            job_id=job_id,
            mapping_override=mapping_override,
        )
        return {"job_id": job_id, "ran_inline": True, "stats": stats, "per_folder": per_folder}

    queue = get_queue() if get_queue else None
    if queue is None:
        if has_app_context():
            current_app.logger.warning("Queue unavailable; running import inline.")
        if run_async:
            start_inline_import_thread(
                filepath=filepath,
                quantity_mode=quantity_mode,
                overwrite=overwrite,
                owner_user_id=owner_user_id,
                owner_username=owner_username,
                job_id=job_id,
                mapping_override=mapping_override,
            )
            return {"job_id": job_id, "ran_inline": False, "stats": None, "per_folder": None}
        stats, per_folder = run_csv_import_inline(
            filepath=filepath,
            quantity_mode=quantity_mode,
            overwrite=overwrite,
            owner_user_id=owner_user_id,
            owner_username=owner_username,
            job_id=job_id,
            mapping_override=mapping_override,
        )
        return {"job_id": job_id, "ran_inline": True, "stats": stats, "per_folder": per_folder}

    try:
        queue.enqueue(
            run_csv_import_job,
            filepath,
            quantity_mode,
            overwrite,
            job_id,
            owner_user_id,
            owner_username,
            mapping_override,
            job_id=f"import-{job_id}",
            description=f"csv-import:{os.path.basename(filepath)}",
        )
        return {"job_id": job_id, "ran_inline": False, "stats": None, "per_folder": None}
    except Exception as exc:  # pragma: no cover - depends on external redis availability
        if has_app_context():
            current_app.logger.warning("Queue unavailable; running import inline: %s", exc)
        if run_async:
            start_inline_import_thread(
                filepath=filepath,
                quantity_mode=quantity_mode,
                overwrite=overwrite,
                owner_user_id=owner_user_id,
                owner_username=owner_username,
                job_id=job_id,
                mapping_override=mapping_override,
            )
            return {"job_id": job_id, "ran_inline": False, "stats": None, "per_folder": None}
        stats, per_folder = run_csv_import_inline(
            filepath=filepath,
            quantity_mode=quantity_mode,
            overwrite=overwrite,
            owner_user_id=owner_user_id,
            owner_username=owner_username,
            job_id=job_id,
            mapping_override=mapping_override,
        )
        return {"job_id": job_id, "ran_inline": True, "stats": stats, "per_folder": per_folder}


def run_csv_import_job(
    filepath: str,
    quantity_mode: str,
    overwrite: bool,
    import_job_id: str,
    owner_user_id: Optional[int],
    owner_username: Optional[str],
    mapping_override: Optional[dict] = None,
    *,
    create_app: Callable,
    get_current_job,
    get_logger: Callable,
    process_csv_import: Callable,
    cleanup_temp_file: Callable,
    keep_failed_import_uploads: bool,
):
    app = create_app()
    with app.app_context():
        job = get_current_job() if get_current_job else None
        log = get_logger()
        success = False
        log.info(
            "Import job started",
            extra={
                "job_id": import_job_id,
                "quantity_mode": quantity_mode,
                "overwrite": overwrite,
                "owner_user_id": owner_user_id,
                "owner_username": owner_username,
                "filepath": filepath,
                "mapping_override": mapping_override,
            },
        )
        try:
            process_csv_import(
                filepath=filepath,
                quantity_mode=quantity_mode,
                overwrite=overwrite,
                import_job_id=import_job_id,
                owner_user_id=owner_user_id,
                owner_username=owner_username,
                job_ref=job,
                mapping_override=mapping_override,
            )
            log.info(
                "Import job completed",
                extra={"job_id": import_job_id, "quantity_mode": quantity_mode, "overwrite": overwrite},
            )
            success = True
        finally:
            if success or not keep_failed_import_uploads:
                cleanup_temp_file(filepath, app.logger)


def run_csv_import_inline(
    filepath: str,
    quantity_mode: str,
    overwrite: bool,
    *,
    owner_user_id: Optional[int] = None,
    owner_username: Optional[str] = None,
    job_id: Optional[str] = None,
    mapping_override: Optional[dict] = None,
    has_app_context: Callable,
    current_app,
    create_app: Callable,
    process_csv_import: Callable,
    cleanup_temp_file: Callable,
    keep_failed_import_uploads: bool,
):
    job_id = job_id or f"inline-{uuid.uuid4().hex[:8]}"
    if has_app_context():
        ctx = nullcontext()
        app_logger = current_app.logger
    else:
        app = create_app()
        ctx = app.app_context()
        app_logger = app.logger
    with ctx:
        success = False
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
                    "mapping_override": mapping_override,
                },
            )
            stats, per_folder = process_csv_import(
                filepath=filepath,
                quantity_mode=quantity_mode,
                overwrite=overwrite,
                import_job_id=job_id,
                owner_user_id=owner_user_id,
                owner_username=owner_username,
                job_ref=None,
                mapping_override=mapping_override,
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
            success = True
            return stats, per_folder
        finally:
            if success or not keep_failed_import_uploads:
                cleanup_temp_file(filepath, app_logger)


def process_csv_import(
    *,
    filepath: str,
    quantity_mode: str,
    overwrite: bool,
    import_job_id: str,
    owner_user_id: Optional[int],
    owner_username: Optional[str],
    job_ref,
    mapping_override: Optional[dict] = None,
    emit_job_event: Callable,
    run_csv_import: Callable,
    header_validation_error_cls,
    file_validation_error_cls,
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
    try:
        result = run_csv_import(
            filepath=filepath,
            quantity_mode=quantity_mode,
            overwrite=overwrite,
            import_job_id=import_job_id,
            owner_user_id=owner_user_id,
            owner_username=owner_username,
            mapping_override=mapping_override,
        )
        stats = result.get("stats")
        per_folder = result.get("per_folder")
        summary = result.get("summary") or {}
        emit_job_event(
            "import",
            "completed",
            job_id=summary.get("job_id") or getattr(stats, "job_id", None),
            added=summary.get("added") if summary else getattr(stats, "added", None),
            updated=summary.get("updated") if summary else getattr(stats, "updated", None),
            skipped=summary.get("skipped") if summary else getattr(stats, "skipped", None),
            errors=summary.get("errors") if summary else getattr(stats, "errors", None),
            removed_folders=summary.get("removed_folders", 0),
            user_id=owner_user_id,
        )
        return stats, per_folder
    except (header_validation_error_cls, file_validation_error_cls) as exc:
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


def cleanup_temp_file(filepath: str, logger) -> None:
    if filepath and os.path.exists(filepath):
        try:
            os.remove(filepath)
        except Exception:
            logger.warning("Failed to remove temp import file %s", filepath, exc_info=True)
