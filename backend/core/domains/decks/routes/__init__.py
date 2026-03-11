"""Decks domain routes."""

from __future__ import annotations

_web_routes_registered = False
_api_routes_registered = False


def register_web_routes() -> None:
    """Import deck web modules so route decorators bind to the views blueprint."""
    global _web_routes_registered
    if _web_routes_registered:
        return
    _web_routes_registered = True

    from . import build  # noqa: F401
    from . import folders  # noqa: F401
    from . import wishlist  # noqa: F401


def register_api_routes() -> None:
    """Register deck API handlers.

    Decks keeps some legacy `/api/*` handlers inside the web route modules.
    """
    global _api_routes_registered
    if _api_routes_registered:
        return
    _api_routes_registered = True
    register_web_routes()


__all__ = ["register_web_routes", "register_api_routes"]
