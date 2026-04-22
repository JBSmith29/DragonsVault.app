"""Legacy runtime schema repair and SQLite recovery helpers."""

from __future__ import annotations

import logging
import shutil
import sqlite3
from pathlib import Path

from flask import Flask
from sqlalchemy import inspect, text
from sqlalchemy.engine.url import make_url

from core.shared.utils.time import utcnow
from extensions import db


def _engine_or_none():
    try:
        return db.engine
    except Exception:
        return None


def _inspector_or_none(engine):
    try:
        return inspect(engine)
    except Exception:
        return None


def _ensure_folder_deck_tag_column() -> None:
    """Ensure legacy databases gain the deck_tag column/index without Alembic."""
    engine = _engine_or_none()
    if engine is None:
        return

    inspector = _inspector_or_none(engine)
    if inspector is None:
        return

    try:
        columns = {col["name"] for col in inspector.get_columns("folder")}
    except Exception:
        return

    if "deck_tag" not in columns:
        try:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE folder ADD COLUMN deck_tag VARCHAR(120)"))
        except Exception:
            return
        inspector = _inspector_or_none(engine)
        if inspector is None:
            return

    try:
        indexes = {idx["name"] for idx in inspector.get_indexes("folder")}
    except Exception:
        indexes = set()

    if "ix_folder_deck_tag" not in indexes:
        try:
            with engine.begin() as conn:
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_folder_deck_tag ON folder (deck_tag)"))
        except Exception:
            pass


def _ensure_folder_owner_user_column() -> None:
    """Add the owner_user_id column if it does not exist (legacy DBs)."""
    engine = _engine_or_none()
    if engine is None:
        return

    inspector = _inspector_or_none(engine)
    if inspector is None:
        return

    try:
        columns = {col["name"] for col in inspector.get_columns("folder")}
    except Exception:
        return

    if "owner_user_id" in columns:
        return

    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE folder ADD COLUMN owner_user_id INTEGER"))
    except Exception as exc:
        logging.getLogger(__name__).warning("Unable to add owner_user_id column automatically: %s", exc)


def _ensure_folder_notes_column() -> None:
    """Add the notes column if it is missing."""
    engine = _engine_or_none()
    if engine is None:
        return

    inspector = _inspector_or_none(engine)
    if inspector is None:
        return

    try:
        columns = {col["name"] for col in inspector.get_columns("folder")}
    except Exception:
        return

    if "notes" in columns:
        return

    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE folder ADD COLUMN notes TEXT"))
    except Exception as exc:
        logging.getLogger(__name__).warning("Unable to add notes column automatically: %s", exc)


def _ensure_folder_sleeve_color_column() -> None:
    """Add the sleeve_color column if it is missing."""
    engine = _engine_or_none()
    if engine is None:
        return

    inspector = _inspector_or_none(engine)
    if inspector is None:
        return

    try:
        columns = {col["name"] for col in inspector.get_columns("folder")}
    except Exception:
        return

    if "sleeve_color" in columns:
        return

    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE folder ADD COLUMN sleeve_color VARCHAR(64)"))
    except Exception as exc:
        logging.getLogger(__name__).warning("Unable to add sleeve_color column automatically: %s", exc)


def _ensure_folder_sharing_columns(*, fallback_enabled: bool) -> None:
    """Add sharing-related columns to folder if missing."""
    if not fallback_enabled:
        return

    engine = _engine_or_none()
    if engine is None:
        return

    inspector = _inspector_or_none(engine)
    if inspector is None:
        return

    try:
        columns = {col["name"] for col in inspector.get_columns("folder")}
    except Exception:
        return

    additions: list[tuple[str, str]] = []
    if "is_public" not in columns:
        additions.append(("is_public", "INTEGER NOT NULL DEFAULT 0"))
    if "share_token" not in columns:
        additions.append(("share_token", "VARCHAR(128)"))
    if "share_token_hash" not in columns:
        additions.append(("share_token_hash", "VARCHAR(64)"))

    for name, ddl in additions:
        try:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE folder ADD COLUMN {name} {ddl}"))
        except Exception as exc:
            logging.getLogger(__name__).warning("Unable to add %s column automatically: %s", name, exc)


