"""Business domains package (staged migration)."""

from __future__ import annotations

_web_domains_registered = False
_api_domains_registered = False
_domains_registered = False


def register_web_domains() -> None:
    """Register domain modules that bind handlers on web/UI blueprints."""
    global _web_domains_registered
    if _web_domains_registered:
        return
    _web_domains_registered = True

    from .cards.routes import register_web_routes as register_cards_web_routes
    from .decks.routes import register_web_routes as register_decks_web_routes
    from .games.routes import register_web_routes as register_games_web_routes
    from .users.routes import register_web_routes as register_users_web_routes

    register_cards_web_routes()
    register_decks_web_routes()
    register_games_web_routes()
    register_users_web_routes()


def register_api_domains() -> None:
    """Register domain modules that bind handlers on API blueprints."""
    global _api_domains_registered
    if _api_domains_registered:
        return
    _api_domains_registered = True

    from .cards.routes import register_api_routes as register_cards_api_routes
    from .decks.routes import register_api_routes as register_decks_api_routes
    from .games.routes import register_api_routes as register_games_api_routes
    from .users.routes import register_api_routes as register_users_api_routes

    register_cards_api_routes()
    register_decks_api_routes()
    register_games_api_routes()
    register_users_api_routes()


def register_domains() -> None:
    """Register all domain route modules."""
    global _domains_registered
    if _domains_registered:
        return
    _domains_registered = True
    register_web_domains()
    register_api_domains()


__all__ = ["register_domains", "register_web_domains", "register_api_domains"]
