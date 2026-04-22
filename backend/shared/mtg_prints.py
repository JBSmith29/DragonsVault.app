"""Shared MTG print, face, color, and token helpers."""

from __future__ import annotations

import re
import time
from collections import OrderedDict

from extensions import cache
from models import Card
from core.domains.cards.services import scryfall_cache as sc
from core.domains.cards.services.scryfall_cache import (
    find_by_set_cn,
    prints_for_oracle,
    unique_oracle_by_name,
)

WUBRG_ORDER = "WUBRG"

RE_CREATE_TOKEN = re.compile(r"\bcreate\b.*\btoken\b", flags=re.IGNORECASE | re.DOTALL)
_COMMON_TOKEN_KINDS = [
    ("treasure", "Treasure"),
    ("food", "Food"),
    ("clue", "Clue"),
    ("blood", "Blood"),
    ("map", "Map"),
    ("powerstone", "Powerstone"),
]
_MANA_SYMBOL_RE = re.compile(r"\{([^}]+)\}")

_CI_NAME_BY_SET = {
    frozenset(): "Colorless",
    frozenset(("W",)): "White",
    frozenset(("U",)): "Blue",
    frozenset(("B",)): "Black",
    frozenset(("R",)): "Red",
    frozenset(("G",)): "Green",
    frozenset(("W", "U")): "Azorius",
    frozenset(("U", "B")): "Dimir",
    frozenset(("U", "R")): "Izzet",
    frozenset(("B", "R")): "Rakdos",
    frozenset(("B", "G")): "Golgari",
    frozenset(("W", "B")): "Orzhov",
    frozenset(("R", "G")): "Gruul",
    frozenset(("W", "R")): "Boros",
    frozenset(("W", "G")): "Selesnya",
    frozenset(("U", "G")): "Simic",
    frozenset(("W", "U", "G")): "Bant",
    frozenset(("W", "U", "B")): "Esper",
    frozenset(("U", "B", "R")): "Grixis",
    frozenset(("B", "R", "G")): "Jund",
    frozenset(("W", "R", "G")): "Naya",
    frozenset(("W", "B", "G")): "Abzan",
    frozenset(("W", "U", "R")): "Jeskai",
    frozenset(("W", "B", "R")): "Mardu",
    frozenset(("U", "B", "G")): "Sultai",
    frozenset(("U", "R", "G")): "Temur",
    frozenset(("W", "U", "B", "R")): "Yore",
    frozenset(("U", "B", "R", "G")): "Glint",
    frozenset(("B", "R", "G", "W")): "Dune",
    frozenset(("R", "G", "W", "U")): "Ink",
    frozenset(("G", "W", "U", "B")): "Witch",
    frozenset(("W", "U", "B", "R", "G")): "5c",
}


def _normalize_ci(ci) -> list[str]:
    if not ci:
        return []
    if isinstance(ci, str):
        letters = [c.upper() for c in ci if c.upper() in WUBRG_ORDER]
    else:
        letters = [str(c).upper() for c in ci if str(c).upper() in WUBRG_ORDER]
    return [c for c in WUBRG_ORDER if c in set(letters)]


def color_identity_name(ci) -> str:
    letters = _normalize_ci(ci)
    key = frozenset(letters)
    return _CI_NAME_BY_SET.get(key, "".join(letters) if letters else "Colorless")


def _faces_list(faces_json) -> list[dict]:
    if not faces_json:
        return []
    if isinstance(faces_json, dict):
        faces = faces_json.get("faces") or []
    else:
        faces = faces_json
    return [face for face in faces if isinstance(face, dict)]


def _oracle_text_from_faces(faces_json) -> str | None:
    parts = [face.get("oracle_text") for face in _faces_list(faces_json) if face.get("oracle_text")]
    if not parts:
        return None
    return " // ".join(parts)


def _mana_costs_from_faces(faces_json) -> list[str]:
    costs = [face.get("mana_cost") for face in _faces_list(faces_json) if face.get("mana_cost")]
    return [cost for cost in costs if cost]


def _mana_cost_from_faces(faces_json) -> str | None:
    costs = _mana_costs_from_faces(faces_json)
    if not costs:
        return None
    return " // ".join(costs) if len(costs) > 1 else costs[0]


