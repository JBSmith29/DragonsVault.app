"""Legacy shim for folder routes."""

from core.domains.decks.routes import folders as _folders  # noqa: F401
from core.domains.decks.routes.folders import *  # noqa: F401,F403

__all__ = getattr(_folders, "__all__", [])
