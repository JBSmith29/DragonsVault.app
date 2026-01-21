"""Legacy shim for list checker routes."""

from core.domains.cards.routes import list_checker as _list_checker  # noqa: F401
from core.domains.cards.routes.list_checker import *  # noqa: F401,F403

__all__ = getattr(_list_checker, "__all__", [])
