from __future__ import annotations

import os

from ..cache import default_cache_type
from ..database import default_sqlite_uri, sqlalchemy_engine_options
from ..paths import INSTANCE_DIR, SECRET_KEY_VALUE


class BaseConfig:
    # Flask basics - ensure secure defaults
    SECRET_KEY = SECRET_KEY_VALUE or "dev"  # override in prod!
    TEMPLATES_AUTO_RELOAD = False

    # Security headers
    SEND_FILE_MAX_AGE_DEFAULT = 86400
    PERMANENT_SESSION_LIFETIME = 4 * 60 * 60  # 4 hour session timeout

    # Database (absolute sqlite path; forward slashes are fine on Windows)
    DEFAULT_SQLITE = default_sqlite_uri(INSTANCE_DIR)
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL", DEFAULT_SQLITE)
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = sqlalchemy_engine_options(SQLALCHEMY_DATABASE_URI)

    # Uploads / responses with security limits
    MAX_CONTENT_LENGTH = int(os.getenv("MAX_CONTENT_LENGTH", 64 * 1024 * 1024))

    # Cookie security - secure by default
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "1").lower() in {"1", "true", "yes", "on"}

    # CSRF Protection
    WTF_CSRF_ENABLED = True
    WTF_CSRF_TIME_LIMIT = 3600  # 1 hour
    WTF_CSRF_SSL_STRICT = os.getenv("WTF_CSRF_SSL_STRICT", "0").lower() in {"1", "true", "yes", "on"}

    # Dev-only convenience
    ALLOW_RUNTIME_INDEX_BOOTSTRAP = os.getenv("ALLOW_RUNTIME_INDEX_BOOTSTRAP", "0").lower() in {"1", "true", "yes", "on"}

    # Cache configuration (defaults to in-process SimpleCache)
    CACHE_DEFAULT_TIMEOUT = int(os.getenv("CACHE_DEFAULT_TIMEOUT", 600))
    CACHE_TYPE = default_cache_type()
    CACHE_REDIS_HOST = os.getenv("CACHE_REDIS_HOST")
    CACHE_REDIS_PORT = int(os.getenv("CACHE_REDIS_PORT", 6379))
    CACHE_REDIS_DB = int(os.getenv("CACHE_REDIS_DB", 0))
    CACHE_REDIS_URL = os.getenv("CACHE_REDIS_URL") or os.getenv("REDIS_URL")
    CACHE_KEY_PREFIX = os.getenv("CACHE_KEY_PREFIX")
    RATELIMIT_STORAGE_URI = os.getenv(
        "RATELIMIT_STORAGE_URI",
        os.getenv("RATELIMIT_REDIS_URI") or os.getenv("REDIS_URL") or "memory://",
    )
    RATELIMIT_DEFAULT = os.getenv("RATELIMIT_DEFAULT", "200 per minute")
    ENABLE_TALISMAN = os.getenv("ENABLE_TALISMAN", "1").lower() in {"1", "true", "yes", "on"}
    TALISMAN_FORCE_HTTPS = os.getenv("TALISMAN_FORCE_HTTPS", "1").lower() in {"1", "true", "yes", "on"}
    REDIS_URL = os.getenv("REDIS_URL", os.getenv("RATELIMIT_STORAGE_URI", "redis://localhost:6379/0"))
    SCRYFALL_OFFLINE_FIRST = os.getenv("SCRYFALL_OFFLINE_FIRST", "1").lower() in {"1", "true", "yes", "on"}
    SCRYFALL_REFRESH_INLINE = os.getenv("SCRYFALL_REFRESH_INLINE", "0").lower() in {"1", "true", "yes", "on"}
    # Default to inline imports so users aren't stuck waiting for a background worker.
    IMPORT_RUN_INLINE = os.getenv("IMPORT_RUN_INLINE", "1").lower() in {"1", "true", "yes", "on"}
    TYPE_FILTER_USE_DB = os.getenv("TYPE_FILTER_USE_DB", "0").lower() in {"1", "true", "yes", "on"}
    HCAPTCHA_ENABLED = os.getenv("HCAPTCHA_ENABLED", "0").lower() in {"1", "true", "yes", "on"}
    HCAPTCHA_SITE_KEY = os.getenv("HCAPTCHA_SITE_KEY")
    HCAPTCHA_SECRET = os.getenv("HCAPTCHA_SECRET")
    # CSP tuned for our CDN dependencies (Bootstrap, icons, HTMX) and external APIs (Scryfall, hCaptcha).
    CONTENT_SECURITY_POLICY = {
        "default-src": "'self'",
        "img-src": "'self' data: https://c1.scryfall.com https://cards.scryfall.io https://svgs.scryfall.io",
        "script-src": "'self' https://cdn.jsdelivr.net https://unpkg.com https://instant.page https://js.hcaptcha.com",
        "script-src-attr": "'unsafe-inline'",
        "style-src": "'self' 'unsafe-inline' https://cdn.jsdelivr.net",
        "connect-src": "'self' https://api.scryfall.com https://js.hcaptcha.com",
        "font-src": "'self' data: https://cdn.jsdelivr.net",
        "frame-src": "https://js.hcaptcha.com",
    }
    STATIC_ASSET_BASE_URL = os.getenv("STATIC_ASSET_BASE_URL")
