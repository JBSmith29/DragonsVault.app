"""Compatibility wrapper for card import, export, and manual-import flows."""

from __future__ import annotations

from core.domains.cards.services.card_export_service import export_cards
from core.domains.cards.services.csv_import_flow_service import (
    handle_import_csv,
    import_status,
    import_template_csv,
)
from core.domains.cards.services.import_category_service import api_update_folder_categories
from core.domains.cards.services.import_shared_service import (
    normalize_collector_number,
    normalize_set_code,
)
from core.domains.cards.services.manual_import_service import manual_import

__all__ = [
    "api_update_folder_categories",
    "export_cards",
    "handle_import_csv",
    "import_status",
    "import_template_csv",
    "manual_import",
    "normalize_collector_number",
    "normalize_set_code",
]
