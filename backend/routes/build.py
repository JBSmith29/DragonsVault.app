"""Build-A-Deck landing and session routes."""

from __future__ import annotations

from flask_login import login_required

from services import build_landing_service, build_session_service
from .base import views


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


@views.post("/decks/build/<int:session_id>/cards/remove")
@login_required
def build_session_remove(session_id: int):
    return build_session_service.remove_card(session_id)


__all__ = [
    "build_landing",
    "build_start",
    "build_session",
    "build_session_add",
    "build_session_remove",
]
