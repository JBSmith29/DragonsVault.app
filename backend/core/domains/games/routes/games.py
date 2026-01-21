"""Commander game tracking routes."""

from __future__ import annotations

from flask_login import login_required

from core.domains.games.services import game_service, games_enhanced
from core.routes.base import views


@views.route("/games")
@login_required
def games_landing():
    return game_service.games_landing()


@views.route("/games/dashboard")
@login_required
def games_dashboard():
    return game_service.games_dashboard()


@views.route("/games/admin")
@login_required
def games_admin():
    return game_service.games_admin()


@views.route("/games/players/streamlined")
@login_required
def games_players_streamlined():
    return games_enhanced.games_streamlined_players()


@views.route("/games/quick-log")
@login_required
def games_quick_log():
    return games_enhanced.games_quick_log()


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


@views.route("/games/import-template")
@login_required
def games_import_template():
    return game_service.games_import_template()


@views.route("/games/new", methods=["GET", "POST"])
@login_required
def games_new():
    return game_service.games_new()


@views.route("/games/metrics")
@login_required
def games_metrics():
    return game_service.games_metrics()


@views.route("/games/metrics/player")
@login_required
def games_metrics_player():
    return game_service.games_metrics_player()


@views.route("/games/metrics/pods")
@login_required
def games_metrics_pods():
    return game_service.games_metrics_pods()


@views.route("/games/metrics/users")
@login_required
def games_metrics_users():
    return game_service.games_metrics_users()


@views.route("/games/metrics/decks")
@login_required
def games_metrics_decks():
    return game_service.games_metrics_decks()


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


@views.route("/games/<int:game_id>/delete", methods=["POST"])
@login_required
def games_delete(game_id: int):
    return game_service.games_delete(game_id)


@views.route("/games/bulk-delete", methods=["POST"])
@login_required
def games_bulk_delete():
    return game_service.games_bulk_delete()


__all__ = [
    "games_landing",
    "games_dashboard",
    "games_admin",
    "games_overview",
    "games_export",
    "games_import",
    "games_import_template",
    "games_metrics",
    "games_metrics_player",
    "games_metrics_pods",
    "games_metrics_users",
    "games_metrics_decks",
    "games_players",
    "games_new",
    "games_edit",
    "games_delete",
    "games_bulk_delete",
    "games_detail",
]
