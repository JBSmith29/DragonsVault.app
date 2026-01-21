from __future__ import annotations
import os
from pathlib import Path

def _default_cache_type() -> str:
    """Prefer Redis cache when a Redis URL is configured; fall back to SimpleCache."""
    if os.getenv("CACHE_TYPE"):
        return os.getenv("CACHE_TYPE", "SimpleCache")
    if os.getenv("CACHE_REDIS_URL") or os.getenv("REDIS_URL"):
        return "RedisCache"
    return "SimpleCache"

def _load_secret_key() -> str | None:
    """Load the Flask secret key from env or an optional file path."""
    secret = os.getenv("SECRET_KEY")
    if secret:
        return secret
    secret_file = os.getenv("SECRET_KEY_FILE")
    if secret_file:
        try:
            secret = Path(secret_file).read_text(encoding="utf-8").strip()
            if secret:
                return secret
        except OSError:
            # Fall back to default handling if the secret file cannot be read.
            pass
    return None


# Absolute project dir
BASE_DIR = Path(__file__).resolve().parent
# Absolute instance dir (defaults to <project>/instance, falls back to legacy root instance/)
DEFAULT_INSTANCE_DIR = BASE_DIR / "instance"
LEGACY_INSTANCE_DIR = BASE_DIR.parent / "instance"
_env_instance = os.getenv("INSTANCE_DIR")
if _env_instance:
    INSTANCE_DIR = Path(_env_instance).resolve()
elif LEGACY_INSTANCE_DIR.exists():
    INSTANCE_DIR = LEGACY_INSTANCE_DIR.resolve()
else:
    INSTANCE_DIR = DEFAULT_INSTANCE_DIR.resolve()
SECRET_KEY_VALUE = _load_secret_key()

