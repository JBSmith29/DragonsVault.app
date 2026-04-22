"""Catalog/search helpers backed by a local Scryfall default-cards JSON file."""

from __future__ import annotations

import gzip
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


def clear_cached_catalog() -> None:
    """Clear cached print/token indexes after the underlying file changes."""
    get_all_prints.cache_clear()
    find_print_by_id.cache_clear()
    _token_name_index.cache_clear()


def _read_json_array(path: Path):
    with open(path, "rb") as fh:
        head = fh.read(2)
    is_gz = head == b"\x1f\x8b" or str(path).lower().endswith(".gz")
    if is_gz:
        with gzip.open(path, "rt", encoding="utf-8") as fin:
            return json.load(fin)
    with open(path, "r", encoding="utf-8") as fin:
        return json.load(fin)


def _normalize_search_text(value: str) -> str:
    if not value:
        return ""
    return re.sub(r"[^a-z0-9]+", " ", str(value).lower()).strip()


def _image_uris(print_obj: Dict[str, Any]) -> Dict[str, Optional[str]]:
    image_uris = print_obj.get("image_uris") or {}
    if image_uris:
        return {
            "small": image_uris.get("small"),
            "normal": image_uris.get("normal"),
            "large": image_uris.get("large"),
        }
    faces = print_obj.get("card_faces") or []
    if faces and isinstance(faces, list):
        face_images = (faces[0] or {}).get("image_uris") or {}
        return {
            "small": face_images.get("small"),
            "normal": face_images.get("normal"),
            "large": face_images.get("large"),
        }
    return {"small": None, "normal": None, "large": None}


@lru_cache(maxsize=8)
def get_all_prints(default_path: str) -> List[Dict[str, Any]]:
    path = Path(default_path)
    if not path.exists() or path.stat().st_size == 0:
        return []
    try:
        data = _read_json_array(path)
    except Exception:
        return []
    return data if isinstance(data, list) else []


@lru_cache(maxsize=32768)
def find_print_by_id(default_path: str, sid: str):
    if not sid:
        return None
    lookup_id = str(sid).lower()
    for print_obj in get_all_prints(default_path):
        print_id = (print_obj.get("id") or "").lower()
        if print_id == lookup_id:
            return print_obj
    return None


def search_prints(
    default_path: str,
    *,
    name_q: str | None = None,
    set_code: str | None = None,
    limit: int = 60,
    offset: int = 0,
) -> Tuple[List[Dict[str, Any]], int]:
    prints = get_all_prints(default_path)
    if not prints:
        return [], 0

    tokens = [token for token in _normalize_search_text(name_q).split() if token]
    wanted_set = (set_code or "").lower()

    def matches(print_obj: Dict[str, Any]) -> bool:
        if wanted_set and (print_obj.get("set") or "").lower() != wanted_set:
            return False
        if not tokens:
            return True
        haystack = _normalize_search_text(print_obj.get("name"))
        return all(token in haystack for token in tokens)

    filtered = (print_obj for print_obj in prints if matches(print_obj))
    buffer: List[Dict[str, Any]] = []
    total = 0
    limit = int(limit or 0)
    offset = max(int(offset or 0), 0)
    stop_at = offset + limit if limit > 0 else None
    for print_obj in filtered:
        if stop_at is None or total < stop_at:
            buffer.append(print_obj)
        total += 1
    if limit <= 0:
        return buffer[offset:], total
    return buffer[offset : offset + limit], total


def _image_set_for_print(print_obj: Dict[str, Any]):
    label_bits = []
    if print_obj.get("set"):
        label_bits.append((print_obj.get("set") or "").upper())
    if print_obj.get("collector_number"):
        label_bits.append(str(print_obj.get("collector_number")))
    if print_obj.get("lang"):
        label_bits.append(str(print_obj.get("lang")).upper())
    label = " · ".join(label_bits) if label_bits else (print_obj.get("name") or "")

    image_uris = print_obj.get("image_uris") or {}
    faces = print_obj.get("card_faces") or []
    small = image_uris.get("small")
    normal = image_uris.get("normal") or image_uris.get("large")
    if (not small) and faces and isinstance(faces, list):
        face_images = (faces[0] or {}).get("image_uris") or {}
        small = face_images.get("small")
        normal = normal or face_images.get("normal") or face_images.get("large")

    key = print_obj.get("illustration_id") or print_obj.get("id")
    return small, normal, label, key


def _unique_art_images(prints, per_card_images=8):
    images = []
    seen = set()
    for print_obj in prints or []:
        small, normal, label, key = _image_set_for_print(print_obj)
        if not (small or normal):
            continue
        if key and key in seen:
            continue
        seen.add(key)
        images.append({"small": small, "normal": normal, "label": label})
        if len(images) >= per_card_images:
            break
    return images


