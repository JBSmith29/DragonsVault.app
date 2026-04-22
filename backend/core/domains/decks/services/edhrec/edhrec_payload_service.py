"""EDHREC payload parsing helpers."""

from __future__ import annotations

from typing import Any, Iterable

from core.domains.decks.services.deck_tags import ensure_deck_tag, normalize_tag_label, resolve_deck_tag_from_slug

_TYPE_LABEL_MAP = {
    "creature": "Creature",
    "creatures": "Creature",
    "instant": "Instant",
    "instants": "Instant",
    "sorcery": "Sorcery",
    "sorceries": "Sorcery",
    "artifact": "Artifact",
    "artifacts": "Artifact",
    "enchantment": "Enchantment",
    "enchantments": "Enchantment",
    "planeswalker": "Planeswalker",
    "planeswalkers": "Planeswalker",
    "land": "Land",
    "lands": "Land",
    "battle": "Battle",
    "battles": "Battle",
    "other": "Other",
}


def safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def inclusion_percent(raw_inclusion: Any, raw_num_decks: Any, raw_potential_decks: Any) -> float | None:
    inclusion = safe_float(raw_inclusion)
    num_decks = safe_float(raw_num_decks)
    potential_decks = safe_float(raw_potential_decks)
    if potential_decks and potential_decks > 0:
        numerator = num_decks if num_decks is not None else inclusion
        if numerator is not None:
            pct = (numerator / potential_decks) * 100.0
            return round(min(max(pct, 0.0), 100.0), 1)
    if inclusion is None:
        return None
    if inclusion <= 1:
        return round(min(max(inclusion * 100.0, 0.0), 100.0), 1)
    return round(min(max(inclusion, 0.0), 100.0), 1)


def normalize_header(header: Any) -> str | None:
    if isinstance(header, str):
        cleaned = header.strip()
        return cleaned or None
    if isinstance(header, dict):
        for key in ("title", "label", "text"):
            value = header.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def extract_cardviews(payload: dict) -> list[dict]:
    cardlists = payload.get("cardlists") or []
    if not isinstance(cardlists, list):
        return []
    candidates = [entry for entry in cardlists if isinstance(entry, dict)]
    synergy_lists = [
        entry for entry in candidates
        if isinstance(entry.get("header"), str) and "synergy" in entry.get("header").lower()
    ]
    selected = synergy_lists or candidates
    views: list[dict] = []
    for entry in selected:
        for raw in entry.get("cardviews") or []:
            if not isinstance(raw, dict):
                continue
            name = raw.get("name")
            if not name:
                continue
            views.append(
                {
                    "name": name,
                    "rank": raw.get("rank"),
                    "synergy": safe_float(raw.get("synergy")),
                    "inclusion": inclusion_percent(
                        raw.get("inclusion"),
                        raw.get("num_decks"),
                        raw.get("potential_decks"),
                    ),
                }
            )
    return views


def extract_cardlists(payload: dict) -> list[dict]:
    cardlists = payload.get("cardlists") or []
    if not isinstance(cardlists, list):
        return []
    lists: list[dict] = []
    for idx, entry in enumerate(cardlists, start=1):
        if not isinstance(entry, dict):
            continue
        header = normalize_header(entry.get("header"))
        if not header:
            continue
        raw_views = entry.get("cardviews") or []
        if not isinstance(raw_views, list):
            continue
        views: list[dict] = []
        for raw in raw_views:
            if not isinstance(raw, dict):
                continue
            name = raw.get("name")
            if not name:
                continue
            views.append(
                {
                    "name": name,
                    "rank": raw.get("rank"),
                    "synergy": safe_float(raw.get("synergy")),
                    "inclusion": inclusion_percent(
                        raw.get("inclusion"),
                        raw.get("num_decks"),
                        raw.get("potential_decks"),
                    ),
                }
            )
        if views:
            lists.append({"category": header, "category_rank": idx, "views": views})
    return lists


