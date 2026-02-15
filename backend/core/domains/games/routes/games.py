"""Commander game tracking routes."""

from __future__ import annotations

from flask import jsonify, render_template, request
from flask_login import current_user, login_required

from core.domains.cards.services import scryfall_cache as sc
from core.domains.games.services import game_engine_client, game_service, games_enhanced
from core.routes.base import views
from core.shared.utils.assets import static_url


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


@views.route("/games/engine")
@login_required
def game_engine():
    return render_template(
        "games/game_engine.html",
        engine_enabled=game_engine_client.engine_service_enabled(),
        deck_options=game_service.accessible_deck_options(current_user.id, commander_only=True),
    )


@views.route("/games/engine/play")
@login_required
def game_engine_table():
    game_id = (request.args.get("game_id") or "").strip()
    return render_template(
        "games/game_engine_table.html",
        engine_enabled=game_engine_client.engine_service_enabled(),
        game_id=game_id,
    )


@views.post("/api/game-engine/games")
@login_required
def api_game_engine_create():
    payload = request.get_json(silent=True) or {}
    format_name = (payload.get("format") or "commander").strip().lower()
    players_raw = payload.get("players") or []
    players = [int(p) for p in players_raw if str(p).isdigit()]
    try:
        result = game_engine_client.create_game(current_user.id, format_name=format_name, players=players or None)
    except game_engine_client.GameEngineError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 502
    return jsonify({"ok": True, "result": result})


@views.get("/api/game-engine/ping")
@login_required
def api_game_engine_ping():
    try:
        result = game_engine_client.ping(current_user.id)
    except game_engine_client.GameEngineError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 502
    return jsonify({"ok": True, "result": result})


@views.get("/api/game-engine/decks/options")
@login_required
def api_game_engine_deck_options():
    options = game_service.accessible_deck_options(current_user.id, commander_only=True)
    return jsonify({"ok": True, "result": {"options": options}})


@views.post("/api/game-engine/games/<game_id>/join")
@login_required
def api_game_engine_join(game_id: str):
    try:
        result = game_engine_client.join_game(current_user.id, game_id)
    except game_engine_client.GameEngineError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 502
    return jsonify({"ok": True, "result": result})


@views.get("/api/game-engine/games/<game_id>")
@login_required
def api_game_engine_get(game_id: str):
    try:
        result = game_engine_client.get_game(current_user.id, game_id)
    except game_engine_client.GameEngineError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 502
    return jsonify({"ok": True, "result": result})


@views.get("/api/game-engine/games/<game_id>/events")
@login_required
def api_game_engine_events(game_id: str):
    since = request.args.get("since", type=int)
    try:
        result = game_engine_client.list_events(current_user.id, game_id, since=since)
    except game_engine_client.GameEngineError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 502
    return jsonify({"ok": True, "result": result})


@views.post("/api/game-engine/cards/images")
@login_required
def api_game_engine_card_images():
    payload = request.get_json(silent=True) or {}
    raw_ids = payload.get("oracle_ids") or []
    oracle_ids = [str(oid).strip() for oid in raw_ids if oid]
    if not oracle_ids:
        return jsonify({"ok": True, "result": {"images": {}}})

    placeholder = static_url("img/card-back-placeholder.png")
    images: dict[str, dict[str, str]] = {}
    try:
        sc.ensure_cache_loaded()
    except Exception:
        # Cache failure shouldn't block the UI; we fall back to placeholders.
        for oid in oracle_ids:
            images[oid] = {"small": placeholder, "normal": placeholder, "large": placeholder}
        return jsonify({"ok": True, "result": {"images": images}})

    for oid in oracle_ids:
        img_pack = {}
        try:
            prints = sc.prints_for_oracle(oid) or []
            if prints:
                img_pack = sc.image_for_print(prints[0]) or {}
        except Exception:
            img_pack = {}
        small = img_pack.get("small") or img_pack.get("normal") or img_pack.get("large") or placeholder
        normal = img_pack.get("normal") or img_pack.get("large") or small or placeholder
        large = img_pack.get("large") or img_pack.get("normal") or normal or placeholder
        images[oid] = {"small": small, "normal": normal, "large": large}

    return jsonify({"ok": True, "result": {"images": images}})


@views.post("/api/game-engine/games/<game_id>/actions")
@login_required
def api_game_engine_action(game_id: str):
    payload = request.get_json(silent=True) or {}
    action_type = (payload.get("action_type") or "").strip()
    action_payload = payload.get("payload") or {}
    if not action_type:
        return jsonify({"ok": False, "error": "action_type_required"}), 400
    try:
        result = game_engine_client.submit_action(
            current_user.id,
            game_id,
            action_type=action_type,
            payload=action_payload,
        )
    except game_engine_client.GameEngineError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 502
    return jsonify({"ok": True, "result": result})


@views.post("/api/game-engine/decks/from-folder")
@login_required
def api_game_engine_sync_deck():
    payload = request.get_json(silent=True) or {}
    folder_id = payload.get("folder_id")
    if not folder_id:
        return jsonify({"ok": False, "error": "folder_id_required"}), 400
    try:
        result = game_engine_client.sync_deck_from_folder(current_user.id, int(folder_id))
    except game_engine_client.GameEngineError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 502
    return jsonify({"ok": True, "result": result})


@views.route("/games/manual-decks/update", methods=["POST"])
@login_required
def games_manual_deck_update():
    return game_service.games_manual_deck_update()


@views.route("/games/deck-bracket/update", methods=["POST"])
@login_required
def games_deck_bracket_update():
    return game_service.games_deck_bracket_update()


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


@views.route("/gamedashboard")
def gamedashboard():
    return game_service.games_metrics_public_dashboard()

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
    "gamedashboard",
    "games_admin",
    "games_overview",
    "games_manual_deck_update",
    "games_deck_bracket_update",
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
    "api_game_engine_ping",
    "api_game_engine_deck_options",
    "games_detail",
]
