"""Folder view models for template rendering."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from core.domains.cards.viewmodels.card_vm import ImageSetVM


@dataclass(slots=True)
class FolderVM:
    id: int
    name: str
    category: Optional[str]
    category_label: Optional[str]
    owner: Optional[str]
    owner_label: Optional[str]
    owner_user_id: Optional[int]
    is_collection: bool
    is_deck: bool
    is_proxy: bool
    is_public: bool
    deck_tag: Optional[str]
    deck_tag_label: Optional[str]
    commander_name: Optional[str]
    commander_oracle_id: Optional[str]
    commander_slot_count: int
    notes: Optional[str] = None
    role_labels: list[str] = field(default_factory=list)


@dataclass(slots=True)
class FolderOptionVM:
    id: int
    name: str


@dataclass(slots=True)
class FolderMoveOptionVM:
    """Presentation-ready move target for card bulk actions."""
    id: int
    name: str
    is_collection: bool
    is_proxy: bool


@dataclass(slots=True)
class FolderExportVM:
    """Folder export row data for import/export UI."""
    id: int
    name: str
    category: str
    category_label: str
    is_proxy: bool
    is_public: bool
    is_deck: bool
    is_collection: bool


@dataclass(slots=True)
class SharedFolderEntryVM:
    """Shared folder summary for the shared folders view."""
    folder: FolderVM
    owner_label: str


@dataclass(slots=True)
class CollectionBucketVM:
    label: str
    folder: Optional[FolderOptionVM]
    rows: int
    qty: int


@dataclass(slots=True)
class FolderCardVM:
    id: int
    name: str
    display_name: str
    set_code: Optional[str]
    collector_number: Optional[str]
    lang: Optional[str]
    is_foil: bool
    quantity: int
    type_line: str
    type_badges: list[str] = field(default_factory=list)
    color_icons: list[str] = field(default_factory=list)
    cmc_value: Optional[float] = None
    cmc_display: str = "—"
    cmc_bucket: str = ""
    rarity_label: Optional[str] = None
    rarity_badge_class: Optional[str] = None
    image_small: Optional[str] = None
    image_normal: Optional[str] = None
    image_large: Optional[str] = None
    hover_image: Optional[str] = None
    data_tags: str = ""
    data_roles: str = ""

    @property
    def images(self) -> ImageSetVM:
        return ImageSetVM(small=self.image_small, normal=self.image_normal, large=self.image_large)

    @property
    def set_code_upper(self) -> str:
        return (self.set_code or "").upper()

    @property
    def foil_label(self) -> str:
        return "✓" if self.is_foil else "—"
