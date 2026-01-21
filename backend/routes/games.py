"""Legacy shim for games routes."""

from core.domains.games.routes import games as _games  # noqa: F401
from core.domains.games.routes.games import *  # noqa: F401,F403

__all__ = getattr(_games, "__all__", [])
