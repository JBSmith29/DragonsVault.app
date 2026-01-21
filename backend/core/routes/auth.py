"""Compatibility shim for auth routes."""

from core.domains.users.routes import auth as _auth  # noqa: F401
from core.domains.users.routes.auth import *  # noqa: F401,F403

__all__ = getattr(_auth, "__all__", [])
