"""Legacy shim for import routes."""

from core.domains.cards.routes import importer as _importer  # noqa: F401
from core.domains.cards.routes.importer import *  # noqa: F401,F403

__all__ = getattr(_importer, "__all__", [])
