"""Legacy shim for ops routes."""

from core.routes import ops as _ops  # noqa: F401
from core.routes.ops import *  # noqa: F401,F403

__all__ = getattr(_ops, "__all__", [])