def _ensure_folder_share_table(*, fallback_enabled: bool) -> None:
    """Create folder_share table for per-user sharing if it is missing."""
    if not fallback_enabled:
        return

    engine = _engine_or_none()
    if engine is None:
        return

    inspector = _inspector_or_none(engine)
    if inspector is None:
        return

    try:
        tables = set(inspector.get_table_names())
    except Exception:
        return

    if "folder_share" in tables:
        return

    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS folder_share (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        folder_id INTEGER NOT NULL REFERENCES folder(id) ON DELETE CASCADE,
                        shared_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
            )
            conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_folder_share_unique ON folder_share(folder_id, shared_user_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_folder_share_user ON folder_share(shared_user_id)"))
    except Exception as exc:
        logging.getLogger(__name__).warning("Unable to create folder_share table: %s", exc)


def _ensure_card_metadata_columns() -> None:
    """Backfill derived card metadata columns for legacy databases."""
    engine = _engine_or_none()
    if engine is None:
        return

    inspector = _inspector_or_none(engine)
    if inspector is None:
        return

    try:
        columns = {col["name"] for col in inspector.get_columns("cards")}
    except Exception:
        return

    missing: list[tuple[str, str]] = []
    if "type_line" not in columns:
        missing.append(("type_line", "TEXT"))
    if "rarity" not in columns:
        missing.append(("rarity", "VARCHAR(16)"))
    if "color_identity" not in columns:
        missing.append(("color_identity", "VARCHAR(8)"))
    if "color_identity_mask" not in columns:
        missing.append(("color_identity_mask", "INTEGER"))

    if not missing:
        return

    try:
        with engine.begin() as conn:
            for name, ddl in missing:
                conn.execute(text(f"ALTER TABLE cards ADD COLUMN {name} {ddl}"))
    except Exception:
        engine.logger.error("Failed to add card metadata columns: %s", missing, exc_info=True)


def _ensure_wishlist_columns() -> None:
    """Ensure wishlist table has auxiliary columns required by newer features."""
    engine = _engine_or_none()
    if engine is None:
        return

    inspector = _inspector_or_none(engine)
    if inspector is None:
        return

    try:
        columns = {col["name"] for col in inspector.get_columns("wishlist_items")}
    except Exception:
        return

    missing: list[tuple[str, str]] = []
    if "source_folders" not in columns:
        missing.append(("source_folders", "TEXT"))
    if "order_ref" not in columns:
        missing.append(("order_ref", "TEXT"))

    if not missing:
        return

    try:
        with engine.begin() as conn:
            for name, ddl in missing:
                conn.execute(text(f"ALTER TABLE wishlist_items ADD COLUMN {name} {ddl}"))
    except Exception:
        engine.logger.error("Failed to add wishlist columns: %s", missing, exc_info=True)


def _quarantine_sqlite_file(app: Flask, db_path: Path, exc: Exception) -> None:
    """Move an unreadable SQLite database aside so a fresh one can be created."""
    timestamp = utcnow().strftime("%Y%m%d-%H%M%S")
    backup = db_path.with_name(f"{db_path.name}.corrupt-{timestamp}")
    try:
        shutil.move(str(db_path), str(backup))
        for suffix in ("-wal", "-shm"):
            sidecar = db_path.with_name(db_path.name + suffix)
            if sidecar.exists():
                shutil.move(str(sidecar), str(backup.with_name(backup.name + suffix)))
        app.logger.error(
            "SQLite database at %s was invalid (%s). Moved to %s and will recreate a new database.",
            db_path,
            exc,
            backup,
        )
    except Exception as move_exc:
        app.logger.error("Unable to recover corrupt SQLite database at %s: %s", db_path, move_exc)
        raise


def validate_sqlite_database(app: Flask) -> None:
    """
    Ensure the configured SQLite file can be opened.

    If the file is corrupt (common when a previous crash leaves a truncated file
    or a cache volume is mounted incorrectly), we back it up and allow the app
    to recreate a fresh database instead of raising a cryptic `file is not a database`
    error during startup.
    """
    uri = app.config.get("SQLALCHEMY_DATABASE_URI")
    if not uri:
        return
    try:
        url = make_url(uri)
    except Exception:
        return
    if url.get_backend_name() != "sqlite":
        return

    db_path = Path(url.database or "")
    if not db_path.is_absolute():
        db_path = Path(app.instance_path) / db_path
    db_path = db_path.resolve()

    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        app.logger.error("Unable to ensure SQLite directory %s: %s", db_path.parent, exc)
        raise

    try:
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA schema_version;")
        conn.close()
    except sqlite3.OperationalError as exc:
        message = str(exc).lower()
        if "unable to open database file" in message:
            try:
                conn = sqlite3.connect(db_path)
                conn.execute("PRAGMA schema_version;")
                conn.close()
                return
            except sqlite3.Error as inner:
                app.logger.error("SQLite database at %s could not be opened: %s", db_path, inner)
                raise
        if "disk i/o error" in message:
            _quarantine_sqlite_file(app, db_path, exc)
            return
        raise
    except sqlite3.DatabaseError as exc:
        _quarantine_sqlite_file(app, db_path, exc)


def ensure_runtime_schema_fallbacks(app: Flask, *, fallback_enabled: bool) -> None:
    """Repair legacy runtime schema gaps when startup fallback mode is enabled."""
    if fallback_enabled and (app.debug or app.config.get("ALLOW_RUNTIME_INDEX_BOOTSTRAP")):
        db.create_all()
        _ensure_folder_deck_tag_column()
        _ensure_folder_owner_user_column()
        _ensure_card_metadata_columns()

    if fallback_enabled:
        _ensure_folder_notes_column()
        _ensure_folder_sleeve_color_column()
        _ensure_folder_sharing_columns(fallback_enabled=fallback_enabled)
        _ensure_folder_share_table(fallback_enabled=fallback_enabled)
        _ensure_wishlist_columns()

    if not fallback_enabled:
        return

    try:
        inspector = inspect(db.engine)
        existing_tables = set(inspector.get_table_names())
        required_role_tables = {"roles", "sub_roles", "card_roles", "card_subroles"}
        if required_role_tables - existing_tables:
            db.create_all()
    except Exception as exc:  # pragma: no cover - defensive bootstrapping
        app.logger.warning("Role table bootstrap skipped: %s", exc)

