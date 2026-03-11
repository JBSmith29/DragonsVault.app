"""Games domain routes."""

from __future__ import annotations

_web_routes_registered = False
_api_routes_registered = False


def register_web_routes() -> None:
    """Import games web modules so route decorators bind to the views blueprint."""
    global _web_routes_registered
    if _web_routes_registered:
        return
    _web_routes_registered = True

    from . import games  # noqa: F401


def register_api_routes() -> None:
    """Import games API modules so route decorators bind to API blueprints."""
    global _api_routes_registered
    if _api_routes_registered:
        return
    _api_routes_registered = True

    from . import games_api  # noqa: F401


__all__ = ["register_web_routes", "register_api_routes"]