def _faces_image_payload(faces_json) -> list[dict]:
    faces = []
    for idx, face in enumerate(_faces_list(faces_json)):
        if not isinstance(face, dict):
            continue
        image_uris = face.get("image_uris") or {}
        if not image_uris:
            continue
        faces.append(
            {
                "small": image_uris.get("small"),
                "normal": image_uris.get("normal"),
                "large": image_uris.get("large"),
                "label": face.get("name") or ("Front" if idx == 0 else "Back"),
            }
        )
    return faces


def _type_line_from_faces(faces_json) -> str | None:
    for face in _faces_list(faces_json):
        type_line = (face.get("type_line") or "").strip()
        if type_line:
            return type_line
    return None


def _type_line_from_print(print_payload: dict | None) -> str:
    if not isinstance(print_payload, dict):
        return ""
    type_line = (print_payload.get("type_line") or "").strip()
    if type_line:
        return type_line
    return _type_line_from_faces(print_payload.get("card_faces")) or ""


def _color_letters_list(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, (list, tuple, set)):
        raw = [str(v).upper() for v in value if v]
    else:
        raw = [ch for ch in str(value).upper()]
    return [ch for ch in raw if ch in set(WUBRG_ORDER)]


def _artifact_production_colors(oracle_text: str | None) -> set[str]:
    if not oracle_text:
        return set()
    upper = oracle_text.upper()
    if "ADD" not in upper:
        return set()

    out: set[str] = set()
    for sym in _MANA_SYMBOL_RE.findall(oracle_text):
        symbol = sym.upper()
        for ch in WUBRG_ORDER:
            if ch in symbol:
                out.add(ch)

    if "ANY COLOR" in upper:
        out.update(WUBRG_ORDER)
    return out


def _effective_color_identity(type_line: str | None, oracle_text: str | None, colors: list[str]) -> list[str]:
    base = [ch for ch in WUBRG_ORDER if ch in set(colors or [])]
    if "artifact" not in (type_line or "").lower():
        return base

    produced = _artifact_production_colors(oracle_text)
    if not produced:
        return base

    return [ch for ch in WUBRG_ORDER if ch in (set(base) | produced)]


def _scryfall_card_url(set_code: str | None, collector_number: str | None) -> str | None:
    scode = (set_code or "").strip().lower()
    cn = (collector_number or "").strip()
    if not scode or not cn:
        return None
    return f"https://scryfall.com/card/{scode}/{cn}"


def _scryfall_set_url(set_code: str | None) -> str | None:
    scode = (set_code or "").strip().lower()
    if not scode:
        return None
    return f"https://scryfall.com/sets/{scode}"


def _card_type_flags(type_line: str | None) -> dict[str, object]:
    lowered = (type_line or "").lower()
    is_land = "land" in lowered
    is_creature = "creature" in lowered
    is_instant = "instant" in lowered
    is_sorcery = "sorcery" in lowered
    is_permanent = any(
        token in lowered for token in ("artifact", "enchantment", "planeswalker", "battle", "land", "creature")
    )
    if is_land:
        zone_hint = "lands"
    elif is_creature:
        zone_hint = "creatures"
    elif is_instant or is_sorcery:
        zone_hint = "graveyard"
    else:
        zone_hint = "permanents"
    return {
        "is_land": is_land,
        "is_creature": is_creature,
        "is_instant": is_instant,
        "is_sorcery": is_sorcery,
        "is_permanent": is_permanent,
        "zone_hint": zone_hint,
    }


def _token_stubs_from_oracle_text(text: str | None) -> list[dict]:
    if not text:
        return []
    lower = text.lower()
    found: list[dict] = []

    def _lookup_token(label: str) -> dict | None:
        if not label or label.lower() == "token":
            return None
        try:
            matches = sc.search_tokens(label, limit=6) or []
        except Exception:
            return None
        label_norm = label.strip().casefold()
        for match in matches:
            if (match.get("name") or "").strip().casefold() == label_norm:
                return match
        return matches[0] if matches else None

    if "token" in lower:
        for key, label in _COMMON_TOKEN_KINDS:
            if f"{key} token" in lower:
                matched = _lookup_token(label)
                found.append(
                    {
                        "id": (matched or {}).get("id"),
                        "name": (matched or {}).get("name") or label,
                        "type_line": (matched or {}).get("type_line") or f"Token - {label}",
                        "images": (matched or {}).get("images") or {"small": None, "normal": None, "large": None},
                    }
                )
    if not found and RE_CREATE_TOKEN.search(text):
        found.append(
            {
                "id": None,
                "name": "Token",
                "type_line": "Token",
                "images": {"small": None, "normal": None, "large": None},
            }
        )
    return found


