from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Tuple

import requests

__all__ = [
    "INSTANT_WIN_RESULTS",
    "RESULT_QUERY_GROUPS",
    "EARLY_MANA_VALUE_THRESHOLD",
    "LATE_MANA_VALUE_THRESHOLD",
    "collect_instant_win_variants",
    "collect_relevant_spellbook_variants",
    "generate_spellbook_combo_dataset",
    "write_dataset_to_file",
]

API_BASE_URL = "https://backend.commanderspellbook.com/variants/"
PAGE_SIZE = 100
DEFAULT_QUERY_SUFFIX = " legal:commander"

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


def _iter_variants(params: Optional[Dict[str, str]] = None) -> Iterator[Dict[str, any]]:
    next_url: Optional[str] = API_BASE_URL
    query = dict(params) if params else {"limit": PAGE_SIZE}

    while next_url:
        response = requests.get(
            next_url,
            params=query if next_url == API_BASE_URL else None,
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()

        for item in payload.get("results", []):
            yield item

        next_url = payload.get("next")
        query = None


def collect_instant_win_variants() -> Dict[str, SpellbookVariantRecord]:
    return _collect_variants_by_queries([("instant_win", query) for query in RESULT_QUERY_GROUPS["instant_win"]])


def _collect_variants_by_queries(query_specs: Iterable[Tuple[str, str]]) -> Dict[str, SpellbookVariantRecord]:
    variants: Dict[str, SpellbookVariantRecord] = {}
    for category, query in query_specs:
        params = {
            "limit": str(PAGE_SIZE),
            "q": f"{query}{DEFAULT_QUERY_SUFFIX}",
        }
        for variant in _iter_variants(params):
            entry = variants.get(variant["id"])
            if entry is None:
                entry = SpellbookVariantRecord(variant=variant, results=set(), categories=set())
                variants[variant["id"]] = entry
            entry.categories.add(category)
            for produced in variant.get("produces", []):
                name = (produced.get("feature") or {}).get("name")
                if name:
                    entry.results.add(name)
    return variants


def collect_relevant_spellbook_variants() -> Dict[str, SpellbookVariantRecord]:
    specs: List[Tuple[str, str]] = []
    for category, queries in RESULT_QUERY_GROUPS.items():
        for query in queries:
            specs.append((category, query))
    return _collect_variants_by_queries(specs)


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
) -> Dict[str, any]:
    targets = tuple(sorted(set(card_count_targets or (2, 3))))
    fetched_variants = collect_relevant_spellbook_variants()
    early_game: List[Dict[str, any]] = []
    late_game: List[Dict[str, any]] = []

    stats = Counter()
    for record in fetched_variants.values():
        variant = record.variant
        requirements = _combo_requirements(variant)
        stats["total_variants"] += 1

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
