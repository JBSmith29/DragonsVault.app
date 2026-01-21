"""Legacy shim for scryfall routes."""

from core.domains.cards.routes import scryfall as _scryfall  # noqa: F401
from core.domains.cards.routes.scryfall import *  # noqa: F401,F403

__all__ = getattr(_scryfall, "__all__", [])
