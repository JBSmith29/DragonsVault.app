"""Legacy shim for base routes and helpers."""

from core.routes import base as _base  # noqa: F401
from core.routes.base import *  # noqa: F401,F403

__all__ = getattr(_base, "__all__", [])
