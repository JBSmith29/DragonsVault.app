from __future__ import annotations

import json
import os
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Iterable, Iterator, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

import requests
from requests import Session
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

__all__ = [
    "INSTANT_WIN_RESULTS",
    "RESULT_QUERY_GROUPS",
    "EARLY_MANA_VALUE_THRESHOLD",
    "LATE_MANA_VALUE_THRESHOLD",
    "collect_instant_win_variants",
    "collect_relevant_spellbook_variants",
    "generate_spellbook_combo_dataset",
    "write_dataset_to_file",
    "DEFAULT_SPELLBOOK_CONCURRENCY",
    "DEFAULT_SPELLBOOK_PAGE_CONCURRENCY",
]

API_BASE_URL = "https://backend.commanderspellbook.com/variants/"
PAGE_SIZE = 100
DEFAULT_QUERY_SUFFIX = " legal:commander"
SPELLBOOK_TIMEOUT = int(os.getenv("COMMANDER_SPELLBOOK_TIMEOUT", "120"))
try:
    DEFAULT_SPELLBOOK_CONCURRENCY = int(os.getenv("COMMANDER_SPELLBOOK_CONCURRENCY", "6") or "6")
except ValueError:
    DEFAULT_SPELLBOOK_CONCURRENCY = 6
try:
    DEFAULT_SPELLBOOK_PAGE_CONCURRENCY = int(os.getenv("COMMANDER_SPELLBOOK_PAGE_CONCURRENCY", "2") or "2")
except ValueError:
    DEFAULT_SPELLBOOK_PAGE_CONCURRENCY = 2
_RETRY_STATUS = (408, 429, 500, 502, 503, 504)
_thread_local = threading.local()

ProgressCallback = Callable[[int, Optional[int]], None]

INSTANT_WIN_RESULTS: List[str] = [
    "Win the game",
    "Win the game at the beginning of your next upkeep",
    "Each opponent loses the game",
]

RESULT_QUERY_GROUPS: Dict[str, List[str]] = {
    "instant_win": [f'result="{name}"' for name in INSTANT_WIN_RESULTS],
    "infinite_mana": ["result:infinite result:mana"],
    "infinite_life": ["result:infinite result:life"],
    "infinite_damage": ["result:infinite result:damage"],
    "infinite_draw": ["result:infinite result:draw", 'result:"Infinite card draw"'],
    "infinite_tokens": ["result:infinite result:token"],
    "infinite_turns": ["result:infinite result:turn"],
}

EARLY_MANA_VALUE_THRESHOLD = 6
LATE_MANA_VALUE_THRESHOLD = 10


@dataclass(frozen=True)
class SpellbookVariantRecord:
    variant: Dict[str, any]
    results: set[str]
    categories: set[str]

def _get_spellbook_session() -> Session:
    session = getattr(_thread_local, "session", None)
    if session is None:
        retry = Retry(
            total=4,
            backoff_factor=1.5,
            status_forcelist=_RETRY_STATUS,
            allowed_methods=frozenset({"GET"}),
        )
        adapter = HTTPAdapter(max_retries=retry)
        session = requests.Session()
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        _thread_local.session = session
    return session