def _normalize_name(s: str) -> str:
    """Normalize a card name for deduping/comparison."""
    s = (s or "").strip()
    s = s.replace("’", "'").strip('"').strip("'")
    s = re.sub(r"\s+", " ", s)
    return s.lower()


_NO_VALUE = object()


class _LRUCache:
    """Simple ordered-dict LRU so repeated Scryfall lookups stay in-process."""

    def __init__(self, maxsize: int = 1024):
        self.maxsize = maxsize
        self._data: OrderedDict[tuple, object] = OrderedDict()

    def get(self, key: tuple) -> object:
        value = self._data.get(key, _NO_VALUE)
        if value is not _NO_VALUE:
            self._data.move_to_end(key)
        return value

    def set(self, key: tuple, value: object) -> None:
        self._data[key] = value
        self._data.move_to_end(key)
        if len(self._data) > self.maxsize:
            self._data.popitem(last=False)


_PRINT_CACHE_BY_SET_CN = _LRUCache(maxsize=4096)
_PRINT_CACHE_BY_ORACLE = _LRUCache(maxsize=1024)
_FAILED_ORACLE_CACHE: dict[str, float] = {}
_FAILED_ORACLE_TTL = 300.0


def _failed_lookup_key(name: str | None) -> str:
    return (name or "").strip().casefold()


def _failed_lookup_recent(name: str | None) -> bool:
    key = _failed_lookup_key(name)
    if not key:
        return False
    ts = _FAILED_ORACLE_CACHE.get(key)
    if ts is None:
        return False
    if time.time() - ts > _FAILED_ORACLE_TTL:
        _FAILED_ORACLE_CACHE.pop(key, None)
        return False
    return True


def _mark_failed_lookup(name: str | None) -> None:
    key = _failed_lookup_key(name)
    if not key:
        return
    now = time.time()
    _FAILED_ORACLE_CACHE[key] = now
    if len(_FAILED_ORACLE_CACHE) > 2048:
        cutoff = now - _FAILED_ORACLE_TTL
        for failed_key, ts in list(_FAILED_ORACLE_CACHE.items()):
            if ts < cutoff:
                _FAILED_ORACLE_CACHE.pop(failed_key, None)
        if len(_FAILED_ORACLE_CACHE) > 2048:
            excess = len(_FAILED_ORACLE_CACHE) - 2048
            for failed_key in list(_FAILED_ORACLE_CACHE.keys())[:excess]:
                _FAILED_ORACLE_CACHE.pop(failed_key, None)


def _clear_failed_lookup(name: str | None) -> None:
    key = _failed_lookup_key(name)
    if key:
        _FAILED_ORACLE_CACHE.pop(key, None)


def _setcn_key(set_code, collector_number) -> tuple | None:
    sc_code = (set_code or "").strip().lower()
    if collector_number is None:
        cn_code = ""
    else:
        cn_code = str(collector_number).strip().lower()
    if not sc_code and not cn_code:
        return None
    return sc_code, cn_code


def _cached_oracle_bundle(oracle_id: str | None) -> tuple[dict[tuple, dict], dict] | None:
    if not oracle_id:
        return None
    cache_key = (str(oracle_id),)
    cached = _PRINT_CACHE_BY_ORACLE.get(cache_key)
    if cached is not _NO_VALUE:
        return cached  # type: ignore[return-value]
    try:
        prints = prints_for_oracle(oracle_id) or []
    except Exception:
        prints = []
    if not prints:
        return None
    index: dict[tuple, dict] = {}
    fallback = None
    for pr in prints:
        fallback = fallback or pr
        key = _setcn_key(pr.get("set"), pr.get("collector_number"))
        if key and key not in index:
            index[key] = pr
    bundle = (index, fallback or {})
    _PRINT_CACHE_BY_ORACLE.set(cache_key, bundle)
    return bundle