class BaseConfig:
    # Flask basics - ensure secure defaults
    SECRET_KEY = SECRET_KEY_VALUE or "dev"  # override in prod!
    TEMPLATES_AUTO_RELOAD = False
    
    # Security headers
    SEND_FILE_MAX_AGE_DEFAULT = 86400
    PERMANENT_SESSION_LIFETIME = 3600  # 1 hour session timeout
    
    # Database (absolute sqlite path; forward slashes are fine on Windows)
    DEFAULT_SQLITE = f"sqlite:///{(INSTANCE_DIR / 'database.db').as_posix()}"
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL", DEFAULT_SQLITE)
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_pre_ping': True,
        'pool_recycle': 3600,
        'connect_args': {'check_same_thread': False} if 'sqlite' in os.getenv("DATABASE_URL", DEFAULT_SQLITE) else {}
    }

    # Uploads / responses with security limits
    MAX_CONTENT_LENGTH = int(os.getenv("MAX_CONTENT_LENGTH", 64 * 1024 * 1024))
    
    # Cookie security - secure by default
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "1").lower() in {"1","true","yes","on"}
    
    # CSRF Protection
    WTF_CSRF_ENABLED = True
    WTF_CSRF_TIME_LIMIT = 3600  # 1 hour
    WTF_CSRF_SSL_STRICT = os.getenv("WTF_CSRF_SSL_STRICT", "1").lower() in {"1","true","yes","on"}

    # Dev-only convenience
    ALLOW_RUNTIME_INDEX_BOOTSTRAP = os.getenv("ALLOW_RUNTIME_INDEX_BOOTSTRAP", "0").lower() in {"1","true","yes","on"}

    # Cache configuration (defaults to in-process SimpleCache)
    CACHE_DEFAULT_TIMEOUT = int(os.getenv("CACHE_DEFAULT_TIMEOUT", 600))
    CACHE_TYPE = _default_cache_type()
    CACHE_REDIS_HOST = os.getenv("CACHE_REDIS_HOST")
    CACHE_REDIS_PORT = int(os.getenv("CACHE_REDIS_PORT", 6379))
    CACHE_REDIS_DB = int(os.getenv("CACHE_REDIS_DB", 0))
    CACHE_REDIS_URL = os.getenv("CACHE_REDIS_URL") or os.getenv("REDIS_URL")
    RATELIMIT_STORAGE_URI = os.getenv(
        "RATELIMIT_STORAGE_URI",
        os.getenv("RATELIMIT_REDIS_URI") or os.getenv("REDIS_URL") or "memory://",
    )
    RATELIMIT_DEFAULT = os.getenv("RATELIMIT_DEFAULT", "200 per minute")
    ENABLE_TALISMAN = os.getenv("ENABLE_TALISMAN", "1").lower() in {"1","true","yes","on"}
    TALISMAN_FORCE_HTTPS = os.getenv("TALISMAN_FORCE_HTTPS", "1").lower() in {"1","true","yes","on"}
    REDIS_URL = os.getenv("REDIS_URL", os.getenv("RATELIMIT_STORAGE_URI", "redis://localhost:6379/0"))
    WTF_CSRF_SSL_STRICT = os.getenv("WTF_CSRF_SSL_STRICT", "0").lower() in {"1","true","yes","on"}
    SCRYFALL_OFFLINE_FIRST = os.getenv("SCRYFALL_OFFLINE_FIRST", "1").lower() in {"1","true","yes","on"}
    SCRYFALL_REFRESH_INLINE = os.getenv("SCRYFALL_REFRESH_INLINE", "0").lower() in {"1","true","yes","on"}
    # Default to inline imports so users aren't stuck waiting for a background worker.
    IMPORT_RUN_INLINE = os.getenv("IMPORT_RUN_INLINE", "1").lower() in {"1","true","yes","on"}
    TYPE_FILTER_USE_DB = os.getenv("TYPE_FILTER_USE_DB", "0").lower() in {"1","true","yes","on"}
    HCAPTCHA_ENABLED = os.getenv("HCAPTCHA_ENABLED", "0").lower() in {"1","true","yes","on"}
    HCAPTCHA_SITE_KEY = os.getenv("HCAPTCHA_SITE_KEY")
    HCAPTCHA_SECRET = os.getenv("HCAPTCHA_SECRET")
    # CSP tuned for our CDN dependencies (Bootstrap, icons, HTMX) and external APIs (Scryfall, hCaptcha).
    CONTENT_SECURITY_POLICY = {
        "default-src": "'self'",
        "img-src": "'self' data: https://c1.scryfall.com https://cards.scryfall.io https://svgs.scryfall.io",
        "script-src": "'self' 'unsafe-inline' https://cdn.jsdelivr.net https://unpkg.com https://instant.page https://js.hcaptcha.com",
        "style-src": "'self' 'unsafe-inline' https://cdn.jsdelivr.net",
        "connect-src": "'self' https://api.scryfall.com https://js.hcaptcha.com",
        "font-src": "'self' data: https://cdn.jsdelivr.net",
        "frame-src": "https://js.hcaptcha.com",
    }
    STATIC_ASSET_BASE_URL = os.getenv("STATIC_ASSET_BASE_URL")

class DevelopmentConfig(BaseConfig):
    DEBUG = True
    TEMPLATES_AUTO_RELOAD = True

class ProductionConfig(BaseConfig):
    DEBUG = False
    # Respect environment override instead of forcing secure cookies (can be re-enabled via env).
    SESSION_COOKIE_SECURE = BaseConfig.SESSION_COOKIE_SECURE


def _select_config():
    env = os.getenv("FLASK_ENV")
    if env == "development":
        return DevelopmentConfig
    secret = SECRET_KEY_VALUE or "dev"
    if not secret or secret == "dev":
        raise RuntimeError(
            "SECRET_KEY must be provided via SECRET_KEY or SECRET_KEY_FILE in production."
        )
    return ProductionConfig


# Choose config
Config = _select_config()
