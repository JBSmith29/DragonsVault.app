"""Legacy shim for frontend routes."""

from core.routes import frontend as _frontend  # noqa: F401
from core.routes.frontend import *  # noqa: F401,F403

__all__ = getattr(_frontend, "__all__", [])
