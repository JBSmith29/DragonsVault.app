"""Scryfall default-cards index and lookup helpers."""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


def name_key(name: str) -> str:
    """Case- and punctuation-insensitive key for name comparisons."""
    normalized = (name or "").casefold()
    try:
        normalized = re.sub(r"[\s\p{P}]+", "", normalized)
    except Exception:
        normalized = re.sub(r"[\W_]+", "", normalized)
    return normalized


def front_face_name(card_obj: Dict[str, Any]) -> str:
    faces = card_obj.get("card_faces") or []
    if faces and isinstance(faces, list):
        return (faces[0] or {}).get("name", "") or ""
    return card_obj.get("name", "") or ""


def back_face_names(card_obj: Dict[str, Any]) -> List[str]:
    faces = card_obj.get("card_faces") or []
    if not faces or not isinstance(faces, list) or len(faces) <= 1:
        return []
    names = []
    for face in faces[1:]:
        label = (face or {}).get("name", "") or ""
        if label:
            names.append(label)
    return names


def cn_variants(cn: str) -> List[str]:
    """Generate tolerant collector-number variants."""
    text = str(cn or "").strip().lower()
    out = [text]
    lstripped = text.lstrip("0") or "0"
    if lstripped not in out:
        out.append(lstripped)
    match = re.match(r"^(\d+)[a-z]?$", text)
    if match:
        core = match.group(1)
        if core not in out:
            out.append(core)
    digits = "".join(ch for ch in text if ch.isdigit())
    if digits and digits not in out:
        out.append(digits)
    return out


def cn_num(cn: str) -> Optional[int]:
    match = re.match(r"(\d+)", str(cn or ""))
    return int(match.group(1)) if match else None


def prime_default_indexes(
    cache: List[Dict[str, Any]],
    *,
    by_set_cn: Dict[str, Dict[str, Any]],
    by_oracle: Dict[str, List[Dict[str, Any]]],
    idx_by_set_num: Dict[Tuple[str, int], List[Dict[str, Any]]],
    idx_by_name: Dict[str, List[Dict[str, Any]]],
    idx_by_front: Dict[str, List[Dict[str, Any]]],
    idx_by_back: Dict[str, List[Dict[str, Any]]],
    key_set_cn_fn: Callable[[str, str], str],
) -> None:
    by_set_cn.clear()
    by_oracle.clear()
    idx_by_set_num.clear()
    idx_by_name.clear()
    idx_by_front.clear()
    idx_by_back.clear()

    for card in cache:
        set_code = (card.get("set") or "").lower()
        collector_number = str(card.get("collector_number") or "")
        if set_code and collector_number:
            by_set_cn[key_set_cn_fn(set_code, collector_number)] = card
            number = cn_num(collector_number)
            if number is not None:
                idx_by_set_num.setdefault((set_code, number), []).append(card)

        oracle_id = card.get("oracle_id")
        if oracle_id:
            by_oracle.setdefault(oracle_id, []).append(card)

        full_name_key = name_key(card.get("name", ""))
        if full_name_key:
            idx_by_name.setdefault(full_name_key, []).append(card)

        front_name_key = name_key(front_face_name(card))
        if front_name_key:
            idx_by_front.setdefault(front_name_key, []).append(card)
        for back_name in back_face_names(card):
            back_name_key = name_key(back_name)
            if back_name_key:
                idx_by_back.setdefault(back_name_key, []).append(card)


