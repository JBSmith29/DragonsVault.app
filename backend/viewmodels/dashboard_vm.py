"""Dashboard view models for template rendering."""

from __future__ import annotations

from dataclasses import dataclass, field

from .deck_vm import DeckVM


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
class DashboardViewModel:
    mode: str
    mode_label: str
    mode_description: str
    content_partial: str
    mode_options: list[DashboardModeOptionVM] = field(default_factory=list)
    builder_tiles: list[DashboardStatTileVM] = field(default_factory=list)
    collection_tiles: list[DashboardStatTileVM] = field(default_factory=list)
    deck_tiles: list[DashboardStatTileVM] = field(default_factory=list)
    builder_actions: list[DashboardActionVM] = field(default_factory=list)
    collection_actions: list[DashboardActionVM] = field(default_factory=list)
    deck_actions: list[DashboardActionVM] = field(default_factory=list)
    decks: list[DeckVM] = field(default_factory=list)
    build_decks: list[DeckVM] = field(default_factory=list)