def normalize_tag_candidates(raw: dict) -> list[str]:
    candidates: list[list[dict]] = []

    def walk(obj: Any) -> None:
        if isinstance(obj, dict):
            for value in obj.values():
                walk(value)
        elif isinstance(obj, list):
            if obj and all(isinstance(item, dict) for item in obj):
                if any("cardviews" in item or "header" in item for item in obj):
                    return
                if all("slug" in item for item in obj):
                    candidates.append(obj)
            for item in obj:
                walk(item)

    walk(raw)

    if not candidates:
        return []

    candidates.sort(key=len, reverse=True)
    raw_list = candidates[0]
    tags: list[str] = []
    for entry in raw_list:
        slug = entry.get("slug")
        label = entry.get("label") or entry.get("name")
        if not isinstance(slug, str) and not isinstance(label, str):
            continue
        candidate = None
        if isinstance(slug, str) and slug.strip():
            candidate = resolve_deck_tag_from_slug(slug)
        if not candidate and isinstance(label, str) and label.strip():
            candidate = resolve_deck_tag_from_slug(label)
        if not candidate:
            candidate = normalize_tag_label(label or slug or "")
        if candidate:
            tags.append(candidate)
    deduped: list[str] = []
    seen = set()
    for tag in tags:
        key = tag.casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(tag)
    return deduped


def normalize_type_label(label: str | None) -> str | None:
    if not label:
        return None
    cleaned = str(label).strip()
    if not cleaned:
        return None
    key = cleaned.casefold()
    if key in _TYPE_LABEL_MAP:
        return _TYPE_LABEL_MAP[key]
    if "creature" in key:
        return "Creature"
    if "instant" in key:
        return "Instant"
    if "sorcery" in key:
        return "Sorcery"
    if "artifact" in key:
        return "Artifact"
    if "enchantment" in key:
        return "Enchantment"
    if "planeswalker" in key:
        return "Planeswalker"
    if "land" in key:
        return "Land"
    if "battle" in key:
        return "Battle"
    return "Other"


def extract_type_distribution(payload: dict | None) -> list[dict]:
    if not isinstance(payload, dict):
        return []
    panels = None
    if isinstance(payload.get("panels"), dict):
        panels = payload.get("panels")
    elif isinstance(payload.get("container"), dict) and isinstance(payload["container"].get("panels"), dict):
        panels = payload["container"].get("panels")
    else:
        container = payload.get("props", {}).get("pageProps", {}).get("data", {}).get("container")
        if isinstance(container, dict):
            panels = container.get("panels")
    if panels is None:
        data = payload.get("props", {}).get("pageProps", {}).get("data", {})
        if isinstance(data, dict) and isinstance(data.get("panels"), dict):
            panels = data.get("panels")
    if not isinstance(panels, dict):
        def _extract_counts(source: dict) -> dict[str, int]:
            counts: dict[str, int] = {}
            for key, label in (
                ("creature", "Creature"),
                ("instant", "Instant"),
                ("sorcery", "Sorcery"),
                ("artifact", "Artifact"),
                ("enchantment", "Enchantment"),
                ("planeswalker", "Planeswalker"),
                ("land", "Land"),
                ("battle", "Battle"),
            ):
                val = source.get(key)
                if val is None:
                    continue
                try:
                    numeric_value = int(round(float(val)))
                except (TypeError, ValueError):
                    continue
                if numeric_value <= 0:
                    continue
                counts[label] = counts.get(label, 0) + numeric_value
            return counts

        data = payload.get("props", {}).get("pageProps", {}).get("data", {})
        counts = _extract_counts(payload)
        if isinstance(data, dict) and not counts:
            counts = _extract_counts(data)
        if not counts:
            return []
        return [{"card_type": key, "count": int(value)} for key, value in counts.items() if value]
    piechart = panels.get("piechart")
    if not isinstance(piechart, dict):
        return []
    content = piechart.get("content")
    if not isinstance(content, list):
        return []
    counts: dict[str, int] = {}
    for item in content:
        if not isinstance(item, dict):
            continue
        label = item.get("label")
        value = item.get("value")
        if label is None or value is None:
            continue
        try:
            numeric_value = int(round(float(value)))
        except (TypeError, ValueError):
            continue
        card_type = normalize_type_label(label)
        if not card_type:
            continue
        counts[card_type] = counts.get(card_type, 0) + numeric_value
    if not counts:
        return []
    return [{"card_type": key, "count": int(value)} for key, value in counts.items() if value]


def extract_type_distribution_from_sources(primary: dict | None, fallback: dict | None) -> list[dict]:
    rows = extract_type_distribution(primary)
    if rows:
        return rows
    return extract_type_distribution(fallback) if fallback is not None else []


