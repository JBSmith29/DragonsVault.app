"""Cards domain services."""

from __future__ import annotations

import importlib

__all__ = [
    "card_detail_service",
    "card_mutation_service",
    "card_export_service",
    "collection_card_list_view_service",
    "collection_facets_service",
    "collection_overview_service",
    "collection_query_service",
    "collection_request_service",
    "collection_service",
    "csv_import_flow_service",
    "csv_import_file_service",
    "csv_importer",
    "import_category_service",
    "import_helpers",
    "import_shared_service",
    "import_service",
    "list_checker_export_service",
    "list_checker_inventory_service",
    "list_checker_parsing_service",
    "list_checker_result_builder_service",
    "list_checker_result_service",
    "list_checker_scryfall_service",
    "list_checker_service",
    "manual_import_service",
    "pricing",
    "shared_folders_service",
    "scryfall_cache",
    "scryfall_cache_compat",
    "scryfall_browser_service",
    "scryfall_bulk_service",
    "scryfall_http_service",
    "scryfall_index_service",
    "scryfall_cache_state_service",
    "scryfall_runtime_service",
    "scryfall_metadata_service",
    "scryfall_print_service",
    "scryfall_print_summary_service",
    "scryfall_rulings_service",
    "scryfall_set_metadata_service",
    "scryfall_search",
    "scryfall_set_profile_service",
    "scryfall_sets_service",
    "scryfall_shared_service",
    "scryfall_service",
]


def __getattr__(name: str):
    if name in __all__:
        return importlib.import_module(f"{__name__}.{name}")
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")


def __dir__() -> list[str]:
    return sorted(__all__)
