"""State/bootstrap helpers for the in-memory local Scryfall cache."""

from __future__ import annotations

import json
import os
from typing import Any, Callable


def clear_in_memory_prints(
    state: dict[str, Any],
    *,
    clear_cached_set_profiles_fn: Callable[[], None],
    bump_cache_epoch_fn: Callable[[], None],
    cache_clearers: list[Callable[[], None]] | None = None,
) -> None:
    state["_cache"] = []
    state["_by_set_cn"] = {}
    state["_by_oracle"] = {}
    state["_set_names"] = None
    state["_set_releases"] = None
    state["_idx_by_set_num"] = {}
    state["_idx_by_name"] = {}
    state["_idx_by_front"] = {}
    state["_idx_by_back"] = {}
    clear_cached_set_profiles_fn()
    bump_cache_epoch_fn()
    for clear_fn in cache_clearers or []:
        try:
            clear_fn()
        except Exception:
            pass


def prime_default_indexes(
    state: dict[str, Any],
    *,
    prime_default_indexes_fn: Callable[..., None],
    key_set_cn_fn: Callable[[str, str], str],
    clear_cached_set_profiles_fn: Callable[[], None],
    bump_cache_epoch_fn: Callable[[], None],
    cache_clearers: list[Callable[[], None]] | None = None,
) -> None:
    state["_by_set_cn"] = {}
    state["_by_oracle"] = {}
    state["_set_names"] = None
    state["_set_releases"] = None
    state["_idx_by_set_num"] = {}
    state["_idx_by_name"] = {}
    state["_idx_by_front"] = {}
    state["_idx_by_back"] = {}
    clear_cached_set_profiles_fn()
    prime_default_indexes_fn(
        state["_cache"],
        by_set_cn=state["_by_set_cn"],
        by_oracle=state["_by_oracle"],
        idx_by_set_num=state["_idx_by_set_num"],
        idx_by_name=state["_idx_by_name"],
        idx_by_front=state["_idx_by_front"],
        idx_by_back=state["_idx_by_back"],
        key_set_cn_fn=key_set_cn_fn,
    )
    bump_cache_epoch_fn()
    for clear_fn in cache_clearers or []:
        try:
            clear_fn()
        except Exception:
            pass


def load_default_cache(
    state: dict[str, Any],
    *,
    path: str | None,
    default_cards_path_fn: Callable[[str | None], str],
    prime_default_indexes_fn: Callable[[], None],
    clear_cached_catalog_fn: Callable[[], None],
) -> bool:
    resolved_path = default_cards_path_fn(path)
    if not os.path.exists(resolved_path):
        return False
    with open(resolved_path, "r", encoding="utf-8") as handle:
        state["_cache"] = json.load(handle) or []
    prime_default_indexes_fn()
    clear_cached_catalog_fn()
    return True


def reload_default_cache(
    *,
    path: str | None,
    clear_in_memory_prints_fn: Callable[[], None],
    clear_cached_catalog_fn: Callable[[], None],
    load_default_cache_fn: Callable[[str | None], bool],
) -> bool:
    clear_in_memory_prints_fn()
    clear_cached_catalog_fn()
    return load_default_cache_fn(path)


def clear_cache_files(
    state: dict[str, Any],
    *,
    include_default_cards: bool,
    default_cards_path_fn: Callable[[], str],
    rulings_bulk_path_fn: Callable[[], str],
    clear_in_memory_prints_fn: Callable[[], None],
    clear_cached_catalog_fn: Callable[[], None],
) -> int:
    removed = 0
    targets: list[str] = []
    if include_default_cards:
        targets.append(default_cards_path_fn())
    targets.append(rulings_bulk_path_fn())

    for target in targets:
        try:
            if os.path.exists(target):
                os.remove(target)
                removed += 1
        except Exception:
            pass

    try:
        clear_in_memory_prints_fn()
    except Exception:
        pass

    state["_rulings_by_oracle"] = {}
    state["_rulings_loaded_path"] = None
    clear_cached_catalog_fn()
    return removed


def load_and_index_with_progress(
    state: dict[str, Any],
    *,
    path: str | None,
    default_cards_path_fn: Callable[[str | None], str],
    step: int,
    progress_cb,
    key_set_cn_fn: Callable[[str, str], str],
    cn_num_fn: Callable[[str], int | None],
    name_key_fn: Callable[[str], str],
    front_face_name_fn: Callable[[dict[str, Any]], str],
    back_face_names_fn: Callable[[dict[str, Any]], list[str]],
    clear_cached_set_profiles_fn: Callable[[], None],
    clear_cached_catalog_fn: Callable[[], None],
) -> bool:
    resolved_path = default_cards_path_fn(path)
    if not os.path.exists(resolved_path):
        return False
    with open(resolved_path, "r", encoding="utf-8") as handle:
        data = json.load(handle) or []

    state["_cache"] = []
    state["_by_set_cn"] = {}
    state["_by_oracle"] = {}
    state["_set_names"] = None
    state["_set_releases"] = None
    state["_idx_by_set_num"] = {}
    state["_idx_by_name"] = {}
    state["_idx_by_front"] = {}
    state["_idx_by_back"] = {}
    clear_cached_set_profiles_fn()

    total = len(data)
    done = 0
    for card in data:
        state["_cache"].append(card)
        set_code = (card.get("set") or "").lower()
        collector_number = str(card.get("collector_number") or "")
        if set_code and collector_number:
            state["_by_set_cn"][key_set_cn_fn(set_code, collector_number)] = card
            number = cn_num_fn(collector_number)
            if number is not None:
                state["_idx_by_set_num"].setdefault((set_code, number), []).append(card)

        oracle_id = card.get("oracle_id")
        if oracle_id:
            state["_by_oracle"].setdefault(oracle_id, []).append(card)

        full_name_key = name_key_fn(card.get("name", ""))
        if full_name_key:
            state["_idx_by_name"].setdefault(full_name_key, []).append(card)

        front_name_key = name_key_fn(front_face_name_fn(card))
        if front_name_key:
            state["_idx_by_front"].setdefault(front_name_key, []).append(card)

        for back_name in back_face_names_fn(card):
            back_name_key = name_key_fn(back_name)
            if back_name_key:
                state["_idx_by_back"].setdefault(back_name_key, []).append(card)

        done += 1
        if progress_cb and (done % max(1, int(step)) == 0 or done == total):
            try:
                progress_cb(done, total)
            except Exception:
                pass

    clear_cached_catalog_fn()
    return True


__all__ = [
    "clear_cache_files",
    "clear_in_memory_prints",
    "load_and_index_with_progress",
    "load_default_cache",
    "prime_default_indexes",
    "reload_default_cache",
]
