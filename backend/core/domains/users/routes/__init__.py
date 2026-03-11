"""Users domain routes."""

from __future__ import annotations

_web_routes_registered = False
_api_routes_registered = False


def register_web_routes() -> None:
    """Import user web modules so route decorators bind to the views blueprint."""
    global _web_routes_registered
    if _web_routes_registered:
        return
    _web_routes_registered = True

    from . import auth  # noqa: F401
    from . import dashboard  # noqa: F401


def register_api_routes() -> None:
    """Register user API handlers.

    User API compatibility endpoints are currently collocated with web route modules.
    """
    global _api_routes_registered
    if _api_routes_registered:
        return
    _api_routes_registered = True
    register_web_routes()


__all__ = ["register_web_routes", "register_api_routes"]
