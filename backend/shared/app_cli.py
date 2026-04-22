"""CLI command registration for the main Flask app."""

from __future__ import annotations

from shared.app_cli_general import register_general_cli_commands
from shared.app_cli_scryfall import register_scryfall_cli_commands
from shared.app_cli_users import register_user_cli_commands


def register_cli_commands(app) -> None:
    register_general_cli_commands(app)
    register_scryfall_cli_commands(app)
    register_user_cli_commands(app)
