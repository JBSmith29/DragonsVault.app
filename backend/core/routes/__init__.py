"""Core route modules (staged migration)."""

from __future__ import annotations

from .base import views
from .api import api_bp

_routes_registered = False


def register_routes() -> None:
    """Import route modules so blueprints pick up their handlers."""
    global _routes_registered
    if _routes_registered:
        return
    _routes_registered = True

    from . import admin  # noqa: F401
    from . import frontend  # noqa: F401
    from . import ops  # noqa: F401
    from core.domains import register_domains

    register_domains()


__all__ = ["views", "api_bp", "register_routes"]
