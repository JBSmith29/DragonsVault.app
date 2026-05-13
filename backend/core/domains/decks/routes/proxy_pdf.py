"""Proxy-sheet PDF download route."""

from __future__ import annotations

from io import BytesIO

from flask import send_file
from flask_login import login_required
from sqlalchemy.orm import selectinload

from extensions import db
from models import Folder
from core.domains.decks.services.proxy_pdf_service import (
    ProxySlot,
    render_proxy_pdf,
)
from core.routes.base import views
from shared.auth import ensure_folder_access
from shared.database import get_or_404


__all__ = ["folder_proxy_pdf"]


def _slugify(name: str) -> str:
    return (
        "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in (name or "deck"))
        .strip("-")
        .lower()
        or "deck"
    )


@views.get("/folders/<int:folder_id>/proxy.pdf")
@login_required
def folder_proxy_pdf(folder_id: int):
    folder = get_or_404(Folder, folder_id)
    ensure_folder_access(folder, write=False, allow_shared=True)

    folder = (
        db.session.query(Folder)
        .options(selectinload(Folder.cards))
        .filter(Folder.id == folder_id)
        .one()
    )
    deck: list[tuple[ProxySlot, int]] = []
    for card in folder.cards:
        qty = max(0, int(card.quantity or 0))
        if qty <= 0:
            continue
        deck.append(
            (
                ProxySlot(
                    name=card.name or "Unknown",
                    set_code=card.set_code,
                    collector_number=card.collector_number,
                    type_line=card.type_line,
                ),
                qty,
            )
        )

    pdf = render_proxy_pdf(deck, title=folder.name)
    filename = f"{_slugify(folder.name)}-proxies.pdf"
    response = send_file(
        BytesIO(pdf),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename,
    )
    return response
