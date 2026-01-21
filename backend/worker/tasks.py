"""Compatibility wrappers for background recomputation tasks."""

from __future__ import annotations

from shared.jobs.background.oracle_recompute import (
    recompute_all_roles,
    recompute_oracle_roles,
    recompute_deck_tag_synergies,
    recompute_oracle_deck_tags,
    recompute_oracle_enrichment,
)

__all__ = [
    "recompute_all_roles",
    "recompute_oracle_roles",
    "recompute_deck_tag_synergies",
    "recompute_oracle_deck_tags",
    "recompute_oracle_enrichment",
]