def _iter_variants(
    params: Optional[Dict[str, str]] = None,
    progress_callback: Optional[ProgressCallback] = None,
    total_callback: Optional[Callable[[int], None]] = None,
) -> Iterator[Dict[str, any]]:
    next_url: Optional[str] = API_BASE_URL
    query = dict(params) if params else {"limit": PAGE_SIZE}
    fetched = 0
    total_count: Optional[int] = None

    def _report_total(payload: Dict[str, any]) -> None:
        nonlocal total_count
        if total_count is not None:
            return
        total_raw = payload.get("count")
        if isinstance(total_raw, int):
            total_count = total_raw
            if total_callback:
                try:
                    total_callback(total_count)
                except Exception:
                    # Total reporting is best-effort.
                    pass

    def _yield_results(results: Iterable[Dict[str, any]]) -> Iterator[Dict[str, any]]:
        nonlocal fetched
        for item in results:
            fetched += 1
            if progress_callback:
                try:
                    progress_callback(fetched, total_count)
                except Exception:
                    # Progress reporting is best-effort; ignore callback errors.
                    pass
            yield item

    session = _get_spellbook_session()
    response = session.get(
        next_url,
        params=query if next_url == API_BASE_URL else None,
        timeout=SPELLBOOK_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    _report_total(payload)
    yield from _yield_results(payload.get("results", []))

    limit = int(query.get("limit") or PAGE_SIZE)
    page_workers = max(1, int(DEFAULT_SPELLBOOK_PAGE_CONCURRENCY or 1))
    offset_start = int(query.get("offset") or 0)

    if (
        page_workers <= 1
        or not total_count
        or total_count <= offset_start + limit
    ):
        next_url = payload.get("next")
        query = None
        while next_url:
            response = session.get(next_url, timeout=SPELLBOOK_TIMEOUT)
            response.raise_for_status()
            payload = response.json()
            yield from _yield_results(payload.get("results", []))
            next_url = payload.get("next")
        return

    offsets = list(range(offset_start + limit, total_count, limit))
    if not offsets:
        return

    q_value = query.get("q")

    def _fetch_offset(offset: int) -> List[Dict[str, any]]:
        params: Dict[str, str] = {"limit": str(limit), "offset": str(offset)}
        if q_value:
            params["q"] = q_value
        sess = _get_spellbook_session()
        resp = sess.get(API_BASE_URL, params=params, timeout=SPELLBOOK_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        return data.get("results", []) or []

    with ThreadPoolExecutor(max_workers=page_workers) as executor:
        futures = [executor.submit(_fetch_offset, offset) for offset in offsets]
        for future in as_completed(futures):
            results = future.result()
            yield from _yield_results(results)


def collect_instant_win_variants(
    progress_callback: Optional[ProgressCallback] = None,
    *,
    concurrency: Optional[int] = None,
) -> Dict[str, SpellbookVariantRecord]:
    return _collect_variants_by_queries(
        [("instant_win", query) for query in RESULT_QUERY_GROUPS["instant_win"]],
        progress_callback=progress_callback,
        concurrency=concurrency,
    )


def _collect_variants_by_queries(
    query_specs: Iterable[Tuple[str, str]],
    *,
    progress_callback: Optional[ProgressCallback] = None,
    concurrency: Optional[int] = None,
) -> Dict[str, SpellbookVariantRecord]:
    max_workers = max(1, int(concurrency or DEFAULT_SPELLBOOK_CONCURRENCY))
    progress_lock = threading.Lock()
    total_lock = threading.Lock()
    global_total = 0
    global_fetched = 0

    def _register_total(count: int) -> None:
        nonlocal global_total
        with total_lock:
            global_total += count

    def _safe_progress(_: int, __: Optional[int]) -> None:
        nonlocal global_fetched
        with progress_lock:
            global_fetched += 1
            total_snapshot = max(global_total, global_fetched)
        if not progress_callback:
            return
        try:
            progress_callback(global_fetched, total_snapshot or None)
        except Exception:
            # Progress reporting is best-effort.
            pass

    def _fetch_for_query(category: str, query: str) -> Dict[str, SpellbookVariantRecord]:
        params = {
            "limit": str(PAGE_SIZE),
            "q": f"{query}{DEFAULT_QUERY_SUFFIX}",
        }
        local_variants: Dict[str, SpellbookVariantRecord] = {}
        for variant in _iter_variants(
            params,
            progress_callback=_safe_progress,
            total_callback=_register_total,
        ):
            entry = local_variants.get(variant["id"])
            if entry is None:
                entry = SpellbookVariantRecord(variant=variant, results=set(), categories=set())
                local_variants[variant["id"]] = entry
            entry.categories.add(category)
            for produced in variant.get("produces", []):
                name = (produced.get("feature") or {}).get("name")
                if name:
                    entry.results.add(name)
        return local_variants

    variants: Dict[str, SpellbookVariantRecord] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(_fetch_for_query, category, query): (category, query)
            for category, query in query_specs
        }
        for future in as_completed(future_map):
            local_variants = future.result()
            for vid, entry in local_variants.items():
                existing = variants.get(vid)
                if existing is None:
                    variants[vid] = entry
                else:
                    existing.categories.update(entry.categories)
                    existing.results.update(entry.results)
    return variants


def collect_relevant_spellbook_variants(
    progress_callback: Optional[ProgressCallback] = None,
    *,
    concurrency: Optional[int] = None,
) -> Dict[str, SpellbookVariantRecord]:
    specs: List[Tuple[str, str]] = []
    for category, queries in RESULT_QUERY_GROUPS.items():
        for query in queries:
            specs.append((category, query))
    return _collect_variants_by_queries(
        specs,
        progress_callback=progress_callback,
        concurrency=concurrency,
    )


def _combo_requirements(variant: Dict[str, any]) -> Dict[str, int]:
    requirements: Dict[str, int] = {}
    for component in variant.get("uses", []):
        card_info = component.get("card") or {}
        name = str(card_info.get("name") or "").strip()
        if not name:
            continue
        qty = int(component.get("quantity") or 1)
        requirements[name] = requirements.get(name, 0) + qty
    return requirements


def _serialize_cards(requirements: Dict[str, int]) -> List[Dict[str, any]]:
    return [
        {"name": name, "quantity": qty}
        for name, qty in sorted(requirements.items(), key=lambda item: item[0].casefold())
    ]


def generate_spellbook_combo_dataset(
    *,
    early_threshold: int = EARLY_MANA_VALUE_THRESHOLD,
    late_threshold: int = LATE_MANA_VALUE_THRESHOLD,
    card_count_targets: Iterable[int] | None = None,
    progress_callback: Optional[ProgressCallback] = None,
    concurrency: Optional[int] = None,
    existing_ids: Optional[Iterable[str]] = None,
) -> Dict[str, any]:
    targets = tuple(sorted(set(card_count_targets or (2, 3))))
    existing_id_set = {str(i) for i in (existing_ids or [])}
    fetched_variants = collect_relevant_spellbook_variants(
        progress_callback=progress_callback,
        concurrency=concurrency,
    )
    early_game: List[Dict[str, any]] = []
    late_game: List[Dict[str, any]] = []

    stats = Counter()
    for record in fetched_variants.values():
        variant = record.variant
        requirements = _combo_requirements(variant)
        stats["total_variants"] += 1

        if existing_id_set and variant["id"] in existing_id_set:
            stats["skipped_existing"] += 1
            continue

        if targets and len(requirements) not in targets:
            stats["skipped_wrong_card_count"] += 1
            continue

        raw_mana_value = variant.get("manaValueNeeded")
        mana_value_needed: Optional[int]
        if isinstance(raw_mana_value, int):
            mana_value_needed = raw_mana_value
        elif isinstance(raw_mana_value, str):
            try:
                mana_value_needed = int(raw_mana_value)
            except ValueError:
                mana_value_needed = None
        else:
            mana_value_needed = None

        if mana_value_needed is None:
            stats["missing_mana_value"] += 1
            normalized_mana_value = late_threshold
        else:
            if mana_value_needed < 0:
                mana_value_needed = 0
            normalized_mana_value = mana_value_needed

        serialized_entry = {
            "id": variant["id"],
            "cards": _serialize_cards(requirements),
            "requirements": _serialize_cards(requirements),
            "results": sorted(record.results),
            "produces": [
                feature.get("feature", {}).get("name")
                for feature in variant.get("produces", [])
            ],
            "mana_value_needed": mana_value_needed,
            "normalized_mana_value": normalized_mana_value,
            "mana_needed": variant.get("manaNeeded") or "",
            "bracket_tag": variant.get("bracketTag"),
            "identity": variant.get("identity"),
            "easy_prerequisites": variant.get("easyPrerequisites") or "",
            "notable_prerequisites": variant.get("notablePrerequisites") or "",
            "description": variant.get("description") or "",
            "url": f"https://commanderspellbook.com/combo/{variant['id']}",
            "result_categories": sorted(record.categories),
        }

        if normalized_mana_value <= early_threshold:
            serialized_entry["category"] = "early"
            early_game.append(serialized_entry)
            stats["early_game"] += 1
        else:
            serialized_entry["category"] = "late"
            late_game.append(serialized_entry)
            stats["late_game"] += 1
        for category in record.categories:
            stats[f"category_{category}"] += 1

    dataset = {
        "fetched_at": datetime.now(tz=timezone.utc).isoformat(),
        "source": API_BASE_URL,
        "instant_win_results": INSTANT_WIN_RESULTS,
        "thresholds": {
            "early_mana_value": early_threshold,
            "late_mana_value": late_threshold,
        "card_counts": list(targets) if targets else [],
        },
        "counts": stats,
        "early_game": sorted(
            early_game,
            key=lambda entry: (entry.get("normalized_mana_value", entry.get("mana_value_needed", 0)), entry["id"]),
        ),
        "late_game": sorted(
            late_game,
            key=lambda entry: (entry.get("normalized_mana_value", entry.get("mana_value_needed", 0)), entry["id"]),
        ),
    }
    return dataset


def write_dataset_to_file(dataset: Dict[str, any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(dataset, handle, indent=2, ensure_ascii=False)