def find_by_set_cn_loose(
    set_code: str,
    collector_number: str,
    name_hint: Optional[str] = None,
    *,
    by_set_cn: Dict[str, Dict[str, Any]],
    idx_by_set_num: Dict[Tuple[str, int], List[Dict[str, Any]]],
) -> Optional[Dict[str, Any]]:
    """Tolerant set+collector lookup using collector variants and name hints."""
    normalized_set = (set_code or "").lower()
    if not normalized_set or not collector_number:
        return None

    candidates = []
    for variant in cn_variants(str(collector_number)):
        item = by_set_cn.get(f"{normalized_set}::{variant}")
        if item:
            candidates.append(item)
    if not candidates:
        number = cn_num(collector_number)
        if number is not None:
            candidates = list(idx_by_set_num.get((normalized_set, number), []) or [])
    if not candidates:
        return None
    if len(candidates) == 1 or not name_hint:
        return candidates[0]

    normalized_name = name_key(name_hint)
    for candidate in candidates:
        if name_key(candidate.get("name", "")) == normalized_name:
            return candidate

    front_name_key = name_key(name_hint.split("//", 1)[0].strip()) if "//" in (name_hint or "") else normalized_name
    for candidate in candidates:
        if name_key(front_face_name(candidate)) == front_name_key:
            return candidate
    return candidates[0]


def find_by_set_cn(
    set_code: str,
    collector_number: str,
    name_hint: Optional[str] = None,
    *,
    by_set_cn: Dict[str, Dict[str, Any]],
    idx_by_set_num: Dict[Tuple[str, int], List[Dict[str, Any]]],
    idx_by_name: Dict[str, List[Dict[str, Any]]],
    idx_by_front: Dict[str, List[Dict[str, Any]]],
    key_set_cn_fn: Callable[[str, str], str],
) -> Optional[Dict[str, Any]]:
    """Find the best-matching print for a set and collector number."""
    if not set_code or not collector_number:
        return None
    normalized_set = (set_code or "").lower()
    collector_number = str(collector_number or "").strip()

    item = by_set_cn.get(key_set_cn_fn(normalized_set, collector_number))
    if item:
        return item

    candidate = find_by_set_cn_loose(
        normalized_set,
        collector_number,
        name_hint=name_hint,
        by_set_cn=by_set_cn,
        idx_by_set_num=idx_by_set_num,
    )
    if candidate:
        return candidate

    normalized_name = name_key(name_hint or "")
    if normalized_name:
        candidates = [card for card in idx_by_name.get(normalized_name, []) if (card.get("set") or "").lower() == normalized_set]
        if len(candidates) == 1:
            return candidates[0]
        if candidates:
            number = cn_num(collector_number)
            if number is not None:
                same_num = [card for card in candidates if cn_num(card.get("collector_number")) == number]
                if len(same_num) == 1:
                    return same_num[0]
            return candidates[0]

        candidates = [card for card in idx_by_front.get(normalized_name, []) if (card.get("set") or "").lower() == normalized_set]
        if len(candidates) == 1:
            return candidates[0]
        if candidates:
            number = cn_num(collector_number)
            if number is not None:
                same_num = [card for card in candidates if cn_num(card.get("collector_number")) == number]
                if len(same_num) == 1:
                    return same_num[0]
            return candidates[0]

        candidates = (idx_by_name.get(normalized_name) or []) + (idx_by_front.get(normalized_name) or [])
        candidates = [card for card in candidates if card.get("set_type") not in ("token", "memorabilia", "art_series")]
        if candidates:
            candidates.sort(key=lambda card: card.get("released_at") or "0000-00-00", reverse=True)
            return candidates[0]

    return None


def prints_for_oracle(
    oracle_id: Optional[str],
    *,
    by_oracle: Dict[str, List[Dict[str, Any]]],
) -> Tuple[Dict[str, Any], ...]:
    if not oracle_id:
        return ()
    items = by_oracle.get(oracle_id, []) or []
    if not items:
        return ()
    return tuple(sorted(items, key=lambda item: (item.get("set", ""), str(item.get("collector_number", "")))))


def candidates_by_set_and_name(
    set_code: str,
    name: str,
    *,
    cache: List[Dict[str, Any]],
    normalize_set_code_fn: Callable[[str], str],
) -> List[Dict[str, Any]]:
    normalized_set = normalize_set_code_fn(set_code)
    normalized_name = name_key(name)
    out = []
    for card in cache:
        if (card.get("set") or "").lower() != normalized_set:
            continue
        if name_key(card.get("name", "")) == normalized_name:
            out.append(card)
    return out


