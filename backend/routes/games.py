"""Commander game tracking routes."""

from __future__ import annotations

from flask_login import login_required

from services import game_service
from .base import views


@views.route("/games")
@login_required
def games_landing():
    return game_service.games_landing()


@views.route("/games/logs")
@login_required
def games_overview():
    return game_service.games_overview()


@views.route("/games/export")
@login_required
def games_export():
    return game_service.games_export()


@views.route("/games/import", methods=["POST"])
@login_required
def games_import():
    return game_service.games_import()


@views.route("/games/new", methods=["GET", "POST"])
@login_required
def games_new():
    return game_service.games_new()


@views.route("/games/metrics")
@login_required
def games_metrics():
    return game_service.games_metrics()


@views.route("/games/players", methods=["GET", "POST"])
@login_required
def games_players():
    return game_service.games_players()


@views.route("/games/<int:game_id>")
@login_required
def games_detail(game_id: int):
    return game_service.game_detail(game_id)


@views.route("/games/<int:game_id>/edit", methods=["GET", "POST"])
@login_required
def games_edit(game_id: int):
    return game_service.games_edit(game_id)


__all__ = [
    "games_landing",
    "games_overview",
    "games_export",
    "games_import",
    "games_metrics",
    "games_players",
    "games_new",
    "games_edit",
    "games_detail",
]
