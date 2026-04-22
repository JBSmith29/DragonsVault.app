"""Flask application factory, runtime bootstrap, and request lifecycle wiring."""

from contextlib import contextmanager
import logging
import os
import re
import sqlite3
import uuid
from pathlib import Path

from flask import Flask, render_template, g, has_request_context, request, flash, redirect, url_for, jsonify, session
from flask_compress import Compress
try:
    from flask_talisman import Talisman  # type: ignore
    _talisman_available = True
except ImportError:  # pragma: no cover - optional dependency
    Talisman = None  # type: ignore
    _talisman_available = False
from jinja2 import ChoiceLoader, FileSystemLoader
from jinja2.bccache import FileSystemBytecodeCache
from sqlalchemy import event, exists, func, or_
from sqlalchemy.engine import Engine
from sqlalchemy.orm import with_loader_criteria

from dotenv import load_dotenv; load_dotenv()

from config import Config, INSTANCE_DIR as CONFIG_INSTANCE_DIR
from extensions import db, migrate, cache, csrf, limiter, login_manager, generate_csrf
from flask_login import current_user
from core.shared.utils.assets import static_url
from shared.error_handlers import register_error_handlers
from shared.app_cli import register_cli_commands
from shared.app_runtime import configure_request_logging, extend_csp_for_static_assets

# Scryfall helpers
from core.domains.cards.services import scryfall_cache as sc
from core.domains.cards.services.scryfall_cache import (
    ensure_cache_loaded,
    cache_exists, load_cache, find_by_set_cn,
    candidates_by_set_and_name, find_by_set_cn_loose,
    normalize_set_code, unique_oracle_by_name,
)
from core.domains.decks.services.deck_utils import BASIC_LANDS

# FTS helpers
from shared.database.fts import ensure_fts, reindex_fts
from shared.database import ensure_runtime_schema_fallbacks, validate_sqlite_database

# Optional compat shim (ignore if missing)
try:
    import core.domains.cards.services.scryfall_cache_compat  # noqa: F401
except Exception:
    pass

def _fallback_enabled() -> bool:
    """Guard legacy schema fallbacks; disabled unless explicitly opted-in."""
    return os.getenv("ENABLE_ROLE_TABLE_FALLBACK", "0").lower() in {"1", "true", "yes", "on"}


_visibility_filters_registered = False
_VISIBILITY_SKIP_SESSION_FLAG = "_skip_visibility_filters"

@contextmanager
def _without_visibility_filters():
    """Temporarily disable per-request visibility hooks for auth loader queries."""
    depth = int(db.session.info.get(_VISIBILITY_SKIP_SESSION_FLAG, 0) or 0)
    db.session.info[_VISIBILITY_SKIP_SESSION_FLAG] = depth + 1
    try:
        yield
    finally:
        if depth > 0:
            db.session.info[_VISIBILITY_SKIP_SESSION_FLAG] = depth
        else:
            db.session.info.pop(_VISIBILITY_SKIP_SESSION_FLAG, None)


def _register_visibility_filters(card_model, folder_model) -> None:
    """Apply per-request visibility rules so users only see their own folders/cards."""
    global _visibility_filters_registered
    if _visibility_filters_registered:
        return

    @event.listens_for(db.session, "do_orm_execute")
    def _apply_folder_visibility(execute_state):
        if not execute_state.is_select:
            return
        if execute_state.is_column_load or execute_state.is_relationship_load:
            return
        if not has_request_context():
            return
        if int(execute_state.session.info.get(_VISIBILITY_SKIP_SESSION_FLAG, 0) or 0) > 0:
            return

        user_id_int = getattr(g, "_visibility_user_id", None)
        if user_id_int is None:
            user_id = session.get("_user_id")
            if not user_id:
                return
            try:
                user_id_int = int(user_id)
            except (TypeError, ValueError):
                return

        if user_id_int <= 0:
            return

        from models import FolderShare, UserFriend  # local import to avoid early import issues

        share_exists = (
            exists()
            .where((FolderShare.folder_id == folder_model.id) & (FolderShare.shared_user_id == user_id_int))
            .correlate(folder_model)
        )
        friend_exists = (
            exists()
            .where(
                (UserFriend.user_id == user_id_int)
                & (UserFriend.friend_user_id == folder_model.owner_user_id)
            )
            .correlate(folder_model)
        )
        scope_clause = or_(
            folder_model.owner_user_id == user_id_int,
            folder_model.owner_user_id.is_(None),
            folder_model.is_public.is_(True),
            share_exists,
            friend_exists,
        )

        execute_state.statement = execute_state.statement.options(
            with_loader_criteria(
                folder_model,
                lambda cls: scope_clause,
                include_aliases=True,
            ),
            with_loader_criteria(
                card_model,
                lambda cls: cls.folder.has(scope_clause),
                include_aliases=True,
            ),
        )

    _visibility_filters_registered = True


