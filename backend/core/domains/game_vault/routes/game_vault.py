"""Game Vault blueprint: the page plus its JSON API.

Everything is scoped to ``current_user.id`` and confined to the ``gv_*`` tables.
"""

from __future__ import annotations

import os

from flask import Blueprint, current_app, jsonify, render_template, request
from flask_login import current_user, login_required

from ..models import KNOWN_SOURCES, WIN_CONDITIONS
from ..services import vault_service as svc

_TEMPLATES = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")

game_vault_bp = Blueprint(
    "game_vault",
    __name__,
    url_prefix="/game-vault",
    template_folder=_TEMPLATES,
)


def _owner() -> int:
    return int(current_user.id)


def _body() -> dict:
    return request.get_json(silent=True) or {}


def _err(message: str, status: int = 400):
    return jsonify({"error": message}), status


def _guard(fn):
    """Wrap an API handler: translate VaultError->400 and crashes->500 JSON."""
    from functools import wraps

    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except svc.VaultError as exc:
            return _err(str(exc), 400)
        except Exception:  # pragma: no cover - defensive
            current_app.logger.exception("game_vault: %s failed", fn.__name__)
            return _err("Something went wrong. Please try again.", 500)

    return wrapper


# --------------------------------------------------------------------------- #
# Page
# --------------------------------------------------------------------------- #
@game_vault_bp.route("/")
@login_required
def index():
    return render_template(
        "game_vault/index.html",
        win_conditions=WIN_CONDITIONS,
        known_sources=KNOWN_SOURCES,
    )


# --------------------------------------------------------------------------- #
# Aggregate state (one round-trip for initial render)
# --------------------------------------------------------------------------- #
@game_vault_bp.get("/api/state")
@login_required
@_guard
def api_state():
    owner = _owner()
    return jsonify(
        {
            "players": svc.list_players(owner),
            "games": svc.list_games(owner),
            "stats": svc.compute_stats(owner),
        }
    )


# --------------------------------------------------------------------------- #
# Players
# --------------------------------------------------------------------------- #
@game_vault_bp.get("/api/players")
@login_required
@_guard
def api_players():
    return jsonify({"players": svc.list_players(_owner())})


@game_vault_bp.post("/api/players")
@login_required
@_guard
def api_create_player():
    body = _body()
    player = svc.create_player(
        _owner(),
        body.get("name"),
        note=body.get("note"),
        color=body.get("color"),
    )
    return jsonify({"player": player.to_dict(include_decks=True)}), 201


@game_vault_bp.patch("/api/players/<int:player_id>")
@login_required
@_guard
def api_update_player(player_id: int):
    body = _body()
    fields = {k: body[k] for k in ("name", "note", "color") if k in body}
    player = svc.update_player(_owner(), player_id, **fields)
    return jsonify({"player": player.to_dict(include_decks=True)})


@game_vault_bp.delete("/api/players/<int:player_id>")
@login_required
@_guard
def api_delete_player(player_id: int):
    svc.delete_player(_owner(), player_id)
    return jsonify({"ok": True})


# --------------------------------------------------------------------------- #
# Decks
# --------------------------------------------------------------------------- #
@game_vault_bp.get("/api/source-decks")
@login_required
@_guard
def api_source_decks():
    source = (request.args.get("source") or "").strip().lower()
    username = (request.args.get("username") or "").strip()
    if not username:
        return _err("Enter a username.")
    return jsonify({"decks": svc.list_source_decks(source, username)})


@game_vault_bp.post("/api/players/<int:player_id>/decks")
@login_required
@_guard
def api_import_deck(player_id: int):
    body = _body()
    deck = svc.import_deck(
        _owner(),
        player_id,
        url=(body.get("url") or "").strip() or None,
        source=(body.get("source") or "").strip().lower() or None,
        deck_ref=(str(body.get("source_id")).strip() if body.get("source_id") else None),
    )
    return jsonify({"deck": deck.to_dict()}), 201


@game_vault_bp.post("/api/decks/<int:deck_id>/sync")
@login_required
@_guard
def api_sync_deck(deck_id: int):
    deck = svc.sync_deck(_owner(), deck_id)
    return jsonify({"deck": deck.to_dict()})


@game_vault_bp.post("/api/decks/sync-all")
@login_required
@_guard
def api_sync_all():
    return jsonify({"result": svc.sync_all_decks(_owner())})


@game_vault_bp.delete("/api/decks/<int:deck_id>")
@login_required
@_guard
def api_delete_deck(deck_id: int):
    svc.delete_deck(_owner(), deck_id)
    return jsonify({"ok": True})


# --------------------------------------------------------------------------- #
# Games
# --------------------------------------------------------------------------- #
@game_vault_bp.get("/api/games")
@login_required
@_guard
def api_games():
    return jsonify({"games": svc.list_games(_owner())})


@game_vault_bp.post("/api/games")
@login_required
@_guard
def api_create_game():
    body = _body()
    game = svc.create_game(
        _owner(),
        played_at=body.get("played_at"),
        format=body.get("format"),
        turns=body.get("turns"),
        duration_minutes=body.get("duration_minutes"),
        win_condition=body.get("win_condition"),
        infinite_win=body.get("infinite_win"),
        notes=body.get("notes"),
        participants=body.get("participants"),
    )
    return jsonify({"game": game.to_dict()}), 201


@game_vault_bp.patch("/api/games/<int:game_id>")
@login_required
@_guard
def api_update_game(game_id: int):
    body = _body()
    game = svc.update_game(
        _owner(),
        game_id,
        played_at=body.get("played_at"),
        turns=body.get("turns"),
        duration_minutes=body.get("duration_minutes"),
        win_condition=body.get("win_condition"),
        infinite_win=body.get("infinite_win"),
        notes=body.get("notes"),
        participants=body.get("participants"),
    )
    return jsonify({"game": game.to_dict()})


@game_vault_bp.delete("/api/games/<int:game_id>")
@login_required
@_guard
def api_delete_game(game_id: int):
    svc.delete_game(_owner(), game_id)
    return jsonify({"ok": True})


# --------------------------------------------------------------------------- #
# Deck mapping
# --------------------------------------------------------------------------- #
@game_vault_bp.get("/api/deck-map")
@login_required
@_guard
def api_deck_map():
    return jsonify(svc.deck_mapping_overview(_owner()))


@game_vault_bp.post("/api/deck-map")
@login_required
@_guard
def api_apply_deck_map():
    body = _body()
    return jsonify({"result": svc.apply_deck_mapping(_owner(), body.get("mappings"))})


# --------------------------------------------------------------------------- #
# Stats
# --------------------------------------------------------------------------- #
@game_vault_bp.get("/api/stats")
@login_required
@_guard
def api_stats():
    return jsonify({"stats": svc.compute_stats(_owner())})


__all__ = ["game_vault_bp"]
