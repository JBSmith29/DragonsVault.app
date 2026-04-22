"""Opening-hand flow for decks, build sessions, and pasted lists."""

from __future__ import annotations

import json
from typing import Optional

from flask import current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user

from core.domains.cards.services import scryfall_cache as sc
from core.domains.decks.services.opening_hand_deck_source_service import (
    _deck_entries_from_build_session,
    _deck_entries_from_folder,
    _deck_entries_from_list,
    _opening_hand_deck_key,
    _opening_hand_deck_options,
    _parse_opening_hand_deck_ref,
)
from core.domains.decks.services.opening_hand_lookup_service import (
    _opening_hand_lookups,
)
from core.domains.decks.services import opening_hand_gameplay_service as gameplay_service
from core.domains.decks.services.opening_hand_payload_service import (
    _client_card_payload,
    _dedupe_opening_hand_tokens,
    _ensure_cache_ready,
    _token_payload,
)
from core.domains.decks.services import opening_hand_state_service as state_service
from core.shared.utils.assets import static_url
from shared.cache.runtime_cache import cache_fetch as _cache_fetch
from shared.validation import ValidationError, log_validation_error

HAND_SIZE = gameplay_service.HAND_SIZE
OPENING_HAND_STATE_SALT = state_service.OPENING_HAND_STATE_SALT
OPENING_HAND_STATE_MAX_AGE_SECONDS = state_service.OPENING_HAND_STATE_MAX_AGE_SECONDS


def _encode_state(payload: dict) -> str:
    secret = current_app.secret_key or current_app.config.get("SECRET_KEY") or "dev"
    return state_service.encode_state(payload, secret_key=secret, salt=OPENING_HAND_STATE_SALT)


def _decode_state(token: str) -> Optional[dict]:
    secret = current_app.secret_key or current_app.config.get("SECRET_KEY") or "dev"
    user_id = current_user.id if current_user and getattr(current_user, "is_authenticated", False) else None
    return state_service.decode_state(
        token,
        secret_key=secret,
        current_user_id=user_id,
        max_age=current_app.config.get(
            "OPENING_HAND_STATE_MAX_AGE_SECONDS",
            OPENING_HAND_STATE_MAX_AGE_SECONDS,
        ),
        salt=OPENING_HAND_STATE_SALT,
    )


def _opening_hand_state_serializer():
    secret = current_app.secret_key or current_app.config.get("SECRET_KEY") or "dev"
    return state_service.opening_hand_state_serializer(secret, salt=OPENING_HAND_STATE_SALT)


def _normalize_opening_hand_state(payload: object) -> Optional[dict]:
    user_id = current_user.id if current_user and getattr(current_user, "is_authenticated", False) else None
    return state_service.normalize_opening_hand_state(payload, current_user_id=user_id)


def _expanded_deck_entries(entries: list[dict]) -> list[dict]:
    return state_service.expanded_deck_entries(entries)


def opening_hand():
    _, deck_options = _opening_hand_deck_options()
    return render_template("decks/opening_hand_landing.html", deck_options=deck_options)


