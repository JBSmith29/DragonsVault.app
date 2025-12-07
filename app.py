"""Flask application factory, CLI entry points, and database bootstrap."""

import json
import logging
from logging.handlers import RotatingFileHandler
import os
import shutil
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

import click
from flask import Flask, render_template, g, has_request_context, request, flash, redirect, url_for, jsonify, session
from flask_compress import Compress
try:
    from flask_talisman import Talisman  # type: ignore
    _talisman_available = True
except ImportError:  # pragma: no cover - optional dependency
    Talisman = None  # type: ignore
    _talisman_available = False
from jinja2.bccache import FileSystemBytecodeCache
from sqlalchemy import event, func, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.engine.url import make_url
from sqlalchemy.orm import with_loader_criteria

from dotenv import load_dotenv; load_dotenv()

from config import Config, INSTANCE_DIR as CONFIG_INSTANCE_DIR
from extensions import db, migrate, cache, csrf, limiter, login_manager, generate_csrf
from flask_login import current_user

# Scryfall helpers
from services import scryfall_cache as sc
from services.scryfall_cache import (
    ensure_cache_loaded,
    cache_exists, load_cache, find_by_set_cn,
    candidates_by_set_and_name, find_by_set_cn_loose,
    normalize_set_code, unique_oracle_by_name,
)
from services.deck_utils import BASIC_LANDS

# FTS helpers
from services.fts import ensure_fts, reindex_fts

# Optional compat shim (ignore if missing)
try:
    import services.scryfall_cache_compat  # noqa: F401
except Exception:
    pass

def _fallback_enabled() -> bool:
    """Guard legacy schema fallbacks; disabled unless explicitly opted-in."""
    return os.getenv("ENABLE_ROLE_TABLE_FALLBACK", "0").lower() in {"1", "true", "yes", "on"}


@click.command("seed-roles")
def seed_roles():
    from seeds.seed_roles import seed_roles_and_subroles

    seed_roles_and_subroles()


