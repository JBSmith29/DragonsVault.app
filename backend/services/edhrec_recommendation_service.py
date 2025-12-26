"""Read-only EDHREC recommendation access using the local cache."""

from __future__ import annotations

from services.edhrec_cache_service import get_commander_synergy

__all__ = ["get_commander_synergy"]