def search_unique_cards(
    default_path: str,
    *,
    name_q: str | None = None,
    set_code: str | None = None,
    limit: int = 60,
    offset: int = 0,
    per_card_images: int = 8,
):
    prints = get_all_prints(default_path)
    if not prints:
        return [], 0

    tokens = [token for token in _normalize_search_text(name_q).split() if token]
    wanted_set = (set_code or "").lower()
    groups: Dict[str, Dict[str, Any]] = {}

    for print_obj in prints:
        name = _normalize_search_text(print_obj.get("name"))
        if tokens and any(token not in name for token in tokens):
            continue

        oracle_id = print_obj.get("oracle_id") or print_obj.get("id")
        if not oracle_id:
            continue

        group = groups.get(oracle_id)
        if group is None:
            groups[oracle_id] = {
                "oracle_id": oracle_id,
                "rep": print_obj,
                "has_wanted_set": ((print_obj.get("set") or "").lower() == wanted_set) if wanted_set else True,
                "members": [print_obj],
            }
            continue

        if wanted_set and not group["has_wanted_set"]:
            group["has_wanted_set"] = ((print_obj.get("set") or "").lower() == wanted_set)
        if len(group["members"]) < max(12, per_card_images * 2):
            group["members"].append(print_obj)

    items = []
    for group in groups.values():
        if wanted_set and not group["has_wanted_set"]:
            continue

        rep = group["rep"]
        purchase_uris = rep.get("purchase_uris") or {}
        tcgplayer_url = purchase_uris.get("tcgplayer") or (rep.get("related_uris") or {}).get("tcgplayer")
        items.append(
            {
                "oracle_id": group["oracle_id"],
                "id": rep.get("id"),
                "name": rep.get("name"),
                "set": (rep.get("set") or "").upper(),
                "set_name": rep.get("set_name"),
                "collector_number": rep.get("collector_number"),
                "lang": (rep.get("lang") or "").upper(),
                "rarity": (rep.get("rarity") or "").title() if rep.get("rarity") else None,
                "scryfall_uri": rep.get("scryfall_uri"),
                "tcgplayer_url": tcgplayer_url,
                "images": _unique_art_images(group["members"], per_card_images=per_card_images),
            }
        )

    items.sort(key=lambda item: ((item["name"] or "").lower(), item["set"], str(item["collector_number"] or "")))
    total = len(items)
    return items[offset : offset + limit], total


@lru_cache(maxsize=8)
def _token_name_index(default_path: str) -> Dict[str, Dict[str, Any]]:
    """
    Build a name->print index for token cards.

    Prefer EN tokens when multiple printings share the same name.
    """
    index: Dict[str, Dict[str, Any]] = {}
    for print_obj in get_all_prints(default_path):
        if (print_obj.get("layout") or "").lower() != "token":
            continue
        name = (print_obj.get("name") or "").strip()
        if not name:
            continue
        key = name.casefold()
        lang = (print_obj.get("lang") or "en").lower()
        existing = index.get(key)
        if existing is None or (existing.get("lang", "").lower() != "en" and lang == "en"):
            index[key] = print_obj
    return index


def search_tokens(default_path: str, *, name_q: str | None = None, limit: int = 36) -> List[Dict[str, Any]]:
    if not name_q:
        return []
    normalized = _normalize_search_text(name_q)
    if not normalized:
        return []
    tokens = [token for token in normalized.split() if token]
    results: List[Dict[str, Any]] = []
    for print_obj in _token_name_index(default_path).values():
        name = (print_obj.get("name") or "").strip()
        if not name:
            continue
        type_line = (print_obj.get("type_line") or "").strip()
        haystack = f"{name} {type_line}".casefold()
        if tokens and any(token not in haystack for token in tokens):
            continue
        images = _image_uris(print_obj)
        results.append(
            {
                "id": print_obj.get("id"),
                "name": name,
                "type_line": type_line,
                "power": print_obj.get("power"),
                "toughness": print_obj.get("toughness"),
                "images": images,
            }
        )
    results.sort(key=lambda item: (item.get("name") or "").lower())
    if limit and len(results) > limit:
        return results[:limit]
    return results


def _dedupe_tokens(items: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    deduped = []
    for item in items:
        key = (
            item.get("id") or "",
            (item.get("name") or "").casefold(),
            (item.get("type_line") or "").casefold(),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _generic_token() -> Dict[str, Any]:
    return {
        "id": None,
        "name": "Token",
        "type_line": None,
        "power": None,
        "toughness": None,
        "images": {"small": None, "normal": None},
    }


def tokens_from_print(default_path: str, print_obj: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Resolve token records created by a print via `all_parts` or generic fallback."""
    if not print_obj:
        return []

    parts = print_obj.get("all_parts") or []
    token_parts = [part for part in parts if part.get("component") == "token"]
    tokens: List[Dict[str, Any]] = []
    seen = set()

    for token_part in token_parts:
        token_id = token_part.get("id")
        if not token_id or token_id in seen:
            continue
        seen.add(token_id)

        token_print = None
        try:
            token_print = find_print_by_id(default_path, token_id)
        except Exception:
            token_print = None

        name = (token_print or {}).get("name") or token_part.get("name")
        type_line = (token_print or {}).get("type_line")
        images = _image_uris(token_print or {})
        tokens.append(
            {
                "id": token_id,
                "name": name,
                "type_line": type_line,
                "power": (token_print or {}).get("power"),
                "toughness": (token_print or {}).get("toughness"),
                "images": {"small": images.get("small"), "normal": images.get("normal") or images.get("large")},
            }
        )

    if tokens:
        return _dedupe_tokens(tokens)

    oracle_text = (print_obj.get("oracle_text") or "").lower()
    if "create" in oracle_text and "token" in oracle_text:
        return [_generic_token()]
    return []


def tokens_from_oracle(default_path: str, print_objects: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    tokens: List[Dict[str, Any]] = []
    for print_obj in print_objects or []:
        tokens.extend(tokens_from_print(default_path, print_obj))
    return _dedupe_tokens(tokens)
