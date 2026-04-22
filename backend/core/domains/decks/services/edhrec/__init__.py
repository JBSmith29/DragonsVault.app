"""EDHREC ingestion and helper services.

Sub-modules are imported lazily to avoid circular import issues at package
initialization time. Import them directly when needed:

    from core.domains.decks.services.edhrec import edhrec_ingestion_service
"""

from __future__ import annotations

__all__ = [
    "edhrec_ingestion_fetch_service",
    "edhrec_ingestion_persistence_service",
    "edhrec_ingestion_service",
    "edhrec_tag_refresh_service",
    "edhrec_payload_service",
    "edhrec_target_service",
]
