"""Core route modules (staged migration)."""

from __future__ import annotations

from .base import views
from .api import api_bp

_web_routes_registered = False
_api_routes_registered = False
_routes_registered = False


def register_web_routes() -> None:
    """Import web-facing route modules so `views` picks up handlers."""
    global _web_routes_registered
    if _web_routes_registered:
        return
    _web_routes_registered = True

    from . import admin  # noqa: F401
    from . import frontend  # noqa: F401
    from core.domains import register_web_domains

    register_web_domains()


def register_api_routes() -> None:
    """Import API-facing route modules so API blueprints pick up handlers."""
    global _api_routes_registered
    if _api_routes_registered:
        return
    _api_routes_registered = True

    from . import ops  # noqa: F401
    from core.domains import register_api_domains

    register_api_domains()


def register_routes() -> None:
    """Import route modules so all blueprints pick up their handlers."""
    global _routes_registered
    if _routes_registered:
        return
    _routes_registered = True
    register_web_routes()
    register_api_routes()


def web_blueprints():
    """Return web-facing blueprints mounted by the application factory."""
    return (views,)


def api_blueprints():
    """Return API blueprints and their versioned URL prefixes."""
    from core.domains.games.routes.games_api import games_api

    return (
        (api_bp, "/api/v1"),
        (games_api, "/api/v1/games"),
    )


__all__ = [
    "views",
    "api_bp",
    "register_web_routes",
    "register_api_routes",
    "register_routes",
    "web_blueprints",
    "api_blueprints",
]
