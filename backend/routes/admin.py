"""Legacy shim for admin routes."""

from core.routes import admin as _admin  # noqa: F401
from core.routes.admin import *  # noqa: F401,F403

__all__ = getattr(_admin, "__all__", [])
