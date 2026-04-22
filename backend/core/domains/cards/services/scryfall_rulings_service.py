"""Helpers for local Scryfall rulings bulk files."""

from __future__ import annotations

import json
import os
import time
from typing import Any


def rulings_bulk_exists(
    path: str | None = None,
    *,
    rulings_bulk_path_fn,
) -> bool:
    return os.path.exists(rulings_bulk_path_fn(path))


def rulings_is_stale(
    path: str | None = None,
    *,
    rulings_bulk_path_fn,
    max_age: int,
) -> bool:
    rulings_path = rulings_bulk_path_fn(path)
    if not os.path.exists(rulings_path):
        return True
    return (time.time() - os.path.getmtime(rulings_path)) > max_age


def load_rulings_bulk(
    path: str | None = None,
    *,
    rulings_bulk_path_fn,
) -> tuple[dict[str, list[dict[str, Any]]], str | None]:
    rulings_path = rulings_bulk_path_fn(path)
    if not os.path.exists(rulings_path):
        return {}, None

    with open(rulings_path, "r", encoding="utf-8") as handle:
        data = json.load(handle) or []

    indexed: dict[str, list[dict[str, Any]]] = {}
    for row in data:
        oracle_id_raw = row.get("oracle_id")
        if not oracle_id_raw:
            continue
        oracle_id = str(oracle_id_raw).lower()
        indexed.setdefault(oracle_id, []).append(
            {
                "published_at": row.get("published_at"),
                "source": row.get("source"),
                "comment": row.get("comment"),
            }
        )
    for rulings in indexed.values():
        rulings.sort(key=lambda entry: (entry.get("published_at") or ""))

    return indexed, rulings_path


def rulings_for_oracle(
    oracle_id: str,
    *,
    rulings_by_oracle: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    if not oracle_id:
        return []
    return rulings_by_oracle.get(str(oracle_id).lower(), []) or []