class RequestIdFilter(logging.Filter):
    """Inject request-scoped metadata into log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        if has_request_context():
            record.request_id = getattr(g, "request_id", "n/a")
            record.path = request.path
            record.method = request.method
        else:
            record.request_id = getattr(record, "request_id", "startup")
            record.path = getattr(record, "path", "")
            record.method = getattr(record, "method", "")
        return True


class JsonRequestFormatter(logging.Formatter):
    """Simple JSON formatter for logfmt-friendly ingestion."""

    def format(self, record: logging.LogRecord) -> str:
        base = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "n/a"),
            "path": getattr(record, "path", ""),
            "method": getattr(record, "method", ""),
        }
        if record.exc_info:
            base["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(base, ensure_ascii=False)


_visibility_filters_registered = False


def _register_visibility_filters(card_model, folder_model) -> None:
    """Apply per-request visibility rules so users only see their own folders/cards."""
    global _visibility_filters_registered
    if _visibility_filters_registered:
        return

    @event.listens_for(db.session, "do_orm_execute")
    def _apply_folder_visibility(execute_state):
        if not execute_state.is_select:
            return
        if not has_request_context():
            return
        user_id = session.get("_user_id")
        if not user_id:
            return
        try:
            user_id_int = int(user_id)
        except (TypeError, ValueError):
            return
        execute_state.statement = execute_state.statement.options(
            with_loader_criteria(
                folder_model,
                lambda cls: cls.owner_user_id == user_id_int,
                include_aliases=True,
            ),
            with_loader_criteria(
                card_model,
                lambda cls: cls.folder.has(folder_model.owner_user_id == user_id_int),
                include_aliases=True,
            ),
        )

    _visibility_filters_registered = True


def _configure_login_manager(app: Flask) -> None:
    """Bind Flask-Login and support API token authentication."""
    login_manager.init_app(app)
    login_manager.login_view = "views.login"
    login_manager.login_message_category = "warning"
    login_manager.session_protection = "strong"

    def _extract_token(req):
        auth_header = req.headers.get("Authorization", "")
        if auth_header.lower().startswith("bearer "):
            return auth_header.split(" ", 1)[1].strip()
        return None

    @login_manager.user_loader
    def _load_user(user_id: str):
        from models import User

        try:
            return User.query.get(int(user_id))
        except (TypeError, ValueError):
            return None

    @login_manager.request_loader
    def _load_user_from_request(req):
        from models import User

        token = _extract_token(req)
        if not token:
            return None
        return User.verify_api_token(token)

    @login_manager.unauthorized_handler
    def _unauthorized():
        accepts_json = request.accept_mimetypes["application/json"] >= request.accept_mimetypes["text/html"]
        if request.path.startswith("/api/") or accepts_json:
            return jsonify({"error": "authentication_required"}), 401
        flash("Please sign in to continue.", "warning")
        return redirect(url_for("views.login", next=request.url))


# ---------------------------------------------------------------------------
# Extension bootstrap helpers
# ---------------------------------------------------------------------------

def _safe_init_sqlalchemy(app: Flask):
    """Initialise SQLAlchemy only if it has not been bound yet."""
    if not getattr(app, "extensions", None) or "sqlalchemy" not in app.extensions:
        db.init_app(app)


def _safe_init_migrate(app: Flask):
    """Bind Flask-Migrate safely, falling back to batch mode for SQLite."""
    try:
        if not getattr(app, "extensions", None) or "migrate" not in app.extensions:
            migrate.init_app(app, db, render_as_batch=True)
    except Exception:
        migrate.init_app(app, db, render_as_batch=True)


def _safe_init_cache(app: Flask):
    """Register the cache extension while swallowing optional dependencies."""
    try:
        cache.init_app(app)
    except Exception as exc:
        app.logger.warning("Primary cache init failed (%s); falling back to SimpleCache.", exc)
        fallback_cfg = {
            "CACHE_TYPE": "SimpleCache",
            "CACHE_DEFAULT_TIMEOUT": 600,
        }
        try:
            cache.init_app(app, config=fallback_cfg)
        except Exception:
            app.logger.exception("Cache fallback failed; aborting startup.")
            raise


def _configure_logging(app: Flask) -> None:
    """Configure structured logging with request IDs."""
    stream_handler = logging.StreamHandler()
    stream_handler.addFilter(RequestIdFilter())
    stream_handler.setFormatter(JsonRequestFormatter())
    stream_handler.setLevel(logging.INFO)

    handlers = [stream_handler]

    try:
        logs_dir = Path(app.instance_path) / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            logs_dir / "app.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.addFilter(RequestIdFilter())
        file_handler.setFormatter(JsonRequestFormatter())
        file_handler.setLevel(logging.INFO)
        handlers.append(file_handler)
    except Exception as exc:
        app.logger.warning("Falling back to stream-only logging (file handler unavailable): %s", exc)

    app.logger.handlers = handlers
    app.logger.setLevel(logging.INFO)
    logging.getLogger("werkzeug").handlers = handlers
    logging.getLogger("werkzeug").setLevel(logging.INFO)


def _ensure_folder_deck_tag_column():
    """Ensure legacy databases gain the deck_tag column/index without Alembic."""
    try:
        engine = db.engine
    except Exception:
        return

    try:
        inspector = inspect(engine)
    except Exception:
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
        try:
            inspector = inspect(engine)
        except Exception:
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


def _ensure_folder_owner_user_column():
    """Add the owner_user_id column if it does not exist (legacy DBs)."""
    try:
        engine = db.engine
    except Exception:
        return

    try:
        inspector = inspect(engine)
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


def _ensure_folder_notes_column():
    """Add the notes column if it is missing."""
    try:
        engine = db.engine
    except Exception:
        return
    try:
        inspector = inspect(engine)
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


def _ensure_folder_sharing_columns():
    """Add sharing-related columns to folder if missing."""
    if not _fallback_enabled():
        return
    try:
        engine = db.engine
    except Exception:
        return

    try:
        inspector = inspect(engine)
        columns = {col["name"] for col in inspector.get_columns("folder")}
    except Exception:
        return

    adds: list[tuple[str, str]] = []
    if "is_public" not in columns:
        adds.append(("is_public", "INTEGER NOT NULL DEFAULT 0"))
    if "share_token" not in columns:
        adds.append(("share_token", "VARCHAR(128)"))
    if "share_token_hash" not in columns:
        adds.append(("share_token_hash", "VARCHAR(64)"))

    if not adds:
        return

    for name, ddl in adds:
        try:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE folder ADD COLUMN {name} {ddl}"))
        except Exception as exc:
            logging.getLogger(__name__).warning("Unable to add %s column automatically: %s", name, exc)


def _ensure_folder_share_table():
    """Create folder_share table for per-user sharing if it is missing."""
    if not _fallback_enabled():
        return
    try:
        engine = db.engine
    except Exception:
        return

    try:
        inspector = inspect(engine)
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


def _ensure_card_metadata_columns():
    """Backfill derived card metadata columns for legacy databases."""
    try:
        engine = db.engine
    except Exception:
        return

    try:
        inspector = inspect(engine)
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
        # Log but do not raise; app can still function with guards.
        engine.logger.error("Failed to add card metadata columns: %s", missing, exc_info=True)


def _ensure_wishlist_columns():
    """Ensure wishlist table has auxiliary columns required by newer features."""
    try:
        engine = db.engine
        inspector = inspect(engine)
        columns = {col["name"] for col in inspector.get_columns("wishlist_items")}
    except Exception:
        return

    missing = []
    if "source_folders" not in columns:
        missing.append(("source_folders", "TEXT"))

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
    timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
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


def _validate_sqlite_database(app: Flask) -> None:
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


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------

def create_app():
    """Create, configure, and return a fully-initialised Flask app."""
    # Use instance_relative_config so ./instance is writable
    app = Flask(
        __name__,
        instance_path=str(CONFIG_INSTANCE_DIR),
        instance_relative_config=False,
    )
    app.config.from_object(Config)
    _configure_logging(app)

    # Ensure instance subdirs exist
    os.makedirs(app.instance_path, exist_ok=True)
    data_dir = os.path.join(app.instance_path, "data")
    uploads_dir = os.path.join(app.instance_path, "uploads")
    cache_dir = os.path.join(app.instance_path, "cache")
    jinja_cache_dir = os.path.join(app.instance_path, "jinja_cache")
    os.makedirs(data_dir, exist_ok=True)
    os.makedirs(uploads_dir, exist_ok=True)
    os.makedirs(cache_dir, exist_ok=True)
    os.makedirs(jinja_cache_dir, exist_ok=True)

    # Defaults (overridable via env/Config)
    app.config.setdefault("SCRYFALL_DATA_DIR", data_dir)
    app.config.setdefault("UPLOAD_FOLDER", uploads_dir)
    app.config.setdefault("SQLALCHEMY_TRACK_MODIFICATIONS", False)
    app.config.setdefault("CACHE_TYPE", os.getenv("CACHE_TYPE", "SimpleCache"))
    app.config.setdefault(
        "CACHE_DEFAULT_TIMEOUT", int(os.getenv("CACHE_DEFAULT_TIMEOUT", "600"))
    )
    app.config.setdefault("CACHE_REDIS_URL", os.getenv("CACHE_REDIS_URL"))
    app.config.setdefault("CACHE_DIR", os.getenv("CACHE_DIR", cache_dir))
    app.config.setdefault("RATELIMIT_DEFAULT", "200 per minute")
    app.config.setdefault("RATELIMIT_STORAGE_URI", "memory://")
    app.config.setdefault("SCRYFALL_OFFLINE_FIRST", True)

    # If no DB URI provided, store SQLite DB in instance/
    if not app.config.get("SQLALCHEMY_DATABASE_URI"):
        app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{os.path.join(app.instance_path, 'database.db')}"

    # --- Core extensions ---
    _safe_init_sqlalchemy(app)
    _safe_init_migrate(app)
    _safe_init_cache(app)
    _configure_login_manager(app)
    csrf.init_app(app)
    app.jinja_env.globals["csrf_token"] = generate_csrf
    Compress(app)

    @app.before_request
    def _reject_querystring_api_token():
        if "api_token" not in request.args:
            return
        detail = "API tokens must be sent using the Authorization: Bearer header; query parameters are not accepted."
        wants_json = request.path.startswith("/api/") or request.headers.get("HX-Request") or (
            request.accept_mimetypes["application/json"] >= request.accept_mimetypes["text/html"]
        )
        payload = {"error": "api_token_query_not_supported", "detail": detail}
        if wants_json:
            return jsonify(payload), 400
        return detail, 400

    public_endpoints = {
        "views.landing_page",
        "views.login",
        "views.register",
        "views.terms_of_service",
        "views.privacy_policy",
        "views.accessibility_statement",
        "views.legal_disclaimer",
        "views.coppa_notice",
        "views.cookie_policy",
        "views.terms_of_use",
        "views.shipping_policy",
        "views.returns_policy",
        "views.do_not_sell",
        "views.do_not_share",
        "views.about_page",
        "views.contact_page",
        "static",
    }

    @app.before_request
    def require_authentication():
        if current_user.is_authenticated:
            # If a prior login requested a full refresh (to update sidebar/admin links), clear the flag.
            if session.pop("force_full_refresh", False):
                # For HX requests, ask the client to hard-redirect; for normal requests, just continue.
                if request.headers.get("HX-Request"):
                    return jsonify({"redirect": request.path}), 200, {"HX-Redirect": request.path}
            return

        endpoint = request.endpoint or ""
        if endpoint in public_endpoints:
            return

        path = request.path or ""
        if path.startswith("/static/") or path.startswith("/favicon"):
            return

        if request.method == "OPTIONS":
            return

        next_url = request.full_path or path or "/"
        return redirect(url_for("views.landing_page", next=next_url))
    default_limits = app.config.get("RATELIMIT_DEFAULT")
    if isinstance(default_limits, str):
        default_limits = [default_limits]
    if limiter:
        limiter_kwargs = {}
        storage_uri = app.config.get("RATELIMIT_STORAGE_URI")
        if storage_uri:
            limiter_kwargs["storage_uri"] = storage_uri
        if default_limits:
            limiter_kwargs["default_limits"] = default_limits
        try:
            limiter.init_app(app, **limiter_kwargs)
        except TypeError:
            # Older Flask-Limiter builds don't accept default_limits.
            limiter_kwargs.pop("default_limits", None)
            try:
                limiter.init_app(app, **limiter_kwargs)
            except TypeError:
                limiter_kwargs.pop("storage_uri", None)
                limiter.init_app(app, **limiter_kwargs)
    else:
        app.logger.warning("Flask-Limiter not installed; rate limiting disabled.")

    if _talisman_available and app.config.get("ENABLE_TALISMAN", True):
        csp = app.config.get("CONTENT_SECURITY_POLICY")
        Talisman(
            app,
            content_security_policy=csp,
            force_https=app.config.get("TALISMAN_FORCE_HTTPS", not app.debug),
            session_cookie_secure=app.config.get("SESSION_COOKIE_SECURE", True),
            session_cookie_samesite=app.config.get("SESSION_COOKIE_SAMESITE", "Lax"),
        )
    elif not _talisman_available and app.config.get("ENABLE_TALISMAN", True):
        app.logger.warning("Flask-Talisman not installed; CSP/HSTS disabled.")

    # --- Jinja bytecode cache (safe on Windows) ---
    try:
        app.jinja_env.bytecode_cache = FileSystemBytecodeCache(
            directory=jinja_cache_dir,
            pattern="dv-%s.cache",
        )
    except Exception as e:
        app.logger.warning(f"Jinja bytecode cache disabled: {e}")
        app.jinja_env.bytecode_cache = None

    app.config["TEMPLATES_AUTO_RELOAD"] = bool(app.debug)

    fallback = _fallback_enabled()

    # Warm caches, create tables, ensure FTS
    with app.app_context():
        _validate_sqlite_database(app)
        try:
            ensure_cache_loaded()
        except Exception as e:
            app.logger.warning("Scryfall cache not loaded at init: %s", e)

        # Import models after db is bound
        from models import Card, Folder, WishlistItem  # noqa: F401
        _register_visibility_filters(Card, Folder)

        if fallback and (app.debug or app.config.get("ALLOW_RUNTIME_INDEX_BOOTSTRAP")):
            # Create DB objects if missing (safe even with Alembic)
            db.create_all()
            _ensure_folder_deck_tag_column()
            _ensure_folder_owner_user_column()
            _ensure_card_metadata_columns()
        # Ensure legacy columns exist even outside bootstrap
        if fallback:
            _ensure_folder_notes_column()
            _ensure_folder_sharing_columns()
            _ensure_folder_share_table()
            _ensure_wishlist_columns()

        # Optional legacy safety net (disabled by default).
        if fallback:
            try:
                inspector = inspect(db.engine)
                existing_tables = set(inspector.get_table_names())
                required_role_tables = {"roles", "sub_roles", "card_roles", "card_subroles"}
                if required_role_tables - existing_tables:
                    db.create_all()
            except Exception as e:  # pragma: no cover - defensive bootstrapping
                app.logger.warning("Role table bootstrap skipped: %s", e)

        # Ensure FTS5 virtual table + triggers exist (fixes the cards_fts error)
        ensure_fts()

    # Blueprints
    from routes import views
    app.register_blueprint(views)

    @app.context_processor
    def inject_device_type():
        user_agent = (request.headers.get("User-Agent") or "").lower()
        return {"is_mobile": "mobi" in user_agent}

    @app.context_processor
    def inject_basic_lands():
        return {"BASIC_LANDS": BASIC_LANDS}

    # ------------------------------------------------------------------
    # CLI COMMANDS
    # ------------------------------------------------------------------
    from models import Card, Folder, User  # local scope for CLI
    from services.csv_importer import process_csv, HeaderValidationError
    from services.spellbook_sync import (
        EARLY_MANA_VALUE_THRESHOLD,
        LATE_MANA_VALUE_THRESHOLD,
        generate_spellbook_combo_dataset,
        write_dataset_to_file,
    )

    @app.cli.command("import-csv")
    @click.argument("filepath")
    @click.option("--dry-run", is_flag=True, help="Preview only; no DB changes.")
    @click.option("--default-folder", default="Unsorted", show_default=True,
                  help="Folder to use when file lacks a folder column.")
    @click.option("--overwrite", is_flag=True,
                  help="Delete ALL cards first (keep folders/commanders), then import.")
    @click.option("--quantity-mode", type=click.Choice(["delta", "new_only"]), default="delta", show_default=True,
                  help="delta: add to existing totals; new_only: create only brand-new rows.")
    def import_csv_cmd(filepath, dry_run, default_folder, overwrite, quantity_mode):
        """Import CSV or Excel file (xlsx/xlsm supported)."""
        p = Path(filepath).expanduser()
        if not p.is_absolute():
            p = Path(app.root_path) / p
        p = p.resolve()
        if not p.exists():
            raise click.ClickException(f"File not found: {p}")

        preserved = None
        should_reset = not dry_run and (overwrite or quantity_mode == "absolute")
        if should_reset:
            click.echo("Clearing existing cards before import...")
            preserved = purge_cards_preserve_commanders()

        try:
            stats, per_folder = process_csv(
                str(p),
                default_folder=default_folder,
                dry_run=dry_run,
                quantity_mode=quantity_mode,
            )
        except HeaderValidationError as exc:
            raise click.ClickException(str(exc)) from exc

        if preserved:
            restore_commander_metadata(preserved)
            removed = delete_empty_folders()
            click.echo(f"Restored commander metadata; removed {removed} empty folder(s).")

        click.echo(
            f"Added {stats.added}, Updated {stats.updated}, "
            f"Skipped {stats.skipped}, Errors {stats.errors}"
        )
        if per_folder:
            top = ", ".join(f"{k}:{v}" for k, v in list(per_folder.items())[:10])
            click.echo(f"By folder (first 10): {top}")

    @app.cli.command("rq-worker")
    @click.option("--queue", default="default", show_default=True)
    def rq_worker(queue):
        """Run an RQ worker that processes background jobs."""
        from rq import Worker
        from services.task_queue import get_queue

        q = get_queue(queue)
        worker = Worker([q], connection=q.connection)
        click.echo(f"Starting RQ worker for queue '{queue}'")
        worker.work()

    @app.cli.command("sync-spellbook-combos")
    @click.option(
        "--output",
        default="data/spellbook_combos.json",
        show_default=True,
        help="Destination file for the Commander Spellbook combo dataset.",
    )
    @click.option(
        "--early-threshold",
        default=EARLY_MANA_VALUE_THRESHOLD,
        type=int,
        show_default=True,
        help="Maximum mana value needed to treat a combo as early-game.",
    )
    @click.option(
        "--late-threshold",
        default=LATE_MANA_VALUE_THRESHOLD,
        type=int,
        show_default=True,
        help="Minimum mana value needed to treat a combo as late-game.",
    )
    @click.option(
        "--card-count",
        "card_counts",
        type=int,
        multiple=True,
        help="Restrict combos to the given card counts (repeat flag to include multiple). Defaults to 2 and 3 cards.",
    )
    def sync_spellbook_combos(output, early_threshold, late_threshold, card_counts):
        """Download instant-win combos from Commander Spellbook and persist them locally."""

        dataset = generate_spellbook_combo_dataset(
            early_threshold=early_threshold,
            late_threshold=late_threshold,
            card_count_targets=card_counts or (2, 3),
        )

        output_path = Path(output)
        if not output_path.is_absolute():
            output_path = Path(app.root_path) / output_path

        write_dataset_to_file(dataset, output_path)

        click.echo(
            "Commander Spellbook combos synced: "
            f"{len(dataset['early_game'])} early-game, {len(dataset['late_game'])} late-game entries."
        )
        click.echo(f"Dataset written to {output_path}")

    @app.cli.command("fts-ensure")
    def cli_fts_ensure():
        """Create FTS table & triggers if missing."""
        ensure_fts()
        click.echo("FTS ensured.")

    @app.cli.command("fts-reindex")
    def cli_fts_reindex():
        """Rebuild the FTS index from current cards."""
        reindex_fts()
        click.echo("FTS reindexed.")

    @app.cli.command("dedupe-cards")
    def dedupe_cards():
        required = ["lang", "is_foil", "quantity"]
        missing = [f for f in required if not hasattr(Card, f)]
        if missing:
            click.echo(
                "Card model missing: " + ", ".join(missing) +
                "\nEnsure your models are up to date and migrations applied."
            )
            return

        session = db.session
        groups = (
            session.query(
                Card.name, Card.folder_id, Card.set_code, Card.collector_number,
                Card.lang, Card.is_foil, func.count(Card.id).label("cnt")
            )
            .group_by(
                Card.name, Card.folder_id, Card.set_code, Card.collector_number,
                Card.lang, Card.is_foil
            )
            .having(func.count(Card.id) > 1)
            .all()
        )

        total_merged = 0
        for (name, folder_id, set_code, collector_number, lang, is_foil, _cnt) in groups:
            rows = (
                session.query(Card)
                .filter_by(
                    name=name, folder_id=folder_id, set_code=set_code,
                    collector_number=collector_number, lang=lang, is_foil=is_foil
                )
                .order_by(Card.id.asc())
                .all()
            )
            keeper = rows[0]
            keeper.quantity = sum((r.quantity or 1) for r in rows)
            for r in rows[1:]:
                session.delete(r)
            total_merged += (len(rows) - 1)

        session.commit()
        click.echo(f"Merged {total_merged} duplicate rows.")

    @app.cli.command("inspect-oracle-ids")
    def inspect_oracle_ids():
        total = (
            db.session.query(func.count(Card.id))
            .filter(Card.is_proxy.is_(False))
            .scalar()
            or 0
        )
        with_oid = (
            db.session.query(func.count(Card.id))
            .filter(
                Card.oracle_id.isnot(None),
                Card.oracle_id != "",
                Card.is_proxy.is_(False),
            )
            .scalar()
            or 0
        )
        missing = total - with_oid
        click.echo(f"Cards total: {total}")
        click.echo(f"With oracle_id: {with_oid}")
        click.echo(f"Missing oracle_id: {missing}")

    @app.cli.command("backfill-oracle-ids")
    @click.option("--limit", type=int, default=0, help="Limit rows processed (0 = no limit).")
    def backfill_oracle_ids(limit):
        if not (cache_exists() and load_cache()):
            click.echo("No Scryfall bulk cache found. Run: flask fetch-scryfall-bulk")
            return

        q = Card.query.filter((Card.oracle_id == None) | (Card.oracle_id == ""))  # noqa: E711
        if limit and limit > 0:
            q = q.limit(limit)

        scanned = set_count = batch = 0
        for c in q:
            scanned += 1
            found = find_by_set_cn(c.set_code, c.collector_number, c.name)
            if found and found.get("oracle_id"):
                c.oracle_id = found["oracle_id"]
                set_count += 1
                batch += 1
                if batch >= 500:
                    db.session.commit()
                    batch = 0
        db.session.commit()
        click.echo(f"Scanned {scanned} row(s). Set oracle_id on {set_count}.")

    @app.cli.command("refresh-scryfall")
    def refresh_scryfall_cmd():
        if not (cache_exists() and load_cache()):
            click.echo("No local Scryfall cache found. Run: flask fetch-scryfall-bulk")
            return

        missing = Card.query.filter((Card.oracle_id == None) | (Card.oracle_id == "")).all()  # noqa: E711
        fixed = 0
        for c in missing:
            f = find_by_set_cn(c.set_code, c.collector_number, c.name)
            if f and f.get("oracle_id"):
                c.oracle_id = f["oracle_id"]
                fixed += 1
                if fixed % 500 == 0:
                    db.session.flush()
        db.session.commit()
        click.echo(f"Backfilled oracle_id for {fixed} card rows.")

    @app.cli.command("cache-stats")
    @click.option("--json", "as_json", is_flag=True, help="Output raw JSON.")
    def cache_stats_cmd(as_json):
        """Show status of local Scryfall caches (prints + rulings)."""
        import json as _json
        stats = sc.cache_stats()  # {"prints": {...}, "rulings": {...}}

        if as_json:
            click.echo(_json.dumps(stats, indent=2, sort_keys=True))
            return

        prints = stats.get("prints", {}) or {}
        rulings = stats.get("rulings", {}) or {}

        def _hb(n):
            units = ["B","KB","MB","GB","TB"]
            n = int(n or 0)
            i = 0
            while n >= 1024 and i < len(units) - 1:
                n /= 1024.0
                i += 1
            return f"{n:.1f} {units[i]}"

        click.echo("PRINTS (default_cards):")
        click.echo(f"  File: {prints.get('file')}")
        click.echo(f"  Exists: {prints.get('exists')}  "
                   f"Size: {_hb(prints.get('size_bytes'))}  "
                   f"Stale: {prints.get('stale')}")
        click.echo(f"  Records loaded: {prints.get('records')}  "
                   f"Unique sets: {prints.get('unique_sets')}  "
                   f"Unique oracles: {prints.get('unique_oracles')}")
        ix = prints.get("index_sizes", {}) or {}
        click.echo(f"  Index sizes: by_set_cn={ix.get('by_set_cn')}  by_oracle={ix.get('by_oracle')}")
        click.echo("")
        click.echo("RULINGS:")
        click.echo(f"  File: {rulings.get('file')}")
        click.echo(f"  Exists: {rulings.get('exists')}  "
                   f"Size: {_hb(rulings.get('size_bytes'))}  "
                   f"Stale: {rulings.get('stale')}")
        click.echo(f"  Entries loaded: {rulings.get('entries')}  "
                   f"Oracle keys: {rulings.get('oracle_keys')}")

    @app.cli.command("diagnose-missing-oracle")
    def diagnose_missing_oracle():
        """Print rows missing oracle_id and show likely Scryfall candidates."""
        if not (cache_exists() and load_cache()):
            click.echo("No Scryfall bulk cache found. Run: flask --app app:create_app fetch-scryfall-bulk")
            return

        rows = (
            db.session.query(Card)
            .filter((Card.oracle_id == None) | (Card.oracle_id == ""))  # noqa: E711
            .order_by(Card.set_code.asc(), Card.collector_number.asc(), Card.name.asc())
            .all()
        )
        if not rows:
            click.echo("All cards have oracle_id. 🎉")
            return

        click.echo(f"{len(rows)} row(s) missing oracle_id:\n")
        for c in rows:
            click.echo(f"- id={c.id}  {c.name}  [{c.set_code} {c.collector_number}]  lang={c.lang or 'en'}")
            cand = find_by_set_cn_loose(c.set_code, c.collector_number, c.name)
            if cand:
                click.echo(f"    ✓ loose match oracle_id={cand.get('oracle_id')}  cn={cand.get('collector_number')}  name={cand.get('name')}")
            else:
                cands = candidates_by_set_and_name(c.set_code, c.name)
                if cands:
                    sample = ", ".join(f"{x.get('collector_number')}" for x in cands[:5])
                    more = "" if len(cands) <= 5 else f" (+{len(cands)-5} more)"
                    click.echo(f"    ? name/set candidates: {sample}{more}")
                else:
                    click.echo("    ? no candidates by set+name")

    @app.cli.command("backfill-oracle-ids-fuzzy")
    @click.option("--limit", type=int, default=0, help="Optionally limit how many to try (0 = all).")
    @click.option("--dry-run", is_flag=True, help="Show what would change, but do not write.")
    def backfill_oracle_ids_fuzzy(limit, dry_run):
        if not (cache_exists() and load_cache()):
            click.echo("No Scryfall cache. Run: flask --app app:create_app fetch-scryfall-bulk")
            return

        q = Card.query.filter((Card.oracle_id == None) | (Card.oracle_id == ""))  # noqa: E711
        if limit and limit > 0:
            q = q.limit(limit)

        scanned = 0
        set_count = 0
        for c in q:
            scanned += 1
            cand = find_by_set_cn_loose(c.set_code, c.collector_number, c.name)
            if not cand or not cand.get("oracle_id"):
                continue
            if dry_run:
                click.echo(f"DRY: would set card id={c.id} '{c.name}' ({c.set_code} {c.collector_number}) -> {cand['oracle_id']}")
                set_count += 1
            else:
                c.oracle_id = cand["oracle_id"]
                set_count += 1
                if set_count % 500 == 0:
                    db.session.flush()

        if not dry_run:
            db.session.commit()

        click.echo(f"Scanned {scanned} row(s). {'Would set' if dry_run else 'Set'} oracle_id on {set_count}.")

    @app.cli.command("repair-oracle-ids-advanced")
    @click.option("--limit", type=int, default=0, help="Process only N missing rows.")
    @click.option("--dry-run", is_flag=True, help="Show what would be changed, but don’t write.")
    def repair_oracle_ids_advanced(limit, dry_run):
        if not (cache_exists() and load_cache()):
            click.echo("No Scryfall cache. Run: flask --app app:create_app fetch-scryfall-bulk")
            return

        q = Card.query.filter((Card.oracle_id == None) | (Card.oracle_id == ""))  # noqa: E711
        if limit and limit > 0:
            q = q.limit(limit)

        scanned = 0
        fixed = 0
        for c in q:
            scanned += 1
            found = (find_by_set_cn(c.set_code, c.collector_number, c.name) or
                     find_by_set_cn_loose(c.set_code, c.collector_number, c.name))

            if not found:
                norm = normalize_set_code(c.set_code)
                if norm != (c.set_code or ""):
                    found = (find_by_set_cn(norm, c.collector_number, c.name) or
                             find_by_set_cn_loose(norm, c.collector_number, c.name))

            if not found and " // " in (c.name or ""):
                for face in (c.name or "").split(" // "):
                    face = face.strip()
                    found = find_by_set_cn_loose(c.set_code, c.collector_number, face)
                    if found:
                        break

            if not found:
                oid = unique_oracle_by_name(c.name)
                if oid:
                    if not dry_run:
                        c.oracle_id = oid
                    fixed += 1
                    continue

            if found and found.get("oracle_id"):
                if not dry_run:
                    c.oracle_id = found["oracle_id"]
                fixed += 1

        if not dry_run and fixed:
            db.session.commit()
        click.echo(f"Scanned {scanned} row(s). {'Set' if not dry_run else 'Would set'} oracle_id on {fixed}.")

    @app.cli.command("map-set-codes")
    @click.option("--apply", is_flag=True, help="Apply changes (otherwise preview).")
    def map_set_codes(apply):
        ALIASES = {"vthb": "thb"}
        keys = list(ALIASES.keys())
        if not keys:
            click.echo("No aliases configured.")
            return

        rows = Card.query.filter(Card.set_code.in_(keys)).all()
        if not rows:
            click.echo("No rows with mapped vendor set codes.")
            return

        for r in rows:
            new = ALIASES.get((r.set_code or "").lower())
            click.echo(f"{r.id}: {r.name}   {r.set_code} -> {new}")
            if apply and new:
                r.set_code = new

        if apply:
            db.session.commit()
            click.echo(f"Updated {len(rows)} row(s). Now run:")
            click.echo("  flask --app app:create_app backfill-oracle-ids")

    @app.cli.command("diagnose-missing-oracle-extended")
    def diagnose_missing_oracle_extended():
        q = Card.query.filter((Card.oracle_id == None) | (Card.oracle_id == "")).order_by(Card.id.asc())  # noqa: E711
        rows = q.all()
        if not rows:
            click.echo("No rows missing oracle_id. 🎉")
            return

        have_cache = cache_exists() and load_cache()
        cache_sets = set(sc.all_set_codes()) if have_cache else set()

        click.echo(f"{len(rows)} row(s) missing oracle_id:\n")
        for c in rows:
            scode = (c.set_code or "").lower()
            norm = normalize_set_code(scode)
            unique_oid = unique_oracle_by_name(c.name)

            reasons = []
            if not have_cache:
                reasons.append("cache not loaded")
            if scode and scode not in cache_sets:
                reasons.append(f"set '{scode}' not in cache")
            if norm != scode and norm in cache_sets:
                reasons.append(f"try alias: {scode} → {norm}")
            if unique_oid:
                reasons.append("name is unique across cache (can force by name)")

            reasons_txt = "; ".join(reasons) or "unknown cause"
            click.echo(
                f"- id={c.id:<6} {c.name}  [{c.set_code or '?'} {c.collector_number or '?'}] lang={c.lang or 'en'}\n"
                f"    → {reasons_txt}"
            )

        click.echo("\nNext steps:")
        click.echo("  1) Refresh bulk cache if sets look 'not in cache':")
        click.echo("       flask --app app:create_app fetch-scryfall-bulk --progress")
        click.echo("       flask --app app:create_app refresh-scryfall")
        click.echo("       flask --app app:create_app repair-oracle-ids-advanced")
        click.echo("  2) If an alias is suggested (e.g. tdm → realcode), add it to normalize_set_code() and rerun.")
        click.echo("  3) If a name is unique, use:")
        click.echo("       flask --app app:create_app force-oracle-by-name \"Exact Card Name\"")

    @app.cli.command("force-oracle-by-name")
    @click.argument("name", nargs=-1)
    @click.option("--dry-run", is_flag=True, help="Preview only; no DB writes.")
    def force_oracle_by_name(name, dry_run):
        full_name = " ".join(name).strip()
        if not full_name:
            raise click.ClickException("Provide the exact card name, e.g. \"Magmatic Hellkite\"")

        if not (cache_exists() and load_cache()):
            raise click.ClickException("No Scryfall cache. Run fetch-scryfall-bulk first.")

        oid = unique_oracle_by_name(full_name)
        if not oid:
            raise click.ClickException(
                f"Name '{full_name}' is not unique across cache (or not found)."
            )

        q = (
            Card.query
            .filter((Card.oracle_id == None) | (Card.oracle_id == ""))  # noqa: E711
            .filter(Card.name == full_name)
        )
        targets = q.all()
        if not targets:
            click.echo(f"No rows missing oracle_id for name '{full_name}'.")
            return

        for c in targets:
            click.echo(f"Set id={c.id}  {c.name} [{c.set_code or '?'} {c.collector_number or '?'}] → {oid}")
            if not dry_run:
                c.oracle_id = oid

        if not dry_run:
            db.session.commit()
            click.echo(f"Updated {len(targets)} row(s).")
        else:
            click.echo(f"Would update {len(targets)} row(s).")

    @app.cli.command("cache-has-set")
    @click.argument("set_code")
    def cache_has_set(set_code):
        if not (cache_exists() and load_cache()):
            click.echo("No Scryfall cache loaded.")
            return
        scode = (set_code or "").lower()
        present = scode in set(sc.all_set_codes())
        click.echo(f"Set '{scode}': {'present' if present else 'NOT present'} in cache.")

    @app.cli.command("fetch-scryfall-bulk")
    @click.option("--path", default=None, show_default=False,
                  help="Where to save the bulk file (defaults to sc.DEFAULT_PATH).")
    @click.option("--progress", is_flag=True, help="Show download/index progress.")
    def fetch_scryfall_bulk(path, progress):
        """Download Scryfall 'default_cards' bulk JSON and (re)build local indexes."""
        path = path or sc.DEFAULT_PATH
        os.makedirs(os.path.dirname(path), exist_ok=True)

        url = sc.get_default_cards_download_uri()
        if not url:
            raise click.ClickException("Could not get default_cards download URI from Scryfall.")

        progress_cb = None
        if progress:
            click.echo(f"Downloading default_cards -> {path}")
            last_report = 0

            def _progress_cb(got, total):
                nonlocal last_report
                if total and total > 0:
                    should_report = (got - last_report) >= (2 << 20) or got == total
                else:
                    should_report = (got - last_report) >= (2 << 20)
                if not should_report:
                    return
                pct = (got / total * 100.0) if total else 0.0
                click.echo(f"\r  {got:,}/{total or 0:,} bytes ({pct:5.1f}%)", nl=False)
                last_report = got

            progress_cb = _progress_cb

        result = sc.stream_download_to(path, url, progress_cb=progress_cb)
        if progress:
            click.echo()

        if result.get("status") == "not_modified":
            click.echo("Remote bulk file is already up to date (ETag matched). (Re)building indexes...")
        else:
            click.echo("Download complete. (Re)building indexes...")

        if progress:
            def cb(done, total):
                pct = (done / total * 100.0) if total else 0.0
                click.echo(f"\r  Indexed {done:,}/{total:,} cards ({pct:5.1f}%)", nl=False)
            sc.load_and_index_with_progress(path, step=5000, progress_cb=cb)
            click.echo()
        else:
            sc.load_cache(path)

        stats = sc.cache_stats(path)["prints"]
        size = stats.get("size_bytes", 0)
        click.echo(f"Loaded {stats.get('records', 0):,} records; "
                   f"{stats.get('unique_oracles', 0):,} unique oracles; "
                   f"file size {size:,} bytes.")

    @app.cli.command("force-unique-names-missing")
    @click.option("--dry-run", is_flag=True, help="Preview updates; no DB writes.")
    def force_unique_names_missing(dry_run):
        if not (cache_exists() and load_cache()):
            raise click.ClickException("No Scryfall cache loaded. Run: flask --app app:create_app fetch-scryfall-bulk")

        rows = Card.query.filter((Card.oracle_id == None) | (Card.oracle_id == "")).order_by(Card.id.asc()).all()  # noqa: E711
        if not rows:
            click.echo("Nothing to do: no rows missing oracle_id.")
            return

        updated = 0
        scanned = 0
        for c in rows:
            scanned += 1
            oid = unique_oracle_by_name(c.name)
            if oid:
                click.echo(f" id={c.id:<6} {c.name} [{c.set_code or '?'} {c.collector_number or '?'}] → {oid}")
                if not dry_run:
                    c.oracle_id = oid
                updated += 1

        if not dry_run and updated:
            db.session.commit()

        click.echo(f"Scanned {scanned} row(s). {'Set' if not dry_run else 'Would set'} oracle_id on {updated}.")
        if updated == 0:
            click.echo("Tip: If these are very new prints, re-download bulk, then:")
            click.echo("  flask --app app:create_app fetch-scryfall-bulk --progress")
            click.echo("  flask --app app:create_app refresh-scryfall")
            click.echo("  flask --app app:create_app repair-oracle-ids-advanced")

    @app.cli.command("rulings-stats")
    def rulings_stats_cmd():
        stats = sc.cache_stats().get("rulings", {})
        size = stats.get("size_bytes") or 0

        def _hb(n):
            units = ["B","KB","MB","GB","TB"]
            i = 0
            while n >= 1024 and i < len(units)-1:
                n /= 1024.0; i += 1
            return f"{n:.1f} {units[i]}"
        click.echo(f"Rulings file: {stats.get('file')}")
        click.echo(f"Exists: {stats.get('exists')}  Size: {_hb(size)}")
        click.echo(f"Entries (loaded): {stats.get('entries')}  Oracle keys: {stats.get('oracle_keys')}")
        click.echo(f"Stale: {stats.get('stale')}")

    @app.cli.command("analyze")
    def analyze_sqlite():
        db.session.execute(text("ANALYZE"))
        db.session.commit()
        click.echo("ANALYZE complete.")

    @app.cli.command("vacuum")
    def vacuum_sqlite():
        db.session.execute(text("VACUUM"))
        db.session.commit()
        click.echo("VACUUM complete.")

    @app.cli.group("users")
    def users_cli():
        """Manage DragonsVault user accounts."""

    @users_cli.command("create")
    @click.argument("username")
    @click.argument("email")
    @click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True)
    @click.option("--display-name", default=None, help="Optional label shown in the UI")
    @click.option("--admin/--no-admin", default=False, help="Grant admin rights")
    def create_user(username, email, password, display_name, admin):
        normalized = email.strip().lower()
        if not normalized:
            raise click.ClickException("Email is required.")
        if User.query.filter(func.lower(User.email) == normalized).first():
            raise click.ClickException(f"User {normalized} already exists.")
        username_clean = username.strip().lower()
        if not username_clean:
            raise click.ClickException("Username is required.")
        if User.query.filter(func.lower(User.username) == username_clean).first():
            raise click.ClickException(f"Username {username_clean} already exists.")
        user = User(email=normalized, username=username_clean, display_name=display_name)
        user.set_password(password)
        user.is_admin = admin
        db.session.add(user)
        db.session.commit()
        click.echo(f"Created user {normalized}/{username_clean} (admin={admin}).")

    @users_cli.command("set-password")
    @click.argument("email")
    @click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True)
    def set_user_password(email, password):
        normalized = email.strip().lower()
        user = User.query.filter(func.lower(User.email) == normalized).first()
        if not user:
            raise click.ClickException(f"User {normalized} not found.")
        user.set_password(password)
        db.session.commit()
        click.echo(f"Password updated for {normalized}.")

    @users_cli.command("token")
    @click.argument("email")
    @click.option("--revoke", is_flag=True, help="Revoke the current token instead of issuing a new one")
    def manage_user_token(email, revoke):
        normalized = email.strip().lower()
        user = User.query.filter(func.lower(User.email) == normalized).first()
        if not user:
            raise click.ClickException(f"User {normalized} not found.")
        if revoke:
            user.clear_api_token()
            db.session.commit()
            click.echo("API token revoked.")
            return
        token = user.issue_api_token()
        db.session.commit()
        click.echo("New API token (store securely; shown once):")
        click.echo(token)

    @app.errorhandler(404)
    def not_found(e):
        """Render a friendly 404 page while keeping the error for debugging."""
        return render_template("shared/system/404.html", e=e), 404

    @app.errorhandler(500)
    def internal(e):
        """Roll back broken transactions and return the standard 500 view."""
        db.session.rollback()
        return render_template("shared/system/500.html", e=e, message="A database error occurred. Please try again."), 500

    @app.before_request
    def assign_request_id():
        g.request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex

    @app.after_request
    def attach_request_id(resp):
        rid = getattr(g, "request_id", None)
        if rid:
            resp.headers.setdefault("X-Request-ID", rid)
        return resp

    @app.after_request
    def security_headers(resp):
        """Attach a minimal set of security-related HTTP headers to each response."""
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer-when-downgrade")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        return resp

    return app


if __name__ == "__main__":
    _app = create_app()
    _app.run(host="127.0.0.1", port=5000, debug=True)


# Single SQLite PRAGMA hook (avoid duplicate listeners)
_SQLITE_PRAGMA_STATEMENTS = (
    "PRAGMA foreign_keys=ON;",
    "PRAGMA journal_mode=WAL;",
    "PRAGMA synchronous=NORMAL;",
    "PRAGMA temp_store=MEMORY;",
    "PRAGMA cache_size=-20000;",
    "PRAGMA mmap_size=268435456;",
)


def _apply_sqlite_pragmas(dbapi_connection) -> None:
    """Execute the configured PRAGMAs if this is a SQLite connection."""
    if not isinstance(dbapi_connection, sqlite3.Connection):
        return
    try:
        cur = dbapi_connection.cursor()
        for statement in _SQLITE_PRAGMA_STATEMENTS:
            cur.execute(statement)
        cur.close()
    except Exception:
        pass


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_connection, _) -> None:
    """Apply pragmatic performance/safety PRAGMAs each time SQLite opens a connection."""
    _apply_sqlite_pragmas(dbapi_connection)
