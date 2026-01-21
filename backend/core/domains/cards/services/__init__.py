"""Cards domain services."""

from __future__ import annotations

import importlib

__all__ = [
    "card_service",
    "csv_importer",
    "import_helpers",
    "import_service",
    "pricing",
    "scryfall_cache",
    "scryfall_cache_compat",
    "scryfall_search",
    "scryfall_service",
]


def __getattr__(name: str):
    if name in __all__:
        return importlib.import_module(f"{__name__}.{name}")
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")


def __dir__() -> list[str]:
    return sorted(__all__)