def opening_hand_play():
    if request.method == "GET":
        return redirect(url_for("views.opening_hand"))

    deck_id_raw = (request.form.get("deck_id") or "").strip()
    deck_list_text = (request.form.get("deck_list") or "").strip()
    commander_hint = (request.form.get("commander_name") or "").strip()

    deck_lookup, deck_options = _opening_hand_deck_options()
    deck_key = ""
    selected_deck_name = ""
    commander_cards: list[dict] = []
    deck_refs: list[str] = []
    custom_token_entries_json = json.dumps([], ensure_ascii=True)

    if deck_id_raw:
        try:
            parsed = _parse_opening_hand_deck_ref(deck_id_raw)
        except ValidationError as exc:
            log_validation_error(exc, context="opening_hand_play")
            flash("Invalid deck selection.", "danger")
            return redirect(url_for("views.opening_hand"))
        if parsed:
            source, deck_id = parsed
            deck_key = _opening_hand_deck_key(source, deck_id)
            selected = deck_lookup.get(deck_key)
            if not selected:
                flash("Deck not found.", "warning")
                return redirect(url_for("views.opening_hand"))
            selected_deck_name = selected.get("label") or "Deck"
            deck_list_text = ""
            commander_hint = ""
            deck_refs = [deck_key]
            selected_deck = selected.get("deck")
            commander_cards = selected_deck and getattr(selected_deck, "commander_name", None)
            commander_cards = _deck_entries_from_build_session(deck_id)[3] if source == "build" else _deck_entries_from_folder(deck_id)[3]
    elif deck_list_text:
        selected_deck_name = "Custom list"
        _, entries_from_list, _, commander_cards = _deck_entries_from_list(deck_list_text, commander_hint)
        oracle_ids = {entry.get("oracle_id") for entry in entries_from_list if entry.get("oracle_id")}
        for commander in commander_cards:
            commander_oracle_id = commander.get("oracle_id")
            if commander_oracle_id:
                oracle_ids.add(commander_oracle_id)
        if oracle_ids and _ensure_cache_ready():
            placeholder = static_url("img/card-placeholder.svg")
            token_payloads: list[dict] = []
            token_seen: set[str] = set()
            for oracle_id in sorted(oracle_ids):
                try:
                    tokens = sc.tokens_from_oracle(oracle_id) or []
                except Exception:
                    tokens = []
                for token in tokens:
                    token_key = f"{(token.get('name') or '').strip().lower()}|{(token.get('type_line') or '').strip().lower()}"
                    if token_key in token_seen:
                        continue
                    token_seen.add(token_key)
                    token_payloads.append(_token_payload(token, placeholder))
            token_payloads.sort(key=lambda item: (item.get("name") or "").lower())
            custom_token_entries_json = json.dumps(token_payloads, ensure_ascii=True)
    else:
        flash("Select a deck or paste a deck list to continue.", "warning")
        return redirect(url_for("views.opening_hand"))

    deck_card_lookup_json, deck_token_lookup_json = _opening_hand_lookups(deck_refs)
    placeholder = static_url("img/card-placeholder.svg")
    selected_commander_cards_json = json.dumps(
        [_client_card_payload(card, placeholder) for card in commander_cards],
        ensure_ascii=True,
    )
    return render_template(
        "decks/opening_hand.html",
        deck_options=deck_options,
        deck_card_lookup_json=deck_card_lookup_json,
        deck_token_lookup_json=deck_token_lookup_json,
        selected_deck_id=deck_key,
        selected_deck_name=selected_deck_name,
        selected_deck_list=deck_list_text,
        selected_commander_name=commander_hint,
        selected_commander_cards_json=selected_commander_cards_json,
        custom_token_entries_json=custom_token_entries_json,
    )


def opening_hand_shuffle():
    payload = request.get_json(silent=True) or {}
    deck_id_raw = payload.get("deck_id")
    deck_list_text = (payload.get("deck_list") or "").strip()
    commander_hint = (payload.get("commander_name") or "").strip()

    deck_name = None
    entries: list[dict] = []
    warnings: list[str] = []
    commander_cards: list[dict] = []
    deck_source = None
    deck_id = None

    if deck_id_raw not in (None, "", False):
        try:
            parsed = _parse_opening_hand_deck_ref(deck_id_raw)
        except ValidationError as exc:
            log_validation_error(exc, context="opening_hand_shuffle")
            return jsonify({"ok": False, "error": "Invalid deck selection."}), 400
        if parsed:
            deck_source, deck_id = parsed

    if deck_id:
        if deck_source == "build":
            deck_name, entries, warnings, commander_cards = _deck_entries_from_build_session(deck_id)
        else:
            deck_name, entries, warnings, commander_cards = _deck_entries_from_folder(deck_id)
        if deck_name is None:
            return jsonify({"ok": False, "error": "Deck not found."}), 404
    elif deck_list_text:
        deck_name, entries, warnings, commander_cards = _deck_entries_from_list(deck_list_text, commander_hint)
    else:
        return jsonify({"ok": False, "error": "Select a deck or paste a deck list first."}), 400

    try:
        shuffled = gameplay_service.shuffle_deck(
            entries,
            hand_size=HAND_SIZE,
            expand_entries_fn=_expanded_deck_entries,
        )
    except ValueError as exc:
        return jsonify(
            {
                "ok": False,
                "error": str(exc),
                "warnings": warnings,
            }
        ), 400

    state_token = _encode_state(
        {
            "deck": shuffled["deck"],
            "index": shuffled["index"],
            "deck_name": deck_name,
            "user_id": current_user.id if current_user and getattr(current_user, "is_authenticated", False) else None,
        }
    )
    placeholder = static_url("img/card-placeholder.svg")
    return jsonify(
        {
            "ok": True,
            "hand": [_client_card_payload(card, placeholder) for card in shuffled["hand_cards"]],
            "state": state_token,
            "remaining": shuffled["remaining"],
            "deck_name": deck_name,
            "warnings": warnings,
            "deck_size": shuffled["deck_size"],
            "commanders": [_client_card_payload(card, placeholder) for card in commander_cards],
        }
    )


