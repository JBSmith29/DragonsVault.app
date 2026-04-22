"""Wishlist route bindings."""

from __future__ import annotations

from core.domains.decks.services import wishlist_service
from core.routes.base import views


@views.route("/wishlist", methods=["GET"])
def wishlist():
    return wishlist_service.wishlist()


@views.route("/wishlist/add-form", methods=["POST"])
def wishlist_add_form():
    return wishlist_service.wishlist_add_form()


@views.route("/wishlist/request-friend", methods=["POST"])
def wishlist_request_friend():
    return wishlist_service.wishlist_request_friend()


@views.route("/friend-card-requests", methods=["POST"])
def friend_card_request_action():
    return wishlist_service.friend_card_request_action()


@views.route("/wishlist/add", methods=["POST"])
def wishlist_add():
    return wishlist_service.wishlist_add()


@views.route("/wishlist/mark/<int:item_id>", methods=["POST"])
def wishlist_mark(item_id: int):
    return wishlist_service.wishlist_mark(item_id)


@views.route("/wishlist/update/<int:item_id>", methods=["POST"])
def wishlist_update(item_id: int):
    return wishlist_service.wishlist_update(item_id)


@views.route("/wishlist/order/<int:item_id>", methods=["POST"])
def wishlist_order_ref(item_id: int):
    return wishlist_service.wishlist_order_ref(item_id)


@views.route("/wishlist/export", methods=["GET"])
def wishlist_export():
    return wishlist_service.wishlist_export()


@views.route("/wishlist/delete/<int:item_id>", methods=["POST"])
def wishlist_delete(item_id):
    return wishlist_service.wishlist_delete(item_id)


__all__ = [
    "friend_card_request_action",
    "wishlist",
    "wishlist_add",
    "wishlist_add_form",
    "wishlist_delete",
    "wishlist_export",
    "wishlist_mark",
    "wishlist_order_ref",
    "wishlist_request_friend",
    "wishlist_update",
]
