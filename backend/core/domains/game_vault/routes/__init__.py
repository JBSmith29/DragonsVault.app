"""Game Vault routes — a single self-contained blueprint (page + JSON API)."""

from __future__ import annotations

from .game_vault import game_vault_bp

_registered = False


def register_web_routes() -> None:
    """Importing this module binds the handlers to the blueprint."""
    global _registered
    if _registered:
        return
    _registered = True
    from . import game_vault  # noqa: F401


def register_api_routes() -> None:
    # The API lives on the same blueprint; nothing extra to bind.
    return None


__all__ = ["game_vault_bp", "register_web_routes", "register_api_routes"]