def opening_hand_mulligan():
    payload = request.get_json(silent=True) or {}
    state = _decode_state(payload.get("state") or "")
    if not state:
        return jsonify({"ok": False, "error": "Invalid or expired hand state."}), 400

    raw_bottom_uids = payload.get("bottom_uids") or []
    if not isinstance(raw_bottom_uids, list):
        return jsonify({"ok": False, "error": "Invalid mulligan selection."}), 400
    try:
        result = gameplay_service.mulligan_state(
            state,
            raw_bottom_uids=raw_bottom_uids,
            count=payload.get("count"),
            hand_size=HAND_SIZE,
        )
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    next_state = result["state"]
    placeholder = static_url("img/card-placeholder.svg")
    return jsonify(
        {
            "ok": True,
            "hand": [_client_card_payload(card, placeholder) for card in result["hand_cards"]],
            "state": _encode_state(next_state),
            "remaining": result["remaining"],
            "deck_name": next_state["deck_name"],
            "bottomed": result["bottomed"],
            "hand_size": result["hand_size"],
        }
    )


def opening_hand_draw():
    payload = request.get_json(silent=True) or {}
    token = payload.get("state") or ""
    state = _decode_state(token)
    if not state:
        return jsonify({"ok": False, "error": "Invalid or expired hand state."}), 400

    try:
        result = gameplay_service.draw_state(state)
    except ValueError as exc:
        return jsonify({"ok": False, "error": "No more cards to draw.", "remaining": 0, "deck_name": state["deck_name"], "state": token})

    next_state = result["state"]
    placeholder = static_url("img/card-placeholder.svg")
    return jsonify(
        {
            "ok": True,
            "card": _client_card_payload(result["card"], placeholder),
            "state": _encode_state(next_state),
            "remaining": result["remaining"],
            "deck_name": next_state["deck_name"],
        }
    )


def opening_hand_search():
    payload = request.get_json(silent=True) or {}
    state = _decode_state(payload.get("state") or "")
    if not state:
        return jsonify({"ok": False, "error": "Invalid or expired hand state."}), 400

    action = (payload.get("action") or "list").lower()
    criteria = payload.get("criteria") or {}
    placeholder = static_url("img/card-placeholder.svg")
    try:
        result = gameplay_service.search_state(
            state,
            action=action,
            criteria=criteria,
            pick_uid=payload.get("pick_uid"),
            pick_name=payload.get("pick_name"),
        )
    except LookupError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    if action == "list":
        return jsonify(
            {
                "ok": True,
                "matches": [
                    {
                        "name": item["name"],
                        "count": item["count"],
                        "card": _client_card_payload(item["card"], placeholder),
                    }
                    for item in result["matches"]
                ],
                "remaining": result["remaining"],
            }
        )

    next_state = result["state"]
    return jsonify(
        {
            "ok": True,
            "card": _client_card_payload(result["card"], placeholder),
            "state": _encode_state(next_state),
            "remaining": result["remaining"],
        }
    )


def opening_hand_peek():
    payload = request.get_json(silent=True) or {}
    state = _decode_state(payload.get("state") or "")
    if not state:
        return jsonify({"ok": False, "error": "Invalid or expired hand state."}), 400

    try:
        result = gameplay_service.peek_state(state, count=payload.get("count"))
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    placeholder = static_url("img/card-placeholder.svg")
    return jsonify(
        {
            "ok": True,
            "cards": [_client_card_payload(card, placeholder) for card in result["cards"]],
            "count": result["count"],
            "remaining": result["remaining"],
            "deck_name": state["deck_name"],
            "state": payload.get("state") or "",
        }
    )