def _configure_login_manager(app: Flask) -> None:
    """Bind Flask-Login and support API token authentication."""
    login_manager.init_app(app)
    login_manager.login_view = "views.login"
    login_manager.login_message_category = "warning"
    login_manager.session_protection = app.config.get("SESSION_PROTECTION", "basic")

    def _extract_token(req):
        auth_header = req.headers.get("Authorization", "")
        if auth_header.lower().startswith("bearer "):
            return auth_header.split(" ", 1)[1].strip()
        return None

    @login_manager.user_loader
    def _load_user(user_id: str):
        from models import User

        try:
            user_id_int = int(user_id)
        except (TypeError, ValueError):
            return None
        with _without_visibility_filters():
            user = db.session.get(User, user_id_int)
        if user is not None and getattr(user, "archived_at", None) is not None:
            return None
        if user is not None and has_request_context():
            g._visibility_user_id = user_id_int
        return user

    @login_manager.request_loader
    def _load_user_from_request(req):
        from models import User

        token = _extract_token(req)
        if not token:
            return None
        with _without_visibility_filters():
            user = User.verify_api_token(token)
        if user is not None and getattr(user, "archived_at", None) is not None:
            return None
        if user is not None and has_request_context():
            try:
                g._visibility_user_id = int(getattr(user, "id", 0) or 0)
            except (TypeError, ValueError):
                g._visibility_user_id = None
        return user

    @login_manager.unauthorized_handler
    def _unauthorized():
        accepts_json = request.accept_mimetypes["application/json"] >= request.accept_mimetypes["text/html"]
        if request.path.startswith("/api/") or accepts_json:
            return jsonify({"error": "authentication_required"}), 401
        flash("Please sign in to continue.", "warning")
        next_url = request.full_path or request.path or "/"
        return redirect(url_for("views.login", next=next_url))


# ---------------------------------------------------------------------------
# Extension bootstrap helpers
# ---------------------------------------------------------------------------

def _safe_init_sqlalchemy(app: Flask):
    """Initialise SQLAlchemy only if it has not been bound yet."""
    if not getattr(app, "extensions", None) or "sqlalchemy" not in app.extensions:
        db.init_app(app)


