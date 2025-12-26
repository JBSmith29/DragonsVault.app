"""Background CSV import routines."""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.exc import SQLAlchemyError

from extensions import db
from services.csv_importer import process_csv
from services.import_helpers import (
    delete_empty_folders,
    purge_cards_preserve_commanders,
    restore_commander_metadata,
)

_LOG = logging.getLogger(__name__)


def run_csv_import(
    *,
    filepath: str,
    quantity_mode: str,
    overwrite: bool,
    import_job_id: str,
    owner_user_id: Optional[int],
    owner_username: Optional[str],
) -> dict:
    """Run a CSV import and return a summary payload."""
    _LOG.info(
        "CSV import started: file=%s mode=%s overwrite=%s",
        filepath,
        quantity_mode,
        overwrite,
    )
    preserved: Optional[dict] = None
    removed = 0
    try:
        if overwrite or quantity_mode in {"absolute", "purge"}:
            preserved = purge_cards_preserve_commanders()
        stats, per_folder = process_csv(
            filepath,
            default_folder="Unsorted",
            dry_run=False,
            quantity_mode=quantity_mode,
            job_id=import_job_id,
            owner_user_id=owner_user_id,
            owner_username=owner_username,
        )
        if preserved:
            restore_commander_metadata(preserved)
            removed = delete_empty_folders()
    except SQLAlchemyError:
        db.session.rollback()
        _LOG.error("CSV import failed due to database error.", exc_info=True)
        raise
    except Exception:
        db.session.rollback()
        _LOG.error("CSV import failed unexpectedly.", exc_info=True)
        raise

    summary = {
        "job_id": stats.job_id,
        "added": stats.added,
        "updated": stats.updated,
        "skipped": stats.skipped,
        "errors": stats.errors,
        "removed_folders": removed,
    }
    if stats.errors or stats.skipped_details:
        _LOG.warning(
            "CSV import completed with issues: added=%s updated=%s skipped=%s errors=%s",
            stats.added,
            stats.updated,
            stats.skipped,
            stats.errors,
        )
    _LOG.info(
        "CSV import completed: added=%s updated=%s skipped=%s errors=%s",
        stats.added,
        stats.updated,
        stats.skipped,
        stats.errors,
    )
    return {"stats": stats, "per_folder": per_folder, "summary": summary}
