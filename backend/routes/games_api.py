"""Legacy shim for games API routes."""

from core.domains.games.routes import games_api as _games_api  # noqa: F401
from core.domains.games.routes.games_api import *  # noqa: F401,F403

__all__ = getattr(_games_api, "__all__", [])
