from __future__ import annotations
import os
from pathlib import Path

# Absolute project dir
BASE_DIR = Path(__file__).resolve().parent
# Absolute instance dir (defaults to <project>/instance)
INSTANCE_DIR = Path(os.getenv("INSTANCE_DIR", BASE_DIR / "instance")).resolve()

class BaseConfig:
    # Flask basics
    SECRET_KEY = os.getenv("SECRET_KEY", "dev")  # override in prod!
    TEMPLATES_AUTO_RELOAD = False

    # Database (absolute sqlite path; forward slashes are fine on Windows)
    DEFAULT_SQLITE = f"sqlite:///{(INSTANCE_DIR / 'database.db').as_posix()}"
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL", DEFAULT_SQLITE)
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Uploads / responses
    MAX_CONTENT_LENGTH = int(os.getenv("MAX_CONTENT_LENGTH", 64 * 1024 * 1024))
    SEND_FILE_MAX_AGE_DEFAULT = 86400

    # Cookie security
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "0").lower() in {"1","true","yes","on"}

    # Dev-only convenience
    ALLOW_RUNTIME_INDEX_BOOTSTRAP = os.getenv("ALLOW_RUNTIME_INDEX_BOOTSTRAP", "0").lower() in {"1","true","yes","on"}

    # Cache configuration (defaults to in-process SimpleCache)
    CACHE_DEFAULT_TIMEOUT = int(os.getenv("CACHE_DEFAULT_TIMEOUT", 600))
    CACHE_TYPE = os.getenv("CACHE_TYPE", "SimpleCache")
    CACHE_REDIS_HOST = os.getenv("CACHE_REDIS_HOST")
    CACHE_REDIS_PORT = int(os.getenv("CACHE_REDIS_PORT", 6379))
    CACHE_REDIS_DB = int(os.getenv("CACHE_REDIS_DB", 0))
    CACHE_REDIS_URL = os.getenv("CACHE_REDIS_URL")
    RATELIMIT_STORAGE_URI = os.getenv(
        "RATELIMIT_STORAGE_URI",
        os.getenv("RATELIMIT_REDIS_URI") or os.getenv("REDIS_URL") or "memory://",
    )
    RATELIMIT_DEFAULT = os.getenv("RATELIMIT_DEFAULT", "200 per minute")
    ENABLE_TALISMAN = os.getenv("ENABLE_TALISMAN", "1").lower() in {"1","true","yes","on"}
    TALISMAN_FORCE_HTTPS = os.getenv("TALISMAN_FORCE_HTTPS", "1").lower() in {"1","true","yes","on"}
    REDIS_URL = os.getenv("REDIS_URL", os.getenv("RATELIMIT_STORAGE_URI", "redis://localhost:6379/0"))
    SCRYFALL_OFFLINE_FIRST = os.getenv("SCRYFALL_OFFLINE_FIRST", "1").lower() in {"1","true","yes","on"}
    SCRYFALL_REFRESH_INLINE = os.getenv("SCRYFALL_REFRESH_INLINE", "0").lower() in {"1","true","yes","on"}
    IMPORT_RUN_INLINE = os.getenv("IMPORT_RUN_INLINE", "0").lower() in {"1","true","yes","on"}
    TYPE_FILTER_USE_DB = os.getenv("TYPE_FILTER_USE_DB", "0").lower() in {"1","true","yes","on"}
    HCAPTCHA_ENABLED = os.getenv("HCAPTCHA_ENABLED", "0").lower() in {"1","true","yes","on"}
    HCAPTCHA_SITE_KEY = os.getenv("HCAPTCHA_SITE_KEY")
    HCAPTCHA_SECRET = os.getenv("HCAPTCHA_SECRET")
    CONTENT_SECURITY_POLICY = {
        "default-src": "'self'",
        "img-src": "'self' data: https://c1.scryfall.com https://cards.scryfall.io",
        "script-src": "'self'",
        "style-src": "'self' 'unsafe-inline'",
        "connect-src": "'self'",
        "font-src": "'self' data:",
    }

class DevelopmentConfig(BaseConfig):
    DEBUG = True
    TEMPLATES_AUTO_RELOAD = True

class ProductionConfig(BaseConfig):
    DEBUG = False
    SESSION_COOKIE_SECURE = True


def _select_config():
    env = os.getenv("FLASK_ENV")
    if env == "development":
        return DevelopmentConfig
    secret = os.getenv("SECRET_KEY", "dev")
    if not secret or secret == "dev":
        raise RuntimeError("SECRET_KEY must be set to a non-default value in production.")
    return ProductionConfig


# Choose config
Config = _select_config()
