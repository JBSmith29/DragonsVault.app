"""Legacy shim for cards routes."""

from core.domains.cards.routes import cards as _cards  # noqa: F401
from core.domains.cards.routes.cards import *  # noqa: F401,F403

__all__ = getattr(_cards, "__all__", [])
