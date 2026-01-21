"""Card view models for template rendering."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional


@dataclass(slots=True)
class ImageSetVM:
    small: Optional[str] = None
    normal: Optional[str] = None
    large: Optional[str] = None
    label: Optional[str] = None


@dataclass(slots=True)
class FolderRefVM:
    id: Optional[int]
    name: Optional[str]


@dataclass(slots=True)
class CardListItemVM:
    id: int
    name: str
    display_name: str
    quantity: int
    folder: Optional[FolderRefVM]
    set_code: Optional[str]
    collector_number: Optional[str]
    lang: Optional[str]
    is_foil: bool
    image_small: Optional[str] = None
    image_large: Optional[str] = None
    type_line: Optional[str] = None
    type_badges: list[str] = field(default_factory=list)
    type_tokens: list[str] = field(default_factory=list)
    core_roles_display: list[str] = field(default_factory=list)
    core_roles_overflow: int = 0
    evergreen_display: list[str] = field(default_factory=list)
    evergreen_overflow: int = 0
    color_letters: list[str] = field(default_factory=list)
    rarity_label: Optional[str] = None
    rarity_badge_class: Optional[str] = None
    price_text: Optional[str] = None
    owner_label: Optional[str] = None

    @property
    def type_tokens_str(self) -> str:
        return " ".join(self.type_tokens or [])

    @property
    def folder_id(self) -> Optional[int]:
        return self.folder.id if self.folder else None

    @property
    def folder_name(self) -> Optional[str]:
        return self.folder.name if self.folder else None


@dataclass(slots=True)
class CardInfoVM:
    """Presentation-ready card detail info for templates."""
    name: Optional[str]
    mana_cost_html: Optional[str]
    cmc: Optional[float]
    type_line: Optional[str]
    oracle_text_html: Optional[str]
    colors: list[str]
    color_identity: list[str]
    keywords: list[str]
    rarity: Optional[str]
    set_code: Optional[str]
    set_name: Optional[str]
    collector_number: Optional[str]
    scryfall_uri: Optional[str]
    scryfall_set_uri: Optional[str]
    commander_legality: Optional[str]
    commander_legality_label: Optional[str]
    commander_legality_class: Optional[str]
    has_commander_legality: bool
    price_text: Optional[str]
    tcgplayer_url: Optional[str]
    prints_search_uri: Optional[str]
    lang: Optional[str] = None
    oracle_id: Optional[str] = None
    prices_json: str = ""
    has_prices: bool = False
    has_oracle_text: bool = False
    has_mana_cost: bool = False
    has_scryfall_uri: bool = False
    has_scryfall_set_uri: bool = False


@dataclass(slots=True)
class CardTokenVM:
    id: Optional[str]
    name: Optional[str]
    type_line: Optional[str]
    images: Optional[ImageSetVM]


@dataclass(slots=True)
class ScryfallCardVM:
    id: str
    name: str
    thumb: Optional[str]
    image_large: Optional[str]
    prints_uri: Optional[str]
    set_code: str
    set_name: Optional[str]
    collector_number: Optional[str]
    lang: Optional[str]
    owned_total: int
    mana_cost_html: Optional[str]
    cmc_display: str
    type_badges: list[str] = field(default_factory=list)
    rarity_label: Optional[str] = None
    rarity_badge_class: Optional[str] = None
    price_lines: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SetGalleryCardVM:
    id: str
    name: str
    collector_number: str
    collector_number_display: str
    image_src: Optional[str]
    detail_href: str
    rarity_label: str
    owned_qty: int
    title: str


@dataclass(slots=True)
class TypeBreakdownVM:
    label: str
    count: int
    icon_class: Optional[str]
    icon_letter: Optional[str]
    url: str


def slice_badges(values: Iterable[str], limit: int = 2) -> tuple[list[str], int]:
    items = [val for val in values if val]
    display = items[:limit]
    overflow = max(len(items) - limit, 0)
    return display, overflow


def format_role_label(raw: str) -> str:
    return raw.replace("_", " ").replace("-", " ").title()
