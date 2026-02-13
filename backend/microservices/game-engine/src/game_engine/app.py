from __future__ import annotations

import uuid

from flask import Flask, jsonify, request, g
from sqlalchemy import delete, select, text

from .auth import require_auth
from .card_data_client import get_card_data_client
from .config import load_config
from .db import ensure_tables, get_engine, get_session_factory, ping_db
from .engine import apply_action, new_game_state
from .models import EngineDeck, EngineDeckCard, Game, GameAction, GameEvent, GamePlayer


def _next_event_seq(session, game_id: str) -> int:
    latest = session.execute(
        select(GameEvent.seq).where(GameEvent.game_id == game_id).order_by(GameEvent.seq.desc())
    ).scalars().first()
    return int(latest or 0) + 1


def _state_has_player(state: dict, user_id: int) -> bool:
    for player in state.get("players", []):
        if int(player.get("user_id") or 0) == int(user_id):
            return True
    return False


def _state_add_player(state: dict, user_id: int, *, format_name: str) -> None:
    if _state_has_player(state, user_id):
        return
    player_payload = {
        "user_id": int(user_id),
        "life": 40 if format_name.lower() == "commander" else 20,
        "commander_damage": {},
        "zones": {
            "library": [],
            "hand": [],
            "battlefield": [],
            "graveyard": [],
            "exile": [],
            "command": [],
        },
    }
    state.setdefault("players", []).append(player_payload)
    meta = state.setdefault("meta", {})
    lands = meta.setdefault("lands_played", {})
    lands[str(user_id)] = 0
    turn = state.setdefault("turn", {})
    if turn.get("active_player") is None:
        turn["active_player"] = user_id
    if turn.get("priority_player") is None:
        turn["priority_player"] = user_id


def _fetch_folder(session, folder_id: int, user_id: int) -> dict | None:
    row = session.execute(
        text(
            "SELECT id, name, owner_user_id, is_proxy "
            "FROM public.folder "
            "WHERE id = :folder_id AND archived_at IS NULL"
        ),
        {"folder_id": folder_id},
    ).mappings().first()
    if not row:
        return None
    if int(row["owner_user_id"] or 0) == int(user_id):
        return dict(row)
    shared = session.execute(
        text(
            "SELECT 1 FROM public.folder_share "
            "WHERE folder_id = :folder_id AND shared_user_id = :user_id"
        ),
        {"folder_id": folder_id, "user_id": user_id},
    ).scalar()
    if shared:
        return dict(row)
    return None


def _fetch_folder_cards(session, folder_id: int) -> list[dict]:
    rows = session.execute(
        text(
            "SELECT oracle_id, name, quantity, type_line, oracle_text, mana_value, "
            "colors, color_identity, layout, faces_json "
            "FROM public.cards "
            "WHERE folder_id = :folder_id AND archived_at IS NULL"
        ),
        {"folder_id": folder_id},
    ).mappings().all()
    return [dict(row) for row in rows]