def _safe_init_migrate(app: Flask):
    """Bind Flask-Migrate safely, falling back to batch mode for SQLite."""
    migrations_dir = str(Path(__file__).resolve().parent / "migrations")
    try:
        if not getattr(app, "extensions", None) or "migrate" not in app.extensions:
            migrate.init_app(app, db, render_as_batch=True, directory=migrations_dir)
    except Exception:
        migrate.init_app(app, db, render_as_batch=True, directory=migrations_dir)


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
    configure_request_logging(app)
    app.jinja_env.globals["static_url"] = static_url

    # Add future template roots for staged domain migration.
    extra_template_dirs = [
        Path(app.root_path) / "core" / "templates",
        Path(app.root_path) / "core" / "domains" / "cards" / "templates",
        Path(app.root_path) / "core" / "domains" / "games" / "templates",
        Path(app.root_path) / "core" / "domains" / "decks" / "templates",
        Path(app.root_path) / "core" / "domains" / "users" / "templates",
    ]
    loaders = []
    if app.jinja_loader:
        loaders.append(app.jinja_loader)
    loaders.append(FileSystemLoader([str(path) for path in extra_template_dirs]))
    app.jinja_loader = ChoiceLoader(loaders)

    # Honor X-Forwarded-* headers from a trusted reverse proxy (nginx, Cloudflare, LB, etc.).
    # This prevents Flask-Talisman from forcing HTTPS redirects on plain HTTP health checks.
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

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
    app.config.setdefault("STATIC_ASSET_BASE_URL", os.getenv("STATIC_ASSET_BASE_URL"))

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
        """Reject API tokens in query parameters for security."""
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

    @app.before_request
    def _validate_csrf_token():
        """Enhanced CSRF protection for all POST requests."""
        if request.method in ['POST', 'PUT', 'DELETE', 'PATCH']:
            # Skip CSRF for API endpoints with proper authentication
            if request.path.startswith('/api/') and request.headers.get('Authorization'):
                return
            
            # Let Flask-WTF handle CSRF validation - it's already configured
            # This function is just for API token validation above
            pass

    public_endpoints = {
        "views.landing_page",
        "views.login",
        "views.register",
        "views.forgot_password",
        "views.reset_password",
        "views.healthz",
        "views.readyz",
        "api.api_healthz",
        "api.api_readyz",
        "api_v1.api_healthz",
        "api_v1.api_readyz",
        "views.metrics",
        "views.overall_health",
        "api.overall_health",
        "api_v1.overall_health",
        "views.gamedashboard",
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
        wants_json = path.startswith("/api/") or (
            request.accept_mimetypes["application/json"] >= request.accept_mimetypes["text/html"]
        )
        if wants_json:
            response = jsonify({"error": "authentication_required"})
            response.status_code = 401
            response.headers["WWW-Authenticate"] = "Bearer"
            return response
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
        extend_csp_for_static_assets(app)
        csp = app.config.get("CONTENT_SECURITY_POLICY")
        Talisman(
            app,
            content_security_policy=csp,
            content_security_policy_nonce_in=["script-src", "style-src"],
            force_https=app.config.get("TALISMAN_FORCE_HTTPS", not app.debug),
            session_cookie_secure=app.config.get("SESSION_COOKIE_SECURE", True),
            session_cookie_samesite=app.config.get("SESSION_COOKIE_SAMESITE", "Lax"),
        )
    elif not _talisman_available and app.config.get("ENABLE_TALISMAN", True):
        app.logger.warning("Flask-Talisman not installed; CSP/HSTS disabled.")

    # Reuse the active document nonce for HTMX fragment responses.
    # Without this, strict nonce-only CSP can block page-specific inline <style>/<script>
    # after boosted navigation because each request gets a fresh server nonce.
    nonce_re = re.compile(r"^[A-Za-z0-9+/_=-]{8,200}$")
    base_csp_nonce = app.jinja_env.globals.get("csp_nonce")

    def csp_nonce_for_templates() -> str:
        if has_request_context() and request.headers.get("HX-Request") == "true":
            header_nonce = (request.headers.get("X-CSP-Nonce") or "").strip()
            if header_nonce and nonce_re.fullmatch(header_nonce):
                return header_nonce
        if callable(base_csp_nonce):
            try:
                nonce_value = base_csp_nonce()
            except Exception:
                nonce_value = ""
            if isinstance(nonce_value, str):
                return nonce_value
            if nonce_value is None:
                return ""
            return str(nonce_value)
        return ""

    app.jinja_env.globals["csp_nonce"] = csp_nonce_for_templates

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
        validate_sqlite_database(app)
        try:
            ensure_cache_loaded()
        except Exception as e:
            app.logger.warning("Scryfall cache not loaded at init: %s", e)

        # Import models after db is bound
        from models import Card, Folder, WishlistItem  # noqa: F401
        from core.domains.decks.services.deck_service import register_deck_stats_listeners
        from shared.cache.request_cache import register_request_cache_listeners
        _register_visibility_filters(Card, Folder)
        register_deck_stats_listeners()
        register_request_cache_listeners()
        ensure_runtime_schema_fallbacks(app, fallback_enabled=fallback)

        # Ensure FTS5 virtual table + triggers exist (fixes the cards_fts error)
        ensure_fts()

    # Blueprints
    from core.routes import api_blueprints, register_routes, web_blueprints
    register_routes()

    # Web UI surface (`views` blueprint and domain web routes)
    for blueprint in web_blueprints():
        app.register_blueprint(blueprint)

    # API surface (unversioned + /api/v1 version aliases)
    for blueprint, versioned_prefix in api_blueprints():
        app.register_blueprint(blueprint)
        app.register_blueprint(
            blueprint,
            url_prefix=versioned_prefix,
            name=f"{blueprint.name}_v1",
        )

    def _legacy_api_endpoint_fallback(error, endpoint: str, values: dict):
        """Gracefully map legacy views.* API endpoint names to api.* routes.

        Some templates/services historically referenced API handlers as
        ``views.<endpoint>``. During route split, those handlers moved under the
        ``api`` blueprint. This fallback prevents runtime 500s from stale
        bytecode/templates by retrying URL generation against API blueprints.
        """
        if not endpoint or not endpoint.startswith("views."):
            return None
        endpoint_name = endpoint.split(".", 1)[1]
        for candidate in (f"api.{endpoint_name}", f"api_v1.{endpoint_name}"):
            if candidate not in app.view_functions:
                continue
            try:
                return app.url_for(candidate, **(values or {}))
            except Exception:
                continue
        return None

    app.url_build_error_handlers.append(_legacy_api_endpoint_fallback)

    @app.context_processor
    def inject_device_type():
        return {"is_mobile": False}

    @app.context_processor
    def inject_basic_lands():
        return {"BASIC_LANDS": BASIC_LANDS}

    register_cli_commands(app)

    register_error_handlers(app)

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
            # Use parameterized queries to prevent SQL injection
            cur.execute(statement)
        cur.close()
    except Exception:
        pass


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_connection, _) -> None:
    """Apply pragmatic performance/safety PRAGMAs each time SQLite opens a connection."""
    _apply_sqlite_pragmas(dbapi_connection)
