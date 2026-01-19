"""Aggregate blueprint for DragonsVault routes."""

from __future__ import annotations

from .base import views
from .api import api_bp

# Register route modules (import order not critical but keeps sections grouped)
from . import (
    admin,          # noqa: F401
    auth,           # noqa: F401
    build,          # noqa: F401
    card_detail,    # noqa: F401
    cards,          # noqa: F401
    dashboard,      # noqa: F401
    games,          # noqa: F401
    folders,        # noqa: F401
    importer,       # noqa: F401
    list_checker,   # noqa: F401
    scryfall,       # noqa: F401
    sets,           # noqa: F401
    wishlist,       # noqa: F401
    ops,            # noqa: F401
    frontend,       # noqa: F401
)

__all__ = ["views", "api_bp"]
