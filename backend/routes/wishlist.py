"""Legacy shim for wishlist routes."""

from core.domains.decks.routes import wishlist as _wishlist  # noqa: F401
from core.domains.decks.routes.wishlist import *  # noqa: F401,F403

__all__ = getattr(_wishlist, "__all__", [])
