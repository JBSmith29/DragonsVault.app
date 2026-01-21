"""Legacy shim for card detail routes."""

from core.domains.cards.routes import card_detail as _card_detail  # noqa: F401
from core.domains.cards.routes.card_detail import *  # noqa: F401,F403

__all__ = getattr(_card_detail, "__all__", [])
