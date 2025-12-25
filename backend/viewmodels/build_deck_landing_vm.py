"""View models for the Build-A-Deck landing page."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class BuildLandingCommanderVM:
    commander_name: str
    commander_oracle_id: str
    owned_count: int
    total_considered: int
    coverage_pct: int
    owned_label: str
    coverage_label: str
    reason: str
    tag_label: str | None = None


@dataclass(slots=True)
class BuildLandingViewModel:
    collection_count: int
    edhrec_ready: bool
    selected_tag: str | None = None
    collection_fits: list[BuildLandingCommanderVM] = field(default_factory=list)
    tag_fits: list[BuildLandingCommanderVM] = field(default_factory=list)
    tag_candidates: int = 0
