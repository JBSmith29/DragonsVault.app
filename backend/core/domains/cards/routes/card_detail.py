"""Owned card detail views."""

from __future__ import annotations

from core.domains.cards.services import card_service
from core.routes.base import views


@views.route("/cards/<int:card_id>")
def card_detail(card_id):
    return card_service.card_detail(card_id)


@views.route("/cards/<id_or_sid>")
def smart_card_detail(id_or_sid):
    return card_service.smart_card_detail(id_or_sid)


__all__ = ["card_detail", "smart_card_detail"]
