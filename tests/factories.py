"""Factory helpers for quickly seeding the test database."""
from __future__ import annotations

import itertools
from typing import Optional

from extensions import db
from models import Card, Folder

_folder_counter = itertools.count(1)
_card_counter = itertools.count(1)


def create_folder(
    *,
    name: Optional[str] = None,
    category: str = Folder.CATEGORY_DECK,
    is_proxy: bool = False,
) -> Folder:
    folder = Folder(
        name=name or f"Folder {_next_value(_folder_counter)}",
        category=category,
        is_proxy=is_proxy,
    )
    db.session.add(folder)
    db.session.flush()
    return folder


def create_card(
    *,
    folder: Optional[Folder] = None,
    name: Optional[str] = None,
    set_code: str = "neo",
    collector_number: str = "001",
    quantity: int = 1,
    lang: str = "en",
    is_foil: bool = False,
    oracle_id: Optional[str] = None,
) -> Card:
    home = folder or create_folder()
    card = Card(
        name=name or f"Card {_next_value(_card_counter)}",
        folder=home,
        folder_id=home.id,
        set_code=set_code,
        collector_number=collector_number,
        quantity=quantity,
        lang=lang,
        is_foil=is_foil,
        oracle_id=oracle_id,
    )
    db.session.add(card)
    db.session.flush()
    return card


def _next_value(counter: itertools.count) -> int:
    return next(counter)


__all__ = ["create_folder", "create_card"]
