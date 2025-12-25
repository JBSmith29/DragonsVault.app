"""Deck view models for template rendering."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .card_vm import ImageSetVM


@dataclass(slots=True)
class DeckCommanderVM:
    name: Optional[str]
    small: Optional[str]
    large: Optional[str]
    alt: Optional[str]
    images: list[ImageSetVM] = field(default_factory=list)


@dataclass(slots=True)
class DeckVM:
    id: int
    name: str
    qty: int
    owner: Optional[str]
    owner_key: str
    is_proxy: bool
    tag: Optional[str]
    tag_label: Optional[str]
    ci_name: str
    ci_html: str
    ci_letters: str
    commander: Optional[DeckCommanderVM]
    bracket_level: Optional[str]
    bracket_label: Optional[str]
    is_build: bool = False


@dataclass(slots=True)
class DeckOwnerSummaryVM:
    owner: Optional[str]
    label: str
    deck_count: int
    card_total: int
    proxy_count: int


@dataclass(slots=True)
class DeckTokenSourceVM:
    card_id: int
    name: str
    qty: int
    img: Optional[str]


@dataclass(slots=True)
class DeckTokenDeckVM:
    deck_id: int
    deck_name: str
    card_count: int
    sources: list[DeckTokenSourceVM] = field(default_factory=list)


@dataclass(slots=True)
class DeckTokenVM:
    name: str
    type_line: str
    image: Optional[str]
    hover_image: Optional[str]
    deck_count: int
    total_sources: int
    total_qty: int
    decks: list[DeckTokenDeckVM] = field(default_factory=list)
    search_key: str = ""
    deck_ids_csv: str = ""


@dataclass(slots=True)
class DeckTokenDeckSummaryVM:
    id: int
    name: str
    token_count: int
    is_proxy: bool
