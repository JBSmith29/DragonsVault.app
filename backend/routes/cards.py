"""Card browsing, collection summaries, and deck-centric routes."""

from __future__ import annotations

from flask_login import login_required

from extensions import limiter
from services import card_service, deck_service
from .base import limiter_key_user_or_ip, views


@views.post("/decks/proxy")
@limiter.limit("6 per minute", key_func=limiter_key_user_or_ip) if limiter else (lambda f: f)
@limiter.limit("30 per hour", key_func=limiter_key_user_or_ip) if limiter else (lambda f: f)
def create_proxy_deck():
    return deck_service.create_proxy_deck()


@views.post("/decks/proxy/bulk")
@limiter.limit("3 per minute", key_func=limiter_key_user_or_ip) if limiter else (lambda f: f)
@limiter.limit("15 per hour", key_func=limiter_key_user_or_ip) if limiter else (lambda f: f)
def create_proxy_deck_bulk():
    return deck_service.create_proxy_deck_bulk()


@views.post("/decks/build")
@login_required
def start_build_deck():
    return deck_service.start_build_deck()


@views.get("/decks/build")
def build_deck_landing():
    return deck_service.build_deck_landing()


@views.post("/api/decks/proxy/fetch")
@limiter.limit("10 per minute", key_func=limiter_key_user_or_ip) if limiter else (lambda f: f)
@limiter.limit("50 per hour", key_func=limiter_key_user_or_ip) if limiter else (lambda f: f)
def api_fetch_proxy_deck():
    return deck_service.api_fetch_proxy_deck()


@views.route("/cards")
def list_cards():
    return card_service.list_cards()


@views.route("/cards/shared")
@login_required
def shared_folders():
    return card_service.shared_folders()


@views.post("/cards/bulk-move")
@login_required
def bulk_move_cards():
    return card_service.bulk_move_cards()


@views.post("/folders/<int:folder_id>/cards/bulk-delete")
@login_required
def bulk_delete_cards(folder_id):
    return card_service.bulk_delete_cards(folder_id)


@views.get("/api/card/<int:card_id>/printing-options")
@login_required
def api_card_printing_options(card_id):
    return card_service.api_card_printing_options(card_id)


@views.post("/api/card/<int:card_id>/update-printing")
@login_required
def api_update_card_printing(card_id):
    return card_service.api_update_card_printing(card_id)


@views.route("/collection")
def collection_overview():
    return card_service.collection_overview()


@views.get('/api/decks/<int:deck_id>/insight')
def api_deck_insight(deck_id: int):
    return deck_service.api_deck_insight(deck_id)


@views.route("/decks")
def decks_overview():
    return deck_service.decks_overview()


@views.route("/decks/from-collection", methods=["GET", "POST"])
@login_required
def deck_from_collection():
    return deck_service.deck_from_collection()


@views.route("/decks/tokens")
def deck_tokens_overview():
    return deck_service.deck_tokens_overview()


@views.route("/opening-hand", methods=["GET"])
def opening_hand():
    return deck_service.opening_hand()


@views.post("/opening-hand/shuffle")
def opening_hand_shuffle():
    return deck_service.opening_hand_shuffle()


@views.post("/opening-hand/draw")
def opening_hand_draw():
    return deck_service.opening_hand_draw()


__all__ = [
    "collection_overview",
    "create_proxy_deck",
    "create_proxy_deck_bulk",
    "bulk_move_cards",
    "bulk_delete_cards",
    "api_card_printing_options",
    "api_update_card_printing",
    "api_fetch_proxy_deck",
    "deck_tokens_overview",
    "opening_hand",
    "opening_hand_shuffle",
    "opening_hand_draw",
    "decks_overview",
    "list_cards",
    "start_build_deck",
    "build_deck_landing",
]
