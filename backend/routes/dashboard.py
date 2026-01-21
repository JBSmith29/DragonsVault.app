"""Legacy shim for dashboard routes."""

from core.domains.users.routes import dashboard as _dashboard  # noqa: F401
from core.domains.users.routes.dashboard import *  # noqa: F401,F403

__all__ = getattr(_dashboard, "__all__", [])
