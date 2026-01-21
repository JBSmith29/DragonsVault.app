"""Legacy shim for set browsing routes."""

from core.domains.cards.routes import sets as _sets  # noqa: F401
from core.domains.cards.routes.sets import *  # noqa: F401,F403

__all__ = getattr(_sets, "__all__", [])
