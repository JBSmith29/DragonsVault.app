"""Cards domain routes."""

from __future__ import annotations

_web_routes_registered = False
_api_routes_registered = False


def register_web_routes() -> None:
    """Import cards web modules so route decorators bind to the views blueprint."""
    global _web_routes_registered
    if _web_routes_registered:
        return
    _web_routes_registered = True

    from . import cards  # noqa: F401
    from . import card_detail  # noqa: F401
    from . import importer  # noqa: F401
    from . import list_checker  # noqa: F401
    from . import scryfall  # noqa: F401
    from . import sets  # noqa: F401


def register_api_routes() -> None:
    """Register cards API handlers.

    Cards keeps legacy `/api/*` handlers collocated with web route modules, so API
    registration currently delegates to the web registration path.
    """
    global _api_routes_registered
    if _api_routes_registered:
        return
    _api_routes_registered = True
    register_web_routes()


__all__ = ["register_web_routes", "register_api_routes"]
