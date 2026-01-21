"""Set view models for template rendering."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(slots=True)
class SetSummaryVM:
    """Presentation-ready set summary row."""
    set_code: str
    set_name: str
    rows: int
    qty: int
    release_iso: Optional[str]
    release_display: Optional[str]