def upsert_edhrec_tags(tags: Iterable[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for tag in tags or []:
        cleaned = normalize_tag_label(tag)
        if not cleaned:
            continue
        key = cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        tag_row = ensure_deck_tag(cleaned, source="edhrec")
        normalized.append(tag_row.name if tag_row else cleaned)
    return normalized


def merge_tags(primary: Iterable[str], secondary: Iterable[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for tag in list(primary) + list(secondary):
        label = (tag or "").strip()
        if not label:
            continue
        key = label.casefold()
        if key in seen:
            continue
        seen.add(key)
        merged.append(label)
    return merged


def map_synergy_cards(
    views: Iterable[dict],
    *,
    lookup_oracle_id_fn,
    max_synergy_cards: int | None,
) -> list[dict]:
    card_map: dict[str, dict] = {}
    for view in views:
        name = view.get("name")
        if not isinstance(name, str):
            continue
        oracle_id = lookup_oracle_id_fn(name)
        if not oracle_id:
            continue
        score = view.get("synergy")
        inclusion = view.get("inclusion")
        rank = view.get("rank")
        existing = card_map.get(oracle_id)
        if existing is None or (score or 0) > (existing.get("synergy_score") or 0):
            card_map[oracle_id] = {
                "card_oracle_id": oracle_id,
                "synergy_score": score,
                "inclusion_percent": inclusion,
                "rank_hint": rank,
            }
        elif score == existing.get("synergy_score") and inclusion is not None:
            if (existing.get("inclusion_percent") or 0) < inclusion:
                existing["inclusion_percent"] = inclusion
            if rank is not None and (existing.get("rank_hint") is None or rank < existing["rank_hint"]):
                existing["rank_hint"] = rank

    items = list(card_map.values())
    if any(item.get("synergy_score") is not None for item in items):
        items.sort(key=lambda item: (-(item.get("synergy_score") or 0.0), item.get("rank_hint") or 0))
    else:
        items.sort(key=lambda item: (item.get("rank_hint") or 0, item.get("card_oracle_id") or ""))

    ranked: list[dict] = []
    limited = items if max_synergy_cards is None else items[:max_synergy_cards]
    for idx, item in enumerate(limited, start=1):
        ranked.append(
            {
                "card_oracle_id": item["card_oracle_id"],
                "synergy_rank": idx,
                "synergy_score": item.get("synergy_score"),
                "inclusion_percent": item.get("inclusion_percent"),
            }
        )
    return ranked


def map_category_cards(
    cardlists: Iterable[dict],
    *,
    lookup_oracle_id_fn,
    max_synergy_cards: int | None,
) -> list[dict]:
    rows: list[dict] = []
    for entry in cardlists:
        category = entry.get("category")
        if not category:
            continue
        category_rank = int(entry.get("category_rank") or 0) or None
        views = entry.get("views") or []
        card_map: dict[str, dict] = {}
        for view in views:
            name = view.get("name")
            if not isinstance(name, str):
                continue
            oracle_id = lookup_oracle_id_fn(name)
            if not oracle_id:
                continue
            score = view.get("synergy")
            inclusion = view.get("inclusion")
            rank = view.get("rank")
            existing = card_map.get(oracle_id)
            if existing is None or (score or 0) > (existing.get("synergy_score") or 0):
                card_map[oracle_id] = {
                    "card_oracle_id": oracle_id,
                    "synergy_score": score,
                    "inclusion_percent": inclusion,
                    "rank_hint": rank,
                }
            elif score == existing.get("synergy_score"):
                if inclusion is not None and (existing.get("inclusion_percent") or 0) < inclusion:
                    existing["inclusion_percent"] = inclusion
                if rank is not None and (existing.get("rank_hint") is None or rank < existing["rank_hint"]):
                    existing["rank_hint"] = rank

        items = list(card_map.values())
        if any(item.get("synergy_score") is not None for item in items):
            items.sort(key=lambda item: (-(item.get("synergy_score") or 0.0), item.get("rank_hint") or 0))
        else:
            items.sort(key=lambda item: (item.get("rank_hint") or 0, item.get("card_oracle_id") or ""))

        limited = items if max_synergy_cards is None else items[:max_synergy_cards]
        for idx, item in enumerate(limited, start=1):
            rows.append(
                {
                    "category": category,
                    "category_rank": category_rank,
                    "card_oracle_id": item["card_oracle_id"],
                    "synergy_rank": idx,
                    "synergy_score": item.get("synergy_score"),
                    "inclusion_percent": item.get("inclusion_percent"),
                }
            )
    return rows


__all__ = [
    "extract_cardlists",
    "extract_cardviews",
    "extract_type_distribution",
    "extract_type_distribution_from_sources",
    "inclusion_percent",
    "map_category_cards",
    "map_synergy_cards",
    "merge_tags",
    "normalize_header",
    "normalize_tag_candidates",
    "normalize_type_label",
    "safe_float",
    "upsert_edhrec_tags",
]
