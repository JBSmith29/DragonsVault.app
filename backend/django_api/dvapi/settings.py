"""Django settings for the API migration."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import unquote, urlparse

BASE_DIR = Path(__file__).resolve().parent.parent
INSTANCE_DIR = Path(os.getenv("INSTANCE_DIR", BASE_DIR / "instance")).resolve()


def _load_secret_key() -> str | None:
    secret = os.getenv("DJANGO_SECRET_KEY") or os.getenv("SECRET_KEY")
    if secret:
        return secret
    secret_file = os.getenv("DJANGO_SECRET_KEY_FILE") or os.getenv("SECRET_KEY_FILE")
    if secret_file:
        try:
            return Path(secret_file).read_text(encoding="utf-8").strip()
        except OSError:
            return None
    return None


def _database_from_url(url: str | None) -> dict[str, str] | None:
    if not url:
        return None
    if url.startswith("postgresql+psycopg2://"):
        url = "postgresql://" + url[len("postgresql+psycopg2://") :]
    parsed = urlparse(url)
    if parsed.scheme in {"postgres", "postgresql"}:
        return {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": (parsed.path or "/").lstrip("/"),
            "USER": unquote(parsed.username or ""),
            "PASSWORD": unquote(parsed.password or ""),
            "HOST": parsed.hostname or "",
            "PORT": str(parsed.port or ""),
        }
    if parsed.scheme == "sqlite":
        db_path = parsed.path
        if not db_path:
            return None
        return {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": db_path,
        }
    return None


SECRET_KEY = _load_secret_key() or "dev"
DEBUG = os.getenv("DJANGO_DEBUG", "0").lower() in {"1", "true", "yes", "on"}

ALLOWED_HOSTS = [host.strip() for host in os.getenv("DJANGO_ALLOWED_HOSTS", "*").split(",") if host.strip()]
if not ALLOWED_HOSTS:
    ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.staticfiles",
    "rest_framework",
    "api",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "dvapi.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [],
        },
    },
]

WSGI_APPLICATION = "dvapi.wsgi.application"

DEFAULT_SQLITE = INSTANCE_DIR / "database.db"
DATABASES = {
    "default": _database_from_url(os.getenv("DATABASE_URL"))
    or {"ENGINE": "django.db.backends.sqlite3", "NAME": str(DEFAULT_SQLITE)}
}

AUTH_PASSWORD_VALIDATORS: list[dict[str, str]] = []

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = False
USE_TZ = True

STATIC_URL = "/static/"

DEFAULT_AUTO_FIELD = "django.db.models.AutoField"

APPEND_SLASH = False

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["api.authentication.ApiTokenAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["api.permissions.ApiTokenRequired"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "UNAUTHENTICATED_USER": None,
}