def _sync_engine_deck(session, folder_id: int, user_id: int) -> dict:
    folder = _fetch_folder(session, folder_id, user_id)
    if not folder:
        return {"ok": False, "error": "folder_not_found"}

    deck = session.execute(
        select(EngineDeck).where(EngineDeck.folder_id == folder_id)
    ).scalars().first()
    if deck is None:
        deck = EngineDeck(
            folder_id=folder_id,
            owner_user_id=folder["owner_user_id"],
            name=folder["name"] or "Deck",
            is_proxy=bool(folder.get("is_proxy")),
        )
        session.add(deck)
        session.flush()
    else:
        deck.owner_user_id = folder["owner_user_id"]
        deck.name = folder["name"] or deck.name
        deck.is_proxy = bool(folder.get("is_proxy"))

    session.execute(delete(EngineDeckCard).where(EngineDeckCard.deck_id == deck.id))

    cards = _fetch_folder_cards(session, folder_id)
    client = get_card_data_client()
    total_cards = 0
    for entry in cards:
        quantity = int(entry.get("quantity") or 1)
        total_cards += quantity
        oracle_id = entry.get("oracle_id")
        oracle = client.fetch_oracle(oracle_id) if oracle_id else None
        card_payload = {
            "oracle_id": oracle_id,
            "name": oracle.get("name") if oracle else entry.get("name") or "Card",
            "quantity": quantity,
            "type_line": oracle.get("type_line") if oracle else entry.get("type_line"),
            "oracle_text": oracle.get("oracle_text") if oracle else entry.get("oracle_text"),
            "mana_cost": oracle.get("mana_cost") if oracle else None,
            "cmc": oracle.get("cmc") if oracle else entry.get("mana_value"),
            "colors": oracle.get("colors") if oracle else None,
            "color_identity": oracle.get("color_identity") if oracle else None,
            "layout": oracle.get("layout") if oracle else entry.get("layout"),
            "card_faces": oracle.get("card_faces") if oracle else entry.get("faces_json"),
            "power": oracle.get("power") if oracle else None,
            "toughness": oracle.get("toughness") if oracle else None,
            "loyalty": oracle.get("loyalty") if oracle else None,
            "defense": oracle.get("defense") if oracle else None,
        }
        session.add(
            EngineDeckCard(
                deck_id=deck.id,
                oracle_id=card_payload["oracle_id"],
                name=card_payload["name"],
                quantity=card_payload["quantity"],
                type_line=card_payload["type_line"],
                oracle_text=card_payload["oracle_text"],
                mana_cost=card_payload["mana_cost"],
                cmc=card_payload["cmc"],
                colors=card_payload["colors"],
                color_identity=card_payload["color_identity"],
                layout=card_payload["layout"],
                card_faces=card_payload["card_faces"],
                power=card_payload["power"],
                toughness=card_payload["toughness"],
                loyalty=card_payload["loyalty"],
                defense=card_payload["defense"],
            )
        )

    return {
        "ok": True,
        "id": deck.id,
        "deck_id": deck.id,
        "folder_id": deck.folder_id,
        "name": deck.name,
        "total_cards": total_cards,
    }


