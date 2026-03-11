"""Card browsing, collection summaries, and deck-centric routes."""

from __future__ import annotations

from flask_login import login_required

from extensions import limiter
from core.domains.cards.services import card_service
from core.domains.decks.services import deck_service
from core.routes.api import api_bp
from core.routes.base import limiter_key_user_or_ip, views


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


@api_bp.post("/decks/proxy/fetch")
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


@views.post("/cards/shared/follow")
@login_required
def shared_follow():
    return card_service.shared_follow()


@views.post("/cards/bulk-move")
@login_required
def bulk_move_cards():
    return card_service.bulk_move_cards()


@views.post("/folders/<int:folder_id>/cards/bulk-delete")
@login_required
def bulk_delete_cards(folder_id):
    return card_service.bulk_delete_cards(folder_id)


@api_bp.get("/card/<int:card_id>/printing-options")
@login_required
def api_card_printing_options(card_id):
    return card_service.api_card_printing_options(card_id)


@api_bp.post("/card/<int:card_id>/update-printing")
@login_required
def api_update_card_printing(card_id):
    return card_service.api_update_card_printing(card_id)


@views.route("/collection")
def collection_overview():
    return card_service.collection_overview()


@api_bp.get("/decks/<int:deck_id>/insight")
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
@login_required
def opening_hand():
    return deck_service.opening_hand()


@views.route("/opening-hand/play", methods=["GET", "POST"])
@login_required
def opening_hand_play():
    return deck_service.opening_hand_play()


@views.post("/opening-hand/shuffle")
@login_required
def opening_hand_shuffle():
    return deck_service.opening_hand_shuffle()


@views.post("/opening-hand/mulligan")
@login_required
def opening_hand_mulligan():
    return deck_service.opening_hand_mulligan()


@views.post("/opening-hand/draw")
@login_required
def opening_hand_draw():
    return deck_service.opening_hand_draw()


@views.post("/opening-hand/search")
@login_required
def opening_hand_search():
    return deck_service.opening_hand_search()


@views.post("/opening-hand/peek")
@login_required
def opening_hand_peek():
    return deck_service.opening_hand_peek()


@views.post("/opening-hand/hideaway")
@login_required
def opening_hand_hideaway():
    return deck_service.opening_hand_hideaway()


@views.post("/opening-hand/scry")
@login_required
def opening_hand_scry():
    return deck_service.opening_hand_scry()


@views.post("/opening-hand/surveil")
@login_required
def opening_hand_surveil():
    return deck_service.opening_hand_surveil()


@views.get("/opening-hand/tokens")
@login_required
def opening_hand_tokens():
    return deck_service.opening_hand_tokens()


@views.get("/opening-hand/tokens/search")
@login_required
def opening_hand_token_search():
    return deck_service.opening_hand_token_search()


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
    "opening_hand_play",
    "opening_hand_shuffle",
    "opening_hand_mulligan",
    "opening_hand_draw",
    "opening_hand_search",
    "opening_hand_peek",
    "opening_hand_hideaway",
    "opening_hand_scry",
    "opening_hand_surveil",
    "opening_hand_tokens",
    "opening_hand_token_search",
    "decks_overview",
    "list_cards",
    "shared_folders",
    "shared_follow",
]
