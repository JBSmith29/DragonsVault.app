"""Business domains package (staged migration)."""

from __future__ import annotations


def register_domains() -> None:
    """Import domain route modules to register legacy routes."""
    from .cards import routes as _cards_routes  # noqa: F401
    from .decks import routes as _decks_routes  # noqa: F401
    from .games import routes as _games_routes  # noqa: F401
    from .users import routes as _users_routes  # noqa: F401


__all__ = ["register_domains"]