def create_app() -> Flask:
    config = load_config()
    app = Flask(__name__)
    engine = get_engine(config)

    @app.get("/healthz")
    def healthz():
        return jsonify(status="ok", service=config.service_name, schema=config.database_schema)

    @app.get("/readyz")
    def readyz():
        try:
            ping_db(engine, config.database_schema)
        except Exception:
            return (
                jsonify(status="error", service=config.service_name),
                503,
            )
        return jsonify(status="ready", service=config.service_name)

    @app.get("/v1/ping")
    def ping():
        return jsonify(status="ok", service=config.service_name)

    @app.post("/v1/decks/from-folder")
    @require_auth
    def sync_deck_from_folder():
        payload = request.get_json(silent=True) or {}
        folder_id = payload.get("folder_id")
        if not folder_id:
            return jsonify(status="error", error="folder_id_required"), 400
        session = get_session_factory(config)()
        try:
            ensure_tables(engine)
            result = _sync_engine_deck(session, int(folder_id), int(g.user_id))
            if not result.get("ok"):
                session.rollback()
                return jsonify(status="error", error=result.get("error")), 404
            session.commit()
            return jsonify(status="ok", deck=result)
        finally:
            session.close()

    @app.get("/v1/decks/<int:deck_id>")
    @require_auth
    def get_deck(deck_id: int):
        session = get_session_factory(config)()
        try:
            ensure_tables(engine)
            deck = session.get(EngineDeck, deck_id)
            if not deck:
                return jsonify(status="not_found"), 404
            folder = _fetch_folder(session, deck.folder_id, int(g.user_id))
            if not folder:
                return jsonify(status="forbidden"), 403
            cards = session.execute(
                select(EngineDeckCard).where(EngineDeckCard.deck_id == deck_id)
            ).scalars().all()
            return jsonify(
                status="ok",
                deck={
                    "id": deck.id,
                    "folder_id": deck.folder_id,
                    "name": deck.name,
                    "is_proxy": deck.is_proxy,
                    "total_cards": sum(card.quantity for card in cards),
                },
                cards=[
                    {
                        "oracle_id": card.oracle_id,
                        "name": card.name,
                        "quantity": card.quantity,
                        "type_line": card.type_line,
                        "oracle_text": card.oracle_text,
                        "mana_cost": card.mana_cost,
                        "cmc": card.cmc,
                        "colors": card.colors,
                        "color_identity": card.color_identity,
                        "layout": card.layout,
                        "card_faces": card.card_faces,
                        "power": card.power,
                        "toughness": card.toughness,
                        "loyalty": card.loyalty,
                        "defense": card.defense,
                    }
                    for card in cards
                ],
            )
        finally:
            session.close()

    @app.post("/v1/games")
    @require_auth
    def create_game():
        payload = request.get_json(silent=True) or {}
        format_name = payload.get("format") or "commander"
        players = payload.get("players") or []
        player_ids = [int(p) for p in players if str(p).isdigit()]
        if not player_ids:
            player_ids = [int(g.user_id)]
        if int(g.user_id) not in player_ids:
            return jsonify(status="error", error="owner_must_be_player"), 403

        session = get_session_factory(config)()
        try:
            ensure_tables(engine)
            game_id = uuid.uuid4().hex
            state = new_game_state(format_name=format_name, player_ids=player_ids)
            game = Game(
                id=game_id,
                format=format_name,
                status="waiting",
                rules_version=config.rules_version,
                state=state,
            )
            session.add(game)
            for index, player_id in enumerate(player_ids):
                session.add(
                    GamePlayer(
                        game_id=game_id,
                        user_id=player_id,
                        seat_index=index,
                    )
                )
            event = GameEvent(
                game_id=game_id,
                seq=1,
                event_type="game_created",
                payload={"format": format_name, "players": player_ids},
            )
            session.add(event)
            session.commit()
            return jsonify(status="ok", game_id=game_id, state=state)
        finally:
            session.close()

    @app.get("/v1/games/<game_id>")
    @require_auth
    def get_game(game_id: str):
        session = get_session_factory(config)()
        try:
            ensure_tables(engine)
            game = session.get(Game, game_id)
            if not game:
                return jsonify(status="not_found"), 404
            players = session.execute(
                select(GamePlayer).where(GamePlayer.game_id == game_id).order_by(GamePlayer.seat_index)
            ).scalars().all()
            if not any(player.user_id == int(g.user_id) for player in players):
                return jsonify(status="forbidden"), 403
            return jsonify(
                status="ok",
                game={
                    "id": game.id,
                    "format": game.format,
                    "status": game.status,
                    "rules_version": game.rules_version,
                    "state": game.state,
                },
                players=[
                    {
                        "user_id": player.user_id,
                        "seat_index": player.seat_index,
                        "status": player.status,
                        "deck_ref": player.deck_ref,
                    }
                    for player in players
                ],
            )
        finally:
            session.close()

    @app.post("/v1/games/<game_id>/join")
    @require_auth
    def join_game(game_id: str):
        session = get_session_factory(config)()
        try:
            ensure_tables(engine)
            game = session.get(Game, game_id)
            if not game:
                return jsonify(status="not_found"), 404
            if game.status != "waiting":
                return jsonify(status="error", error="game_already_started"), 409
            existing = session.execute(
                select(GamePlayer).where(
                    GamePlayer.game_id == game_id,
                    GamePlayer.user_id == int(g.user_id),
                )
            ).scalars().first()
            if existing:
                return jsonify(
                    status="ok",
                    game_id=game_id,
                    player_id=existing.user_id,
                    seat_index=existing.seat_index,
                    joined=False,
                )
            seat_index = session.execute(
                select(GamePlayer.seat_index)
                .where(GamePlayer.game_id == game_id)
                .order_by(GamePlayer.seat_index.desc())
            ).scalars().first()
            next_seat = int(seat_index or -1) + 1
            session.add(
                GamePlayer(
                    game_id=game_id,
                    user_id=int(g.user_id),
                    seat_index=next_seat,
                )
            )
            state = dict(game.state or {})
            format_name = state.get("format") or game.format or "commander"
            _state_add_player(state, int(g.user_id), format_name=str(format_name))
            game.state = state
            event = GameEvent(
                game_id=game_id,
                seq=_next_event_seq(session, game_id),
                event_type="player_joined",
                payload={"user_id": int(g.user_id)},
            )
            session.add(event)
            session.commit()
            return jsonify(
                status="ok",
                game_id=game_id,
                player_id=int(g.user_id),
                seat_index=next_seat,
                joined=True,
            )
        finally:
            session.close()

    @app.post("/v1/games/<game_id>/actions")
    @require_auth
    def submit_action(game_id: str):
        payload = request.get_json(silent=True) or {}
        player_id = payload.get("player_id") or g.user_id
        action_type = payload.get("action_type")
        if not player_id or not action_type:
            return jsonify(status="error", error="player_id_and_action_type_required"), 400

        session = get_session_factory(config)()
        try:
            ensure_tables(engine)
            game = session.get(Game, game_id)
            if not game:
                return jsonify(status="not_found"), 404
            player = session.execute(
                select(GamePlayer).where(
                    GamePlayer.game_id == game_id, GamePlayer.user_id == int(player_id)
                )
            ).scalars().first()
            if not player:
                return jsonify(status="forbidden"), 403
            action_payload = payload.get("payload") or {}
            if action_type == "load_deck" and action_payload.get("deck_id"):
                deck_id = int(action_payload.get("deck_id"))
                deck = session.get(EngineDeck, deck_id)
                if not deck:
                    return jsonify(status="error", error="deck_not_found"), 404
                if not _fetch_folder(session, deck.folder_id, int(g.user_id)):
                    return jsonify(status="forbidden"), 403
                cards = session.execute(
                    select(EngineDeckCard).where(EngineDeckCard.deck_id == deck_id)
                ).scalars().all()
                expanded = []
                for card in cards:
                    for _ in range(max(1, int(card.quantity or 1))):
                        expanded.append(
                            {
                                "oracle_id": card.oracle_id,
                                "name": card.name,
                                "type_line": card.type_line,
                                "oracle_text": card.oracle_text,
                                "mana_cost": card.mana_cost,
                                "cmc": card.cmc,
                                "colors": card.colors,
                                "color_identity": card.color_identity,
                                "layout": card.layout,
                                "card_faces": card.card_faces,
                                "power": card.power,
                                "toughness": card.toughness,
                                "loyalty": card.loyalty,
                                "defense": card.defense,
                            }
                        )
                action_payload = {"cards": expanded, "shuffle": action_payload.get("shuffle", True)}
            action = GameAction(
                game_id=game_id,
                player_id=int(player_id),
                action_type=str(action_type),
                payload=action_payload,
                status="pending",
            )
            session.add(action)
            result = apply_action(
                game.state,
                {
                    "player_id": int(player_id),
                    "action_type": action_type,
                    "payload": action_payload,
                },
            )
            if result.get("ok"):
                game.state = dict(result["state"])
                action.status = "applied"
                events = result.get("events") or []
                next_seq = _next_event_seq(session, game_id)
                for event in events:
                    session.add(
                        GameEvent(
                            game_id=game_id,
                            seq=next_seq,
                            event_type=event.get("type", "event"),
                            payload=event,
                        )
                    )
                    next_seq += 1
            else:
                action.status = "failed"
                action.error = result.get("error") or "action_failed"
            session.commit()
            return jsonify(status=action.status, action_id=action.id, result=result)
        finally:
            session.close()

    @app.get("/v1/games/<game_id>/events")
    @require_auth
    def list_events(game_id: str):
        since = request.args.get("since", type=int)
        session = get_session_factory(config)()
        try:
            ensure_tables(engine)
            player = session.execute(
                select(GamePlayer).where(
                    GamePlayer.game_id == game_id, GamePlayer.user_id == int(g.user_id)
                )
            ).scalars().first()
            if not player:
                return jsonify(status="forbidden"), 403
            query = select(GameEvent).where(GameEvent.game_id == game_id)
            if since is not None:
                query = query.where(GameEvent.seq > since)
            events = session.execute(query.order_by(GameEvent.seq)).scalars().all()
            return jsonify(
                status="ok",
                events=[
                    {
                        "seq": event.seq,
                        "event_type": event.event_type,
                        "payload": event.payload,
                        "created_at": event.created_at.isoformat(),
                    }
                    for event in events
                ],
            )
        finally:
            session.close()

    return app
