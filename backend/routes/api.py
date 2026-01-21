"""Legacy shim for API routes."""

from core.routes import api as _api  # noqa: F401
from core.routes.api import *  # noqa: F401,F403

__all__ = getattr(_api, "__all__", [])
