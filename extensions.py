"""DragonsVault: shared Flask extension instances.
===================================================
This module centralizes third-party Flask extensions so they can be imported
without causing circular dependencies. Import **only** from here in app code:
    from extensions import db, migrate, cache

Why this exists
---------------
- Keeps a single SQLAlchemy() instance across the app.
- Applies a stable naming convention so Alembic migrations produce deterministic
  constraint/index names (helps with code review and conflict resolution).
- Provides a small in-process cache (flask-caching) for inexpensive memoization.

Do **not** import the application here. Extensions are initialized by `create_app`
or the root app initializer via `ext.init_app(app)`.
"""
from __future__ import annotations

from flask_caching import Cache
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect, generate_csrf
try:
    from flask_limiter import Limiter  # type: ignore
    from flask_limiter.util import get_remote_address  # type: ignore
    _limiter_available = True
except ImportError:  # pragma: no cover
    Limiter = None  # type: ignore
    get_remote_address = None  # type: ignore
    _limiter_available = False
from sqlalchemy import MetaData

# Stable names for constraints/indexes so Alembic migrations are predictable
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# Attach the naming convention to SQLAlchemy's MetaData
metadata = MetaData(naming_convention=NAMING_CONVENTION)

# Core extensions (initialized in app factory)
db: SQLAlchemy = SQLAlchemy(metadata=metadata)
migrate: Migrate = Migrate()
csrf: CSRFProtect = CSRFProtect()

# Lightweight in-memory cache (you can swap to Redis/Memcached later via env)
cache: Cache = Cache(config={
    "CACHE_TYPE": "SimpleCache",
    "CACHE_DEFAULT_TIMEOUT": 600,  # 10 minutes
})
limiter = Limiter(key_func=get_remote_address) if _limiter_available else None
login_manager: LoginManager = LoginManager()

__all__ = ["db", "migrate", "cache", "csrf", "limiter", "login_manager", "NAMING_CONVENTION", "metadata", "generate_csrf"]