def unique_oracle_by_name(
    name: str,
    *,
    idx_by_name: Dict[str, List[Dict[str, Any]]],
    idx_by_front: Dict[str, List[Dict[str, Any]]],
    idx_by_back: Dict[str, List[Dict[str, Any]]],
) -> Optional[str]:
    normalized_name = name_key(name)
    if not normalized_name:
        return None

    def _variant_strings(raw: str) -> List[str]:
        variants = []

        def _add(value: str) -> None:
            value = (value or "").strip()
            if not value or value in variants:
                return
            variants.append(value)

        _add(raw)
        if "//" in raw:
            left, _, right = raw.partition("//")
            left = left.strip()
            right = right.strip()
            if left:
                _add(left)
            if right:
                _add(right)
            if left and right:
                _add(f"{left} // {right}")
                _add(f"{left}//{right}")
        if "/" in raw:
            _add(raw.split("/", 1)[0])
        if "," in raw:
            _add(raw.replace(",", ""))
        return variants

    variant_strings = _variant_strings(name)
    variant_keys = {key for key in (name_key(value) for value in variant_strings) if key}
    variant_keys.add(normalized_name)

    seen_print_ids: Set[tuple] = set()
    candidates: List[Dict[str, Any]] = []

    def _extend(items: List[Dict[str, Any]]) -> None:
        for card in items or []:
            print_id = card.get("id") or (
                card.get("oracle_id"),
                card.get("set"),
                card.get("collector_number"),
                card.get("lang"),
            )
            if not print_id or print_id in seen_print_ids:
                continue
            seen_print_ids.add(print_id)
            candidates.append(card)

    for key in variant_keys:
        _extend(idx_by_name.get(key) or [])
        _extend(idx_by_front.get(key) or [])
        _extend(idx_by_back.get(key) or [])

    if not candidates:
        return None

    def _filter(
        items: List[Dict[str, Any]],
        *,
        banned_layouts: Set[str] = frozenset(),
        banned_set_types: Set[str] = frozenset(),
        require_physical: bool = False,
    ) -> List[Dict[str, Any]]:
        filtered = []
        for card in items:
            oracle_id = card.get("oracle_id")
            if not oracle_id:
                continue
            layout = (card.get("layout") or "").lower()
            if layout in banned_layouts:
                continue
            set_type = (card.get("set_type") or "").lower()
            if set_type in banned_set_types:
                continue
            if require_physical and card.get("digital"):
                continue
            filtered.append(card)
        return filtered

    def _unique_oid(items: List[Dict[str, Any]]) -> Optional[str]:
        oracle_ids = {card.get("oracle_id") for card in items if card.get("oracle_id")}
        if len(oracle_ids) == 1:
            return next(iter(oracle_ids))
        return None

    banned_layouts = {"art_series", "token", "double_faced_token", "planar", "scheme", "emblem", "vanguard"}
    banned_set_types = {"token", "memorabilia", "art_series"}

    filter_passes = [
        _filter(candidates, banned_layouts=banned_layouts, banned_set_types=banned_set_types, require_physical=True),
        _filter(candidates, banned_layouts=banned_layouts, banned_set_types=banned_set_types),
        _filter(candidates, require_physical=True),
        candidates,
    ]

    for subset in filter_passes:
        oracle_id = _unique_oid(subset)
        if oracle_id:
            return oracle_id

    variant_key_set = {name_key(value) for value in variant_strings if name_key(value)}
    for candidate in candidates:
        oracle_id = candidate.get("oracle_id")
        if not oracle_id:
            continue
        names = [candidate.get("name") or ""]
        for face in candidate.get("card_faces") or []:
            names.append((face or {}).get("name") or "")
        name_keys = {name_key(candidate_name) for candidate_name in names if candidate_name}
        if variant_key_set & name_keys:
            return oracle_id

    return None
