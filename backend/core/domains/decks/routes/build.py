"""Build-A-Deck landing and session routes."""

from __future__ import annotations

from flask_login import login_required

from core.domains.decks.services import build_landing_service, build_session_service
from core.routes.base import views


@views.get("/decks/build")
@login_required
def build_landing():
    return build_landing_service.build_landing_page()


@views.post("/decks/build/start")
@login_required
def build_start():
    return build_session_service.start_build_session()


@views.get("/decks/build/<int:session_id>")
@login_required
def build_session(session_id: int):
    return build_session_service.build_session_page(session_id)


@views.post("/decks/build/<int:session_id>/cards/add")
@login_required
def build_session_add(session_id: int):
    return build_session_service.add_card(session_id)


@views.post("/decks/build/<int:session_id>/cards/add-bulk")
@login_required
def build_session_add_bulk(session_id: int):
    return build_session_service.add_cards_bulk(session_id)


@views.post("/decks/build/<int:session_id>/cards/manual-add")
@login_required
def build_session_add_manual(session_id: int):
    return build_session_service.add_cards_manual(session_id)


@views.post("/decks/build/<int:session_id>/cards/remove")
@login_required
def build_session_remove(session_id: int):
    return build_session_service.remove_card(session_id)


@views.post("/decks/build/<int:session_id>/cards/quantity")
@login_required
def build_session_quantity(session_id: int):
    return build_session_service.update_quantity(session_id)


@views.post("/decks/build/<int:session_id>/tags")
@login_required
def build_session_tags(session_id: int):
    return build_session_service.update_tags(session_id)


@views.post("/decks/build/<int:session_id>/name")
@login_required
def build_session_name(session_id: int):
    return build_session_service.update_name(session_id)


@views.post("/decks/build/<int:session_id>/delete")
@login_required
def build_session_delete(session_id: int):
    return build_session_service.delete_session(session_id)


@views.post("/decks/build/<int:session_id>/edhrec")
@login_required
def build_session_edhrec(session_id: int):
    return build_session_service.refresh_edhrec(session_id)


@views.get("/decks/build/<int:session_id>/edhrec/status")
@login_required
def build_session_edhrec_status(session_id: int):
    return build_session_service.edhrec_status(session_id)


@views.get("/api/build-sessions/<int:session_id>/insight")
@login_required
def api_build_session_insight(session_id: int):
    return build_session_service.api_build_session_insight(session_id)


__all__ = [
    "build_landing",
    "build_start",
    "build_session",
    "build_session_add",
    "build_session_add_bulk",
    "build_session_add_manual",
    "build_session_remove",
    "build_session_quantity",
    "build_session_tags",
    "build_session_name",
    "build_session_delete",
    "build_session_edhrec",
    "build_session_edhrec_status",
    "api_build_session_insight",
]
