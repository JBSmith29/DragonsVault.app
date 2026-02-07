"""Dashboard view models for template rendering."""

from __future__ import annotations

from dataclasses import dataclass, field

from core.domains.decks.viewmodels.deck_vm import DeckVM


@dataclass(slots=True)
class DashboardModeOptionVM:
    value: str
    label: str
    selected: bool = False


@dataclass(slots=True)
class DashboardActionVM:
    label: str
    href: str
    icon: str
    external: bool = False


@dataclass(slots=True)
class DashboardStatTileVM:
    label: str
    value: str
    href: str
    icon: str


@dataclass(slots=True)
class DashboardCollectionStatsVM:
    total_qty: int
    collection_qty: int
    unique_names: int
    set_count: int
    collection_bucket_count: int


@dataclass(slots=True)
class DashboardTopCardVM:
    id: int
    name: str
    image: str | None
    price_text: str | None
    folder_name: str
    card_href: str
    printing_label: str | None = None


@dataclass(slots=True)
class DashboardViewModel:
    mode: str
    mode_label: str
    mode_description: str
    content_partial: str
    mode_options: list[DashboardModeOptionVM] = field(default_factory=list)
    collection_tiles: list[DashboardStatTileVM] = field(default_factory=list)
    deck_tiles: list[DashboardStatTileVM] = field(default_factory=list)
    collection_actions: list[DashboardActionVM] = field(default_factory=list)
    deck_actions: list[DashboardActionVM] = field(default_factory=list)
    decks: list[DeckVM] = field(default_factory=list)
    collection_stats: DashboardCollectionStatsVM | None = None
    collection_top_cards: list[DashboardTopCardVM] = field(default_factory=list)
