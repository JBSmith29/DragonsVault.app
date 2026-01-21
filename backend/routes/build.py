"""Legacy shim for build routes."""

from core.domains.decks.routes import build as _build  # noqa: F401
from core.domains.decks.routes.build import *  # noqa: F401,F403

__all__ = getattr(_build, "__all__", [])