def _lookup_print_data(set_code, collector_number, name, oracle_id) -> dict:
    """Resolve a Scryfall print, preferring cached entries when possible."""
    key = _setcn_key(set_code, collector_number)
    if key:
        cached = _PRINT_CACHE_BY_SET_CN.get(key)
        if cached is not _NO_VALUE:
            return cached  # type: ignore[return-value]

    pr = {}
    resolved_oid = oracle_id

    bundle = _cached_oracle_bundle(oracle_id)
    if bundle:
        index, fallback = bundle
        if key:
            pr = index.get(key, {}) or {}
        pr = pr or fallback or {}

    if not pr and name and not resolved_oid:
        if not _failed_lookup_recent(name):
            try:
                resolved_oid = unique_oracle_by_name(name)
            except Exception:
                resolved_oid = None
            if resolved_oid:
                _clear_failed_lookup(name)
            else:
                _mark_failed_lookup(name)

    if not pr:
        try:
            pr = find_by_set_cn(set_code, collector_number, name) or {}
        except Exception:
            pr = {}

    if not pr and resolved_oid:
        try:
            alts = prints_for_oracle(resolved_oid) or []
        except Exception:
            alts = []
        if alts:
            target_set = (set_code or "").strip().lower()
            match = next(
                (p for p in alts if (p.get("set") or "").strip().lower() == target_set and target_set),
                None,
            )
            pr = match or alts[0] or {}
        if pr:
            _clear_failed_lookup(name)

    if key and pr:
        _PRINT_CACHE_BY_SET_CN.set(key, pr)

    return pr or {}


def _cards_fingerprint(cards: list[Card]) -> str:
    """Build a deterministic signature for a set of cards."""
    parts: list[str] = []
    for card in sorted(cards, key=lambda c: getattr(c, "id", 0) or 0):
        parts.append(
            "|".join(
                [
                    str(getattr(card, "id", "")),
                    str(getattr(card, "set_code", "")).lower(),
                    str(getattr(card, "collector_number", "")).lower(),
                    str(getattr(card, "oracle_id", "")).lower(),
                    str(getattr(card, "lang", "")).lower(),
                    "1" if getattr(card, "is_foil", False) else "0",
                ]
            )
        )
    return ";".join(parts)


def _bulk_print_lookup(cards: list[Card], *, cache_key: str | None = None, epoch: int | None = None) -> dict[int, dict]:
    """Resolve Scryfall print metadata for all cards, reusing cache entries."""
    use_cache = bool(cache_key and cache)
    cached_key = None
    if use_cache:
        cached_key = f"prints:{cache_key}:{epoch or ''}:{_cards_fingerprint(cards)}"
        cached = cache.get(cached_key)
        if isinstance(cached, dict):
            return cached

    out: dict[int, dict] = {}
    for card in cards:
        out[card.id] = _lookup_print_data(
            getattr(card, "set_code", None),
            getattr(card, "collector_number", None),
            getattr(card, "name", None),
            getattr(card, "oracle_id", None),
        )
    if use_cache and cached_key:
        try:
            cache.set(cached_key, out, timeout=600)
        except Exception:
            pass
    return out


def _small_thumb_for_print(pr: dict | None) -> str | None:
    """Return a stable small image URL for any print, including double-faced cards."""
    if not pr:
        return None
    iu = (pr or {}).get("image_uris") or {}
    if iu.get("small"):
        return iu["small"]
    faces = (pr or {}).get("card_faces") or []
    for face in faces:
        face_uris = (face or {}).get("image_uris") or {}
        if face_uris.get("small"):
            return face_uris["small"]
    return None


def _img_url_for_print(pr, size="normal"):
    if not pr:
        return None
    iu = pr.get("image_uris")
    if iu:
        return iu.get(size) or iu.get("large") or iu.get("png")
    faces = pr.get("card_faces") or []
    for face in faces:
        face_uris = face.get("image_uris") or {}
        url = face_uris.get(size) or face_uris.get("large") or face_uris.get("png")
        if url:
            return url
    return None


def _unique_art_variants(prints):
    """Unique art by illustration_id/id while preserving first-seen order."""
    seen = set()
    out = []
    for print_payload in prints or []:
        illustration_id = print_payload.get("illustration_id") or print_payload.get("id")
        if illustration_id and illustration_id not in seen:
            seen.add(illustration_id)
            out.append(print_payload)
    return out