def opening_hand_hideaway():
    payload = request.get_json(silent=True) or {}
    state = _decode_state(payload.get("state") or "")
    if not state:
        return jsonify({"ok": False, "error": "Invalid or expired hand state."}), 400

    try:
        result = gameplay_service.hideaway_state(
            state,
            count=payload.get("count"),
            pick_uid=payload.get("pick_uid"),
            pick_index=payload.get("pick_index"),
        )
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    next_state = result["state"]
    placeholder = static_url("img/card-placeholder.svg")
    return jsonify(
        {
            "ok": True,
            "state": _encode_state(next_state),
            "remaining": result["remaining"],
            "deck_name": next_state["deck_name"],
            "hidden": _client_card_payload(result["hidden"], placeholder),
            "bottom": result["bottom"],
        }
    )


def opening_hand_scry():
    return _opening_hand_reorder(action="scry")


def opening_hand_surveil():
    return _opening_hand_reorder(action="surveil")


def _opening_hand_reorder(*, action: str):
    payload = request.get_json(silent=True) or {}
    state = _decode_state(payload.get("state") or "")
    if not state:
        return jsonify({"ok": False, "error": "Invalid or expired hand state."}), 400

    try:
        result = gameplay_service.reorder_state(
            state,
            action=action,
            count=payload.get("count"),
            keep_order=payload.get("keep_order") or [],
            bottom_order=payload.get("bottom_order") or [],
            graveyard_order=payload.get("graveyard_order") or [],
            choices=payload.get("choices") or [],
        )
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    next_state = result["state"]
    placeholder = static_url("img/card-placeholder.svg")
    return jsonify(
        {
            "ok": True,
            "state": _encode_state(next_state),
            "remaining": result["remaining"],
            "deck_name": next_state["deck_name"],
            "moved": {
                "graveyard": [_client_card_payload(card, placeholder) for card in result["graveyard_cards"]],
                "bottom": len(result["bottom_cards"]),
            },
        }
    )


def opening_hand_tokens():
    deck_id_raw = (request.args.get("deck_id") or "").strip()
    if not deck_id_raw:
        return jsonify({"ok": True, "deck_id": "", "tokens": []})
    try:
        parsed = _parse_opening_hand_deck_ref(deck_id_raw)
    except ValidationError as exc:
        log_validation_error(exc, context="opening_hand_tokens")
        return jsonify({"ok": False, "error": "Invalid deck selection."}), 400
    if not parsed:
        return jsonify({"ok": False, "error": "Invalid deck selection."}), 400

    source, deck_id = parsed
    deck_key = _opening_hand_deck_key(source, deck_id)
    deck_lookup, _ = _opening_hand_deck_options()
    if deck_key not in deck_lookup:
        return jsonify({"ok": False, "error": "Deck not found."}), 404
    try:
        deck_token_lookup = json.loads(_opening_hand_lookups([deck_key])[1] or "{}")
    except Exception:
        deck_token_lookup = {}
    return jsonify(
        {
            "ok": True,
            "deck_id": deck_key,
            "tokens": _dedupe_opening_hand_tokens(deck_token_lookup.get(deck_key) or []),
        }
    )


def opening_hand_token_search():
    query = (request.args.get("q") or "").strip()
    if len(query) < 2:
        return jsonify({"ok": True, "tokens": []})
    if not _ensure_cache_ready():
        return jsonify({"ok": False, "error": "Token search is unavailable."}), 503

    def _token_search():
        try:
            return sc.search_tokens(query, limit=36) or []
        except Exception:
            return []

    tokens = _cache_fetch(f"token_search:{query.lower()}", 300, _token_search)
    placeholder = static_url("img/card-placeholder.svg")
    payloads = _dedupe_opening_hand_tokens([_token_payload(token, placeholder) for token in tokens])
    return jsonify({"ok": True, "tokens": payloads})


__all__ = [
    "opening_hand",
    "opening_hand_draw",
    "opening_hand_hideaway",
    "opening_hand_mulligan",
    "opening_hand_peek",
    "opening_hand_play",
    "opening_hand_scry",
    "opening_hand_search",
    "opening_hand_shuffle",
    "opening_hand_surveil",
    "opening_hand_token_search",
    "opening_hand_tokens",
]
