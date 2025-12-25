"""Opening hand view models for deck simulator payloads."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(slots=True)
class OpeningHandCardVM:
    """Presentation-ready card payload for the opening hand simulator."""
    value: str
    name: str
    image: str
    hover: str
    type_line: str
    mana_value: Optional[float]
    is_creature: bool
    is_land: bool
    is_instant: bool
    is_sorcery: bool
    is_permanent: bool
    zone_hint: str

    def to_payload(self) -> dict:
        return {
            "value": self.value,
            "name": self.name,
            "image": self.image,
            "hover": self.hover,
            "type_line": self.type_line,
            "mana_value": self.mana_value,
            "is_creature": self.is_creature,
            "is_land": self.is_land,
            "is_instant": self.is_instant,
            "is_sorcery": self.is_sorcery,
            "is_permanent": self.is_permanent,
            "zone_hint": self.zone_hint,
        }


@dataclass(slots=True)
class OpeningHandTokenVM:
    """Presentation-ready token payload for the opening hand simulator."""
    id: Optional[str]
    name: str
    type_line: str
    image: str
    hover: str
    is_creature: bool
    is_land: bool
    is_instant: bool
    is_sorcery: bool
    is_permanent: bool
    zone_hint: str

    def to_payload(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "type_line": self.type_line,
            "image": self.image,
            "hover": self.hover,
            "is_creature": self.is_creature,
            "is_land": self.is_land,
            "is_instant": self.is_instant,
            "is_sorcery": self.is_sorcery,
            "is_permanent": self.is_permanent,
            "zone_hint": self.zone_hint,
        }
