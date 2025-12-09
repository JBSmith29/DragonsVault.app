# services/scryfall_cache.py
from __future__ import annotations

import gzip
import os
import time
import math
import random
import re
from pathlib import Path
from collections import Counter
from typing import Optional, Dict, Any, List, Tuple, Set, Iterable, Callable
import json
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from flask import current_app
from functools import lru_cache
from math import inf

# -----------------------------------------------------------------------------
# In-memory flags/state
# -----------------------------------------------------------------------------
_cache_loaded = False
_cache_epoch = 0

def _bump_cache_epoch() -> None:
    global _cache_epoch
    _cache_epoch += 1

def cache_epoch() -> int:
    return _cache_epoch

# default_cards cache + indexes
_cache: List[Dict[str, Any]] = []
_by_set_cn: Dict[str, Dict[str, Any]] = {}
_by_oracle: Dict[str, List[Dict[str, Any]]] = {}
_set_names: Optional[Dict[str, str]] = None  # lazy-built from _cache
_set_releases: Optional[Dict[str, str]] = None

# Tolerant indexes (for meld/adventure/DFC and CN variants)
_idx_by_set_num: Dict[Tuple[str, int], List[Dict[str, Any]]] = {}
_idx_by_name: Dict[str, List[Dict[str, Any]]] = {}
_idx_by_front: Dict[str, List[Dict[str, Any]]] = {}
_idx_by_back: Dict[str, List[Dict[str, Any]]] = {}
_set_profiles: Optional[Dict[str, Dict[str, Any]]] = None

# rulings bulk (indexed by oracle_id)
_rulings_by_oracle: Dict[str, List[Dict[str, Any]]] = {}
_rulings_loaded_path: Optional[str] = None

# HTTP/session config
SCRYFALL_UA = os.getenv("SCRYFALL_UA", "DragonsVault/6 (+https://dragonsvault.app)")
_SCRYFALL_RETRY_TOTAL = int(os.getenv("SCRYFALL_HTTP_RETRIES", "5"))
_SCRYFALL_RETRY_BACKOFF = float(os.getenv("SCRYFALL_HTTP_BACKOFF", "0.5"))
_status_raw = os.getenv("SCRYFALL_HTTP_STATUS_FORCELIST", "429,500,502,503,504")
_status_codes: List[int] = []
for _code in _status_raw.split(","):
    token = _code.strip()
    if not token:
        continue
    try:
        _status_codes.append(int(token))
    except ValueError:
        continue
if not _status_codes:
    _status_codes = [429, 500, 502, 503, 504]
_SCRYFALL_STATUS_FORCELIST = tuple(_status_codes)
_session: Optional[requests.Session] = None


def _scryfall_session() -> requests.Session:
    """Return a shared requests Session with UA + retry config."""
    global _session
    if _session is not None:
        return _session

    retry_kwargs = {
        "total": _SCRYFALL_RETRY_TOTAL,
        "backoff_factor": _SCRYFALL_RETRY_BACKOFF,
        "status_forcelist": _SCRYFALL_STATUS_FORCELIST,
        "raise_on_status": False,
    }
    methods = frozenset(["GET", "HEAD", "OPTIONS"])
    try:
        retries = Retry(allowed_methods=methods, **retry_kwargs)
    except TypeError:
        # Older urllib3 versions only support method_whitelist
        retries = Retry(method_whitelist=methods, **retry_kwargs)
    adapter = HTTPAdapter(max_retries=retries)
    sess = requests.Session()
    sess.mount("https://", adapter)
    sess.mount("http://", adapter)
    sess.headers.update({
        "User-Agent": SCRYFALL_UA,
        "Accept": "application/json",
    })
    _session = sess
    return _session

# -----------------------------------------------------------------------------
# Public entry: make sure cache is in-memory
# -----------------------------------------------------------------------------
def ensure_cache_loaded(path: str | None = None, force: bool = False) -> bool:
    """
    Warm in-memory Scryfall 'default_cards' cache (prints) and indexes if not loaded.
    If `force=True`, clears the in-memory copy and reloads from disk.
    """
    global _cache_loaded
    if force:
        _cache_loaded = False
        # clear in-memory structures
        try:
            _clear_in_memory_prints()
        except Exception:
            pass

    if _cache_loaded and _cache:
        return True

    ok = load_default_cache(path)
    _cache_loaded = bool(ok and _cache)
    return _cache_loaded


def cache_ready() -> bool:
    """Fast check: is the in-memory default_cards cache already available?"""
    return bool(_cache_loaded and _cache)

# -----------------------------------------------------------------------------
# Paths & helpers
# -----------------------------------------------------------------------------
def _guess_instance_data_root() -> Path:
    """Best-effort path when no Flask app context is active."""
    env = os.getenv("SCRYFALL_DATA_DIR")
    if env:
        return Path(env)
    return Path("instance") / "data"

def _data_root() -> Path:
    """
    Preferred data root order:
      1) current_app.config["SCRYFALL_DATA_DIR"]
      2) current_app.instance_path / "data"
      3) env SCRYFALL_DATA_DIR
      4) ./instance/data
    """
    try:
        base = current_app.config.get("SCRYFALL_DATA_DIR")
        if not base:
            base = Path(current_app.instance_path) / "data"
        return Path(base)
    except Exception:
        return _guess_instance_data_root()

def default_cards_path(path: Optional[str] = None) -> str:
    return path or str(_data_root() / "scryfall_default_cards.json")

def rulings_bulk_path(path: Optional[str] = None) -> str:
    return path or str(_data_root() / "scryfall_rulings.json")

# Legacy constants (kept for callers that read a module attribute)
DEFAULT_CARDS_PATH = default_cards_path()
RULINGS_BULK_PATH = rulings_bulk_path()
DEFAULT_PATH = DEFAULT_CARDS_PATH  # legacy alias used by CLI
DEFAULT_MAX_AGE = 7 * 24 * 3600  # 7 days
RULINGS_MAX_AGE = 7 * 24 * 3600  # 7 days
BULK_META_URL = "https://api.scryfall.com/bulk-data"

# -----------------------------------------------------------------------------
# Utilities
# -----------------------------------------------------------------------------
def _key_set_cn(set_code: str, cn: str) -> str:
    return f"{(set_code or '').lower()}::{str(cn).strip().lower()}"

def _human_bytes(n: int) -> str:
    if not n:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = min(int(math.log(max(n, 1), 1024)), len(units) - 1)
    return f"{n / (1024 ** i):.1f} {units[i]}"

def _image_uris(card_obj: Dict[str, Any]) -> Dict[str, Optional[str]]:
    iu = card_obj.get("image_uris")
    if iu:
        return {"small": iu.get("small"), "normal": iu.get("normal"), "large": iu.get("large")}
    faces = card_obj.get("card_faces") or []
    if faces and isinstance(faces, list):
        iu = (faces[0] or {}).get("image_uris") or {}
        return {"small": iu.get("small"), "normal": iu.get("normal"), "large": iu.get("large")}
    return {"small": None, "normal": None, "large": None}

def _cn_variants(cn: str) -> List[str]:
    """
    Generate plausible variants of a collector number to handle:
      - leading zeros ("001" -> "1")
      - letter suffixes ("256a" -> "256a", "256")
      - non-digit chars (strip everything but digits as a last resort)
    """
    s = str(cn or "").strip().lower()
    out = [s]
    s_lstrip = s.lstrip("0") or "0"
    if s_lstrip not in out:
        out.append(s_lstrip)
    m = re.match(r"^(\d+)[a-z]?$", s)
    if m:
        core = m.group(1)
        if core not in out:
            out.append(core)
    digits = "".join(ch for ch in s if ch.isdigit())
    if digits and digits not in out:
        out.append(digits)
    return out

def _cn_num(cn: str) -> Optional[int]:
    m = re.match(r"(\d+)", str(cn or ""))
    return int(m.group(1)) if m else None


_COLOR_BIT_MAP = {"W": 1, "U": 2, "B": 4, "R": 8, "G": 16}


def normalize_color_identity(colors: Optional[Iterable[str]]) -> Tuple[str, int]:
    """Return (sorted letters string, bitmask) from an iterable of color letters."""
    letters = sorted({str(c or "").strip().upper() for c in (colors or []) if c})
    mask = 0
    for letter in letters:
        mask |= _COLOR_BIT_MAP.get(letter, 0)
    return "".join(letters), mask


def metadata_from_print(print_data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Extract cached metadata the Card model stores from a Scryfall print payload."""
    if not print_data:
        return {
            "type_line": None,
            "rarity": None,
            "color_identity": None,
            "color_identity_mask": None,
        }

    type_line_raw = (print_data.get("type_line") or "").strip()
    type_line = type_line_raw or None

    rarity_raw = (print_data.get("rarity") or "").strip().lower()
    rarity = rarity_raw or None

    colors = print_data.get("color_identity")
    if not colors:
        colors = print_data.get("colors")
    identity_letters, mask = normalize_color_identity(colors or [])

    return {
        "type_line": type_line,
        "rarity": rarity,
        "color_identity": identity_letters or None,
        "color_identity_mask": mask or None,
    }


def _collector_sort_key(value: Optional[str]) -> Tuple[int, str]:
    raw = str(value or "")
    digits = "".join(ch for ch in raw if ch.isdigit())
    number = int(digits) if digits else inf
    return (0 if digits else 1, number, raw)


def search_local_cards(
    *,
    name: str = "",
    set_code: str = "",
    base_types: Iterable[str] = (),
    typal: str = "",
    colors: Iterable[str] = (),
    color_mode: str = "contains",
    commander_only: bool = False,
    order: str = "name",
    direction: str = "asc",
    page: int = 1,
    per: int = 60,
) -> Optional[Dict[str, Any]]:
    if not ensure_cache_loaded():
        return None

    name = (name or "").strip().lower()
    set_code = (set_code or "").strip().lower()
    typal = (typal or "").strip().lower()
    color_mode = color_mode or "contains"
    colors = [c.upper() for c in colors if c]

    def matches(card):
        if name and name not in (card.get("name") or "").lower():
            return False
        if set_code and (card.get("set") or "").lower() != set_code:
            return False
        tline = (card.get("type_line") or "").lower()
        for base in base_types or []:
            if base.lower() not in tline:
                return False
        if typal and typal not in tline:
            return False
        ci = card.get("color_identity") or []
        ci_set = set(str(c or "").upper() for c in ci)
        color_set = set(colors)
        if color_set:
            if color_mode == "exact":
                if ci_set != color_set:
                    return False
            else:
                if not color_set.issubset(ci_set):
                    return False
        if commander_only:
            legality = ((card.get("legalities") or {}).get("commander") or "").lower()
            if legality != "legal":
                return False
        return True

    filtered = [card for card in _cache if matches(card)]

    reverse = direction == "desc"

    def sort_key(card):
        if order == "cmc":
            return (card.get("cmc") or 0, card.get("name") or "")
        if order == "rarity":
            return ((card.get("rarity") or "").lower(), card.get("name") or "")
        if order == "set":
            return ((card.get("set") or "").lower(), _collector_sort_key(card.get("collector_number")))
        if order in {"collector", "cn"}:
            return _collector_sort_key(card.get("collector_number"))
        return ((card.get("name") or "").lower(),)

    filtered.sort(key=sort_key, reverse=reverse)

    total = len(filtered)
    start = max(0, (page - 1) * per)
    sliced = filtered[start : start + per]

    return {
        "data": sliced,
        "total_cards": total,
        "has_more": start + per < total,
    }

def _name_key(name: str) -> str:
    """Case/punct-insensitive key for name comparisons."""
    s = (name or "").casefold()
    try:
        s = re.sub(r"[\s\p{P}]+", "", s)
    except Exception:
        s = re.sub(r"[\W_]+", "", s)
    return s

def _front_face_name(card_obj: Dict[str, Any]) -> str:
    faces = card_obj.get("card_faces") or []
    if faces and isinstance(faces, list) and faces:
        return (faces[0] or {}).get("name", "") or ""
    return card_obj.get("name", "") or ""


def _back_face_names(card_obj: Dict[str, Any]) -> List[str]:
    faces = card_obj.get("card_faces") or []
    if not faces or not isinstance(faces, list) or len(faces) <= 1:
        return []
    names: List[str] = []
    for face in faces[1:]:
        label = (face or {}).get("name", "") or ""
        if label:
            names.append(label)
    return names

def display_name_for_print(pr: Dict[str, Any]) -> str:
    """
    Prefer face names; de-duplicate 'X // X' cases.
    """
    faces = pr.get("card_faces") or []
    if faces and isinstance(faces, list):
        names = [(f or {}).get("name", "").strip() for f in faces if f]
        names = [n for n in names if n]
        if not names:
            return pr.get("name") or ""
        if len(names) >= 2 and names[0].casefold() == names[-1].casefold():
            return names[0]
        return " // ".join(names)
    return pr.get("name") or ""

def type_label_for_print(pr: Dict[str, Any]) -> str:
    """
    Combine face type lines when present (DFC / Adventure):
      e.g., 'Creature — Dragon // Sorcery — Adventure'
    Falls back to top-level 'type_line'.
    """
    faces = pr.get("card_faces") or []
    if faces and isinstance(faces, list):
        tls: List[str] = []
        seen = set()
        for f in faces:
            t = ((f or {}).get("type_line") or "").strip()
            if not t:
                continue
            k = t.casefold()
            if k in seen:
                continue
            seen.add(k)
            tls.append(t)
        if tls:
            if len(tls) >= 2 and tls[0].casefold() == tls[-1].casefold():
                return tls[0]
            return " // ".join(tls)
    return (pr.get("type_line") or "").strip()

def _clear_in_memory_prints():
    global _cache, _by_set_cn, _by_oracle, _set_names
    global _idx_by_set_num, _idx_by_name, _idx_by_front, _idx_by_back
    global _set_profiles
    _cache = []
    _by_set_cn = {}
    _by_oracle = {}
    _set_names = None
    _idx_by_set_num = {}
    _idx_by_name = {}
    _idx_by_front = {}
    _idx_by_back = {}
    _set_profiles = None
    _bump_cache_epoch()
    try:
        prints_for_oracle.cache_clear()
    except Exception:
        pass
    try:
        unique_oracle_by_name.cache_clear()
    except Exception:
        pass

# -----------------------------------------------------------------------------
# Default cards bulk (prints) — load & lookups
# -----------------------------------------------------------------------------
def default_cache_exists(path: Optional[str] = None) -> bool:
    return os.path.exists(default_cards_path(path))

def default_is_stale(path: Optional[str] = None, max_age: int = DEFAULT_MAX_AGE) -> bool:
    p = default_cards_path(path)
    if not os.path.exists(p):
        return True
    return (time.time() - os.path.getmtime(p)) > max_age

def _prime_default_indexes() -> None:
    global _by_set_cn, _by_oracle, _set_names
    global _idx_by_set_num, _idx_by_name, _idx_by_front, _idx_by_back
    _by_set_cn.clear()
    _by_oracle.clear()
    _set_names = None
    _idx_by_set_num.clear()
    _idx_by_name.clear()
    _idx_by_front.clear()
    _idx_by_back.clear()

    for c in _cache:
        scode = (c.get("set") or "").lower()
        cn = str(c.get("collector_number") or "")
        if scode and cn:
            _by_set_cn[_key_set_cn(scode, cn)] = c
            n = _cn_num(cn)
            if n is not None:
                _idx_by_set_num.setdefault((scode, n), []).append(c)

        oid = c.get("oracle_id")
        if oid:
            _by_oracle.setdefault(oid, []).append(c)

        nk_full = _name_key(c.get("name", ""))
        if nk_full:
            _idx_by_name.setdefault(nk_full, []).append(c)

        nk_front = _name_key(_front_face_name(c))
        if nk_front:
            _idx_by_front.setdefault(nk_front, []).append(c)
        for back_name in _back_face_names(c):
            nk_back = _name_key(back_name)
            if nk_back:
                _idx_by_back.setdefault(nk_back, []).append(c)
    _bump_cache_epoch()
    try:
        prints_for_oracle.cache_clear()
    except Exception:
        pass
    try:
        unique_oracle_by_name.cache_clear()
    except Exception:
        pass

def load_default_cache(path: Optional[str] = None) -> bool:
    """Load default_cards JSON (prints) into memory and index it."""
    global _cache
    p = default_cards_path(path)
    if not os.path.exists(p):
        return False
    with open(p, "r", encoding="utf-8") as f:
        _cache = json.load(f) or []
    _prime_default_indexes()
    return True

def reload_default_cache(path: Optional[str] = None) -> bool:
    _clear_in_memory_prints()
    return load_default_cache(path)

def find_by_set_cn(set_code: str, collector_number: str, name_hint: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Tolerant match for a Scryfall print:
      1) strict set+collector_number
      2) loose CN variants within set (handles 001/1, 123a/123, etc.)
      3) name within set (full), then front-face name within set (Adventure/DFC)
      4) last resort: name-only across sets (prefer newest non-token/non-ARS)
    """
    if not set_code or not collector_number:
        return None
    sc = (set_code or "").lower()
    cn = str(collector_number or "").strip()

    # 1) strict
    item = _by_set_cn.get(_key_set_cn(sc, cn))
    if item:
        return item

    # 2) loose variants inside set
    cand = find_by_set_cn_loose(sc, cn, name_hint=name_hint)
    if cand:
        return cand

    nk = _name_key(name_hint or "")
    # 3a) name within same set
    if nk:
        cands = [c for c in _idx_by_name.get(nk, []) if (c.get("set") or "").lower() == sc]
        if len(cands) == 1:
            return cands[0]
        if cands:
            n = _cn_num(cn)
            if n is not None:
                same_num = [c for c in cands if _cn_num(c.get("collector_number")) == n]
                if len(same_num) == 1:
                    return same_num[0]
            return cands[0]

    # 3b) front-face name (Adventure/DFC) within set
    if nk:
        cands = [c for c in _idx_by_front.get(nk, []) if (c.get("set") or "").lower() == sc]
        if len(cands) == 1:
            return cands[0]
        if cands:
            n = _cn_num(cn)
            if n is not None:
                same_num = [c for c in cands if _cn_num(c.get("collector_number")) == n]
                if len(same_num) == 1:
                    return same_num[0]
            return cands[0]

    # 4) name only (any set), prefer newest and skip tokens/art series
    if nk:
        cands = (_idx_by_name.get(nk) or []) + (_idx_by_front.get(nk) or [])
        cands = [c for c in cands if not (c.get("set_type") in ("token", "memorabilia", "art_series"))]
        if cands:
            cands.sort(key=lambda c: c.get("released_at") or "0000-00-00", reverse=True)
            return cands[0]

    return None

def find_by_set_cn_loose(set_code: str, collector_number: str, name_hint: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Loose match: try CN variants (strip zeros/letters) within the given set.
    Uses name_hint only as a weak tie-breaker if multiple candidates found.
    """
    sc = (set_code or "").lower()
    if not sc or not collector_number:
        return None
    candidates: List[Dict[str, Any]] = []
    for v in _cn_variants(str(collector_number)):
        item = _by_set_cn.get(f"{sc}::{v}")
        if item:
            candidates.append(item)
    if not candidates:
        # Also try: same set + same numeric CN
        n = _cn_num(collector_number)
        if n is not None:
            candidates = list(_idx_by_set_num.get((sc, n), []) or [])
    if not candidates:
        return None
    if len(candidates) == 1 or not name_hint:
        return candidates[0]
    nk = _name_key(name_hint)
    for c in candidates:
        if _name_key(c.get("name", "")) == nk:
            return c
    ff_nk = _name_key(name_hint.split("//", 1)[0].strip()) if "//" in (name_hint or "") else nk
    for c in candidates:
        if _name_key(_front_face_name(c)) == ff_nk:
            return c
    return candidates[0]


def fetch_live_print(set_code: str, collector_number: str, name_hint: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Fetch a single print directly from Scryfall when it's missing from the local cache.
    Attempts a direct /cards/{set}/{cn} lookup, then a set-scoped name search.
    """
    scode = (set_code or "").strip().lower()
    cn = str(collector_number or "").strip()
    if not scode or not cn:
        return None
    sess = _http_session()
    try:
        resp = sess.get(f"https://api.scryfall.com/cards/{scode}/{cn}", timeout=8)
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass

    if name_hint:
        try:
            resp = sess.get(
                "https://api.scryfall.com/cards/search",
                params={"q": f'!"{name_hint}" set:{scode} cn:{cn}'},
                timeout=8,
            )
            if resp.status_code == 200:
                data = resp.json()
                items = data.get("data") or []
                if items:
                    return items[0]
        except Exception:
            pass
    return None


@lru_cache(maxsize=32768)
def prints_for_oracle(oracle_id: Optional[str]) -> Tuple[Dict[str, Any], ...]:
    if not oracle_id:
        return ()
    items = _by_oracle.get(oracle_id, []) or []
    if not items:
        return ()
    sorted_items = sorted(items, key=lambda x: (x.get("set", ""), str(x.get("collector_number", ""))))
    return tuple(sorted_items)

def set_name_for_code(code: str) -> Optional[str]:
    global _set_names
    if not code:
        return None
    if _set_names is None:
        m: Dict[str, str] = {}
        for c in _cache:
            scode = c.get("set")
            sn = c.get("set_name")
            if scode and sn and scode not in m:
                m[scode] = sn
        _set_names = m
    return _set_names.get(code.lower())

def set_release_for_code(code: str) -> Optional[str]:
    """
    Return the earliest printed release date for a set, based on default_cards data.
    """
    global _set_releases
    if not code:
        return None
    if _set_releases is None:
        releases: Dict[str, str] = {}
        for c in _cache:
            scode = (c.get("set") or "").lower()
            released = c.get("released_at")
            if not scode or not released:
                continue
            if scode not in releases or released < releases[scode]:
                releases[scode] = released
        _set_releases = releases
    return _set_releases.get(code.lower())

def all_set_codes() -> List[str]:
    codes = set()
    for c in _cache:
        scode = c.get("set")
        if scode:
            codes.add(scode.lower())
    return sorted(codes)

def _build_set_profiles() -> Dict[str, Dict[str, Any]]:
    global _set_profiles
    if _set_profiles is not None:
        return _set_profiles

    ensure_cache_loaded()
    profiles: Dict[str, Dict[str, Any]] = {}
    skip_layouts = {"token", "double_faced_token", "art_series", "emblem", "vanguard", "scheme", "plane", "planar"}
    wbgr_order = "WUBRG"

    for card in _cache:
        scode = (card.get("set") or "").lower()
        if not scode:
            continue
        layout = (card.get("layout") or "").lower()
        if layout in skip_layouts:
            continue
        type_line_raw = card.get("type_line") or ""
        type_line = type_line_raw.lower()
        if "token" in type_line or "emblem" in type_line:
            continue
        if "land" in type_line:
            continue

        profile = profiles.setdefault(
            scode,
            {
                "color_counts": Counter({c: 0 for c in wbgr_order}),
                "nonland_spells": 0,
                "mv_total": 0.0,
                "mv_samples": 0,
                "mono_cards": 0,
                "multicolor_cards": 0,
                "colorless_cards": 0,
            },
        )

        profile["nonland_spells"] += 1

        mv = card.get("cmc")
        if mv is None:
            mv = card.get("mana_value")
        try:
            mv_value = float(mv)
        except (TypeError, ValueError):
            mv_value = None
        if mv_value is not None:
            profile["mv_total"] += mv_value
            profile["mv_samples"] += 1

        raw_identity = card.get("color_identity") or card.get("colors") or []
        identity = sorted({str(sym).upper() for sym in raw_identity if sym})
        if not identity:
            profile["colorless_cards"] += 1
        elif len(identity) == 1:
            profile["mono_cards"] += 1
        else:
            profile["multicolor_cards"] += 1
        for sym in identity:
            if sym in wbgr_order:
                profile["color_counts"][sym] += 1

    finalized: Dict[str, Dict[str, Any]] = {}
    for scode, profile in profiles.items():
        color_counts: Counter = profile["color_counts"]
        palette = [
            (color, color_counts.get(color, 0))
            for color in wbgr_order
        ]
        palette.sort(key=lambda item: (-item[1], wbgr_order.index(item[0])))
        dominant_colors = [color for color, count in palette if count][:3]
        color_presence = {color for color, count in palette if count}

        if profile["mv_samples"]:
            avg_mv = round(profile["mv_total"] / profile["mv_samples"], 2)
        else:
            avg_mv = None

        if avg_mv is None:
            curve_bucket = None
        elif avg_mv <= 3.0:
            curve_bucket = "low"
        elif avg_mv <= 4.5:
            curve_bucket = "mid"
        else:
            curve_bucket = "high"

        if not color_presence:
            color_mode = "colorless"
        elif profile["multicolor_cards"] > 0:
            color_mode = "multi"
        elif len(color_presence) == 1:
            color_mode = "mono"
        else:
            color_mode = "mixed"

        finalized[scode] = {
            "avg_mv": avg_mv,
            "curve_bucket": curve_bucket,
            "dominant_colors": dominant_colors,
            "color_presence": list(color_presence),
            "color_mode": color_mode,
            "nonland_spells": profile["nonland_spells"],
            "mono_cards": profile["mono_cards"],
            "multicolor_cards": profile["multicolor_cards"],
            "colorless_cards": profile["colorless_cards"],
            "color_counts": {color: color_counts.get(color, 0) for color in wbgr_order},
        }

    _set_profiles = finalized
    return _set_profiles

def set_profiles(set_codes: Optional[Iterable[str]] = None) -> Dict[str, Dict[str, Any]]:
    profiles = _build_set_profiles()
    if set_codes is None:
        return dict(profiles)
    subset: Dict[str, Dict[str, Any]] = {}
    for code in set_codes:
        if not code:
            continue
        subset[code.lower()] = profiles.get(code.lower(), {})
    return subset

def set_image_samples(set_code: str, *, per_set: int = 6) -> List[Dict[str, Any]]:
    sc = (set_code or "").lower()
    if not sc:
        return []
    candidates: List[Dict[str, Any]] = []
    for c in _cache:
        if (c.get("set") or "").lower() == sc:
            iu = _image_uris(c)
            if iu.get("small") or iu.get("normal") or iu.get("large"):
                candidates.append({
                    "small": iu.get("small"),
                    "normal": iu.get("normal"),
                    "large": iu.get("large"),
                    "name": c.get("name"),
                    "collector_number": c.get("collector_number"),
                    "lang": c.get("lang"),
                    "rarity": c.get("rarity"),
                })
    if not candidates:
        return []
    if len(candidates) > per_set:
        return random.sample(candidates, k=per_set)
    return candidates

def image_for_print(print_obj: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """Return image URIs for a single print (small/normal/large) plus a label."""
    uris = _image_uris(print_obj)
    cn = print_obj.get("collector_number")
    scode = (print_obj.get("set") or "").upper()
    uris["label"] = f"{scode} #{cn}" if scode or cn else ""
    return uris

def resolve_print_bundle(set_code: str, collector_number: str, name_hint: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Return a small bundle for this exact print if found:
      { "print": pr, "display_name": str, "type_label": str, "image": {small,normal,label} }
    """
    pr = find_by_set_cn(set_code, collector_number, name_hint)
    if not pr:
        return None
    return {
        "print": pr,
        "display_name": display_name_for_print(pr),
        "type_label": type_label_for_print(pr),
        "image": image_for_print(pr),
    }

# -----------------------------------------------------------------------------
# Bulk rulings — single local file indexed by oracle_id (no per-card HTTP)
# -----------------------------------------------------------------------------
def rulings_bulk_exists(path: Optional[str] = None) -> bool:
    return os.path.exists(rulings_bulk_path(path))

def rulings_is_stale(path: Optional[str] = None, max_age: int = RULINGS_MAX_AGE) -> bool:
    p = rulings_bulk_path(path)
    if not os.path.exists(p):
        return True
    return (time.time() - os.path.getmtime(p)) > max_age

def load_rulings_bulk(path: Optional[str] = None) -> int:
    """
    Load rulings JSON (list of objects) and index by oracle_id.
    Returns number of rulings loaded.
    """
    global _rulings_by_oracle, _rulings_loaded_path
    p = rulings_bulk_path(path)
    if not os.path.exists(p):
        _rulings_by_oracle = {}
        _rulings_loaded_path = None
        return 0
    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f) or []
    idx: Dict[str, List[Dict[str, Any]]] = {}
    for r in data:
        oid_raw = r.get("oracle_id")
        if not oid_raw:
            continue
        oid = str(oid_raw).lower()
        idx.setdefault(oid, []).append({
            "published_at": r.get("published_at"),
            "source": r.get("source"),
            "comment": r.get("comment"),
        })
    for _, rs in idx.items():
        rs.sort(key=lambda x: (x.get("published_at") or ""))
    _rulings_by_oracle = idx
    _rulings_loaded_path = p
    return sum(len(v) for v in idx.values())

def rulings_for_oracle(oracle_id: str) -> List[Dict[str, Any]]:
    if not oracle_id:
        return []
    if not _rulings_by_oracle:
        if rulings_bulk_exists():
            load_rulings_bulk()
    key = str(oracle_id).lower()
    return _rulings_by_oracle.get(key, []) or []

# -----------------------------------------------------------------------------
# Download helpers (prints & rulings)
# -----------------------------------------------------------------------------
def fetch_bulk_index() -> List[Dict[str, Any]]:
    """Fetch Scryfall bulk index metadata."""
    session = _scryfall_session()
    r = session.get(BULK_META_URL, timeout=60)
    r.raise_for_status()
    payload = r.json() or {}
    return payload.get("data", [])


def get_bulk_metadata(kind: str) -> Optional[Dict[str, Any]]:
    """Return the metadata block for a given Scryfall bulk dataset."""
    try:
        for item in fetch_bulk_index():
            if item.get("type") == kind:
                return item
    except Exception:
        return None
    return None


def get_bulk_download_uri(kind: str) -> Optional[str]:
    entry = get_bulk_metadata(kind)
    return entry.get("download_uri") if entry else None


def get_default_cards_download_uri() -> Optional[str]:
    return get_bulk_download_uri("default_cards")


def _etag_file_for(path: Path, override: Optional[str] = None) -> Path:
    if override:
        return Path(override)
    return path.with_suffix(path.suffix + ".etag")


def stream_download_to(
    path: str,
    url: str,
    *,
    chunk_size: int = 1 << 20,
    etag_path: Optional[str] = None,
    progress_cb: Optional[Callable[[int, int], None]] = None,
    timeout: int = 600,
    force_download: bool = False,
) -> Dict[str, Any]:
    """
    Download a URL to `path`, supporting ETag-based conditional GET and retries.

    Returns metadata describing whether the file changed and how many bytes were written:
      {"status": "downloaded"|"not_modified", "bytes": <int>, "total": <int>, "etag": <str|None>, "path": <str>}
    """
    session = _scryfall_session()
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    etag_file = _etag_file_for(dest, etag_path)
    headers: Dict[str, str] = {}
    cached_etag = None
    if force_download:
        try:
            if etag_file.exists():
                etag_file.unlink()
        except Exception:
            pass
    else:
        if etag_file.exists():
            try:
                cached_etag = etag_file.read_text(encoding="utf-8").strip()
            except Exception:
                cached_etag = None
            if cached_etag:
                headers["If-None-Match"] = cached_etag

    response = session.get(url, stream=True, timeout=timeout, headers=headers)
    if response.status_code == 304:
        return {
            "status": "not_modified",
            "etag": cached_etag,
            "bytes": 0,
            "total": 0,
            "path": str(dest),
        }

    response.raise_for_status()
    total = int(response.headers.get("Content-Length") or 0)
    written = 0

    try:
        with open(tmp, "wb") as fh:
            for chunk in response.iter_content(chunk_size=chunk_size):
                if not chunk:
                    continue
                fh.write(chunk)
                written += len(chunk)
                if progress_cb:
                    progress_cb(written, total)
        os.replace(tmp, dest)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except Exception:
                pass

    new_etag = response.headers.get("ETag")
    if new_etag:
        try:
            etag_file.write_text(new_etag, encoding="utf-8")
        except Exception:
            pass
    elif etag_file.exists():
        try:
            etag_file.unlink()
        except Exception:
            pass

    return {
        "status": "downloaded",
        "etag": new_etag,
        "bytes": written,
        "total": total,
        "path": str(dest),
    }

# -----------------------------------------------------------------------------
# Diagnostics
# -----------------------------------------------------------------------------
def cache_stats(path: Optional[str] = None) -> Dict[str, Any]:
    prints_path = default_cards_path(path)
    rul_path = rulings_bulk_path()

    prints_file_exists = os.path.exists(prints_path)
    prints_size = os.path.getsize(prints_path) if prints_file_exists else 0
    prints_mtime = os.path.getmtime(prints_path) if prints_file_exists else None

    rul_file_exists = os.path.exists(rul_path)
    rul_size = os.path.getsize(rul_path) if rul_file_exists else 0
    rul_mtime = os.path.getmtime(rul_path) if rul_file_exists else None

    return {
        "prints": {
            "file": prints_path,
            "exists": prints_file_exists,
            "size_bytes": prints_size,
            "modified_at": prints_mtime,
            "age_seconds": (time.time() - prints_mtime) if prints_mtime else None,
            "stale": default_is_stale(prints_path) if prints_file_exists else True,
            "records": len(_cache),
            "unique_sets": len(all_set_codes()) if _cache else 0,
            "unique_oracles": len(_by_oracle),
            "index_sizes": {
                "by_set_cn": len(_by_set_cn),
                "by_oracle": len(_by_oracle),
                "by_set_num": len(_idx_by_set_num),
                "by_name": len(_idx_by_name),
                "by_front": len(_idx_by_front),
            },
        },
        "rulings": {
            "file": rul_path,
            "exists": rul_file_exists,
            "size_bytes": rul_size,
            "modified_at": rul_mtime,
            "age_seconds": (time.time() - rul_mtime) if rul_mtime else None,
            "stale": rulings_is_stale(rul_path) if rul_file_exists else True,
            "oracle_keys": len(_rulings_by_oracle),
            "entries": sum(len(v) for v in _rulings_by_oracle.values()) if _rulings_by_oracle else 0,
        },
    }

# -----------------------------------------------------------------------------
# Name/Set helpers used by CLI and routes
# -----------------------------------------------------------------------------
# Keep only real mappings; do NOT alias VTHB->THB (we want each set's art)
_ALIAS_MAP: Dict[str, str] = {
    # example vendor fixups:
    # "plist": "plst",
}

def normalize_set_code(code: Optional[str]) -> str:
    sc = (code or "").strip().lower()
    if not sc:
        return sc
    return _ALIAS_MAP.get(sc, sc)

def candidates_by_set_and_name(set_code: str, name: str) -> List[Dict[str, Any]]:
    sc = normalize_set_code(set_code)
    nk = _name_key(name)
    out: List[Dict[str, Any]] = []
    for c in _cache:
        if (c.get("set") or "").lower() != sc:
            continue
        if _name_key(c.get("name", "")) == nk:
            out.append(c)
    return out

@lru_cache(maxsize=32768)
def unique_oracle_by_name(name: str) -> Optional[str]:
    nk = _name_key(name)
    if not nk:
        return None

    def _variant_strings(raw: str) -> List[str]:
        variants: List[str] = []

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
            frag = raw.split("/", 1)[0]
            _add(frag)
        if "," in raw:
            _add(raw.replace(",", ""))
        return variants

    variant_strings = _variant_strings(name)
    variant_keys = {k for k in (_name_key(v) for v in variant_strings) if k}
    variant_keys.add(nk)

    seen_print_ids: Set[tuple] = set()
    candidates: List[Dict[str, Any]] = []

    def _extend(items: List[Dict[str, Any]]) -> None:
        for c in items or []:
            pid = c.get("id") or (
                c.get("oracle_id"),
                c.get("set"),
                c.get("collector_number"),
                c.get("lang"),
            )
            if not pid or pid in seen_print_ids:
                continue
            seen_print_ids.add(pid)
            candidates.append(c)

    for key in variant_keys:
        _extend(_idx_by_name.get(key) or [])
        _extend(_idx_by_front.get(key) or [])
        _extend(_idx_by_back.get(key) or [])

    if not candidates:
        return None

    def _filter(
        items: List[Dict[str, Any]],
        *,
        banned_layouts: Set[str] = frozenset(),
        banned_set_types: Set[str] = frozenset(),
        require_physical: bool = False,
    ) -> List[Dict[str, Any]]:
        filtered: List[Dict[str, Any]] = []
        for c in items:
            oid = c.get("oracle_id")
            if not oid:
                continue
            layout = (c.get("layout") or "").lower()
            if layout in banned_layouts:
                continue
            set_type = (c.get("set_type") or "").lower()
            if set_type in banned_set_types:
                continue
            if require_physical and c.get("digital"):
                continue
            filtered.append(c)
        return filtered

    def _unique_oid(items: List[Dict[str, Any]]) -> Optional[str]:
        ids = {c.get("oracle_id") for c in items if c.get("oracle_id")}
        if len(ids) == 1:
            return next(iter(ids))
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
        oracle = _unique_oid(subset)
        if oracle:
            return oracle

    # Last resort: prefer candidates whose face names match any variant directly.
    variant_key_set = {_name_key(v) for v in variant_strings if _name_key(v)}
    for candidate in candidates:
        oid = candidate.get("oracle_id")
        if not oid:
            continue
        names = [candidate.get("name") or ""]
        for face in candidate.get("card_faces") or []:
            names.append((face or {}).get("name") or "")
        name_keys = {_name_key(n) for n in names if n}
        if variant_key_set & name_keys:
            return oid

    return None

# -----------------------------------------------------------------------------
# COMPAT: older names
# -----------------------------------------------------------------------------
def cache_exists(path: Optional[str] = None) -> bool:
    return default_cache_exists(path)

def is_stale(path: Optional[str] = None) -> bool:
    return default_is_stale(path)

def load_cache(path: Optional[str] = None) -> bool:
    return load_default_cache(path)

def reload_cache(path: Optional[str] = None) -> bool:
    return reload_default_cache(path)

def clear_cache_files(include_default_cards: bool = False) -> int:
    n = 0
    targets = []
    if include_default_cards:
        targets.append(default_cards_path())
    targets.append(rulings_bulk_path())

    for p in targets:
        try:
            if os.path.exists(p):
                os.remove(p)
                n += 1
        except Exception:
            pass
    try:
        _clear_in_memory_prints()
    except Exception:
        pass
    try:
        global _rulings_by_oracle, _rulings_loaded_path
        _rulings_by_oracle = {}
        _rulings_loaded_path = None
    except Exception:
        pass
    return n

# -----------------------------------------------------------------------------
# Optional: progress loader used by CLI (--progress)
# -----------------------------------------------------------------------------
def load_and_index_with_progress(path: Optional[str] = None, step: int = 5000, progress_cb=None) -> bool:
    global _cache, _by_set_cn, _by_oracle, _set_names
    global _idx_by_set_num, _idx_by_name, _idx_by_front, _idx_by_back
    p = default_cards_path(path)
    if not os.path.exists(p):
        return False
    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f) or []
    total = len(data)

    _cache = []
    _by_set_cn.clear()
    _by_oracle.clear()
    _set_names = None
    _idx_by_set_num.clear()
    _idx_by_name.clear()
    _idx_by_front.clear()
    _idx_by_back.clear()

    done = 0
    for c in data:
        _cache.append(c)
        scode = (c.get("set") or "").lower()
        cn = str(c.get("collector_number") or "")
        if scode and cn:
            _by_set_cn[_key_set_cn(scode, cn)] = c
            n = _cn_num(cn)
            if n is not None:
                _idx_by_set_num.setdefault((scode, n), []).append(c)
        oid = c.get("oracle_id")
        if oid:
            _by_oracle.setdefault(oid, []).append(c)

        nk_full = _name_key(c.get("name", ""))
        if nk_full:
            _idx_by_name.setdefault(nk_full, []).append(c)
        nk_front = _name_key(_front_face_name(c))
        if nk_front:
            _idx_by_front.setdefault(nk_front, []).append(c)
        for back_name in _back_face_names(c):
            nk_back = _name_key(back_name)
            if nk_back:
                _idx_by_back.setdefault(nk_back, []).append(c)

        done += 1
        if progress_cb and (done % max(1, int(step)) == 0 or done == total):
            try:
                progress_cb(done, total)
            except Exception:
                pass
    return True

# -----------------------------------------------------------------------------
# Scryfall - All Cards (search for browser & unique view)
# -----------------------------------------------------------------------------
try:
    DEFAULT_PATH  # already defined in your file
except NameError:
    DEFAULT_PATH = "data/default-cards.json"

def _read_json_array(path: Path):
    with open(path, "rb") as f:
        head = f.read(2)
    is_gz = head == b"\x1f\x8b" or str(path).lower().endswith(".gz")
    if is_gz:
        with gzip.open(path, "rt", encoding="utf-8") as fin:
            return json.load(fin)
    else:
        with open(path, "r", encoding="utf-8") as fin:
            return json.load(fin)

def _normalize_search_text(value: str) -> str:
    if not value:
        return ""
    return re.sub(r"[^a-z0-9]+", " ", str(value).lower()).strip()


@lru_cache(maxsize=1)
def get_all_prints():
    p = Path(DEFAULT_PATH)
    if not p.exists() or p.stat().st_size == 0:
        return []
    try:
        data = _read_json_array(p)
        return data if isinstance(data, list) else []
    except Exception:
        return []

@lru_cache(maxsize=4096)
def find_print_by_id(sid: str):
    if not sid:
        return None
    sid = str(sid).lower()
    for p in get_all_prints():
        pid = (p.get("id") or "").lower()
        if pid == sid:
            return p
    return None

def search_prints(name_q: str | None = None, set_code: str | None = None, limit: int = 60, offset: int = 0):
    prints = get_all_prints()
    if not prints:
        return [], 0

    tokens = [t for t in _normalize_search_text(name_q).split() if t]
    set_code = (set_code or "").lower()

    def ok(p):
        if set_code and (p.get("set") or "").lower() != set_code:
            return False
        if tokens:
            nm = _normalize_search_text(p.get("name"))
            for t in tokens:
                if t not in nm:
                    return False
        return True

    filtered = (p for p in prints if ok(p))
    buf, total = [], 0
    limit = int(limit or 0)
    offset = max(int(offset or 0), 0)
    stop_at = offset + limit if limit > 0 else None
    for p in filtered:
        if stop_at is None or total < stop_at:
            buf.append(p)
        total += 1
    if limit <= 0:
        return buf[offset:], total
    return buf[offset:offset+limit], total

def _image_set_for_print(p):
    label_bits = []
    if p.get("set"): label_bits.append((p.get("set") or "").upper())
    if p.get("collector_number"): label_bits.append(str(p.get("collector_number")))
    if p.get("lang"): label_bits.append(str(p.get("lang")).upper())
    label = " · ".join(label_bits) if label_bits else (p.get("name") or "")

    iu = p.get("image_uris") or {}
    faces = p.get("card_faces") or []
    small = iu.get("small")
    normal = iu.get("normal") or iu.get("large")
    if (not small) and faces and isinstance(faces, list):
        iu2 = (faces[0] or {}).get("image_uris") or {}
        small = iu2.get("small")
        normal = normal or iu2.get("normal") or iu2.get("large")

    key = p.get("illustration_id") or p.get("id")
    return small, normal, label, key

def _unique_art_images(prints, per_card_images=8):
    out, seen = [], set()
    for pr in prints or []:
        small, normal, label, key = _image_set_for_print(pr)
        if not (small or normal):
            continue
        if key and key in seen:
            continue
        seen.add(key)
        out.append({"small": small, "normal": normal, "label": label})
        if len(out) >= per_card_images:
            break
    return out

def search_unique_cards(name_q: str | None = None,
                        set_code: str | None = None,
                        limit: int = 60,
                        offset: int = 0,
                        per_card_images: int = 8):
    prints = get_all_prints()
    if not prints:
        return [], 0

    tokens = [t for t in _normalize_search_text(name_q).split() if t]
    want_set = (set_code or "").lower()

    groups = {}  # oracle_id -> dict

    for p in prints:
        nm = _normalize_search_text(p.get("name"))
        if tokens and any(t not in nm for t in tokens):
            continue

        oid = p.get("oracle_id") or p.get("id")
        if not oid:
            continue

        g = groups.get(oid)
        if g is None:
            groups[oid] = {
                "oracle_id": oid,
                "name": p.get("name"),
                "rep": p,
                "has_wanted_set": ((p.get("set") or "").lower() == want_set) if want_set else True,
                "members": [p],
            }
        else:
            if want_set and not g["has_wanted_set"]:
                g["has_wanted_set"] = ((p.get("set") or "").lower() == want_set)
            if len(g["members"]) < max(12, per_card_images * 2):
                g["members"].append(p)

    items = []
    for g in groups.values():
        if want_set and not g["has_wanted_set"]:
            continue

        rep = g["rep"]
        images = _unique_art_images(g["members"], per_card_images=per_card_images)

        purchase_uris = rep.get("purchase_uris") or {}
        tcg = purchase_uris.get("tcgplayer") or (rep.get("related_uris") or {}).get("tcgplayer")

        items.append({
            "oracle_id": g["oracle_id"],
            "id": rep.get("id"),
            "name": rep.get("name"),
            "set": (rep.get("set") or "").upper(),
            "set_name": rep.get("set_name"),
            "collector_number": rep.get("collector_number"),
            "lang": (rep.get("lang") or "").upper(),
            "rarity": (rep.get("rarity") or "").title() if rep.get("rarity") else None,
            "scryfall_uri": rep.get("scryfall_uri"),
            "tcgplayer_url": tcg,
            "images": images,
        })

    items.sort(key=lambda it: ((it["name"] or "").lower(), it["set"], str(it["collector_number"] or "")))
    total = len(items)
    return items[offset: offset + limit], total

# --- Token helpers ------------------------------------------------------------

def _token_images_from_print(p):
    """Return {'small':..., 'normal':...} URLs for a token print dict."""
    if not p:
        return {"small": None, "normal": None}
    iu = p.get("image_uris") or {}
    if iu:
        return {"small": iu.get("small"), "normal": iu.get("normal") or iu.get("large")}
    faces = p.get("card_faces") or []
    if faces and isinstance(faces, list):
        iu2 = (faces[0] or {}).get("image_uris") or {}
        return {"small": iu2.get("small"), "normal": iu2.get("normal") or iu2.get("large")}
    return {"small": None, "normal": None}


def tokens_from_print(print_obj):
    """
    Return a list of token records created by this print (via Scryfall 'all_parts').
    Each record: {'id','name','type_line','images':{'small','normal'}}
    """
    if not print_obj:
        return []

    parts = print_obj.get("all_parts") or []
    token_parts = [p for p in parts if (p.get("component") == "token")]
    out, seen = [], set()

    for tp in token_parts:
        tid = tp.get("id")
        if not tid or tid in seen:
            continue
        seen.add(tid)

        tok = None
        try:
            tok = find_print_by_id(tid)  # existing helper in this module
        except Exception:
            tok = None

        name = (tok or {}).get("name") or tp.get("name")
        type_line = (tok or {}).get("type_line")
        imgs = _token_images_from_print(tok)

        out.append({"id": tid, "name": name, "type_line": type_line, "images": imgs})

    # Heuristic fallback if no relationships but text mentions creating a token
    if not out:
        text = (print_obj.get("oracle_text") or "").lower()
        if "create" in text and "token" in text:
            out.append({"id": None, "name": "Token", "type_line": None, "images": {"small": None, "normal": None}})

    return out


@lru_cache(maxsize=1)
def _token_name_index() -> Dict[str, Dict[str, Any]]:
    """
    Build a name->print index for token cards to decorate fallbacks with art/type lines.
    Prefer EN tokens; fall back to first seen.
    """
    idx: Dict[str, Dict[str, Any]] = {}
    try:
        prints = get_all_prints()
    except Exception:
        prints = []
    for p in prints:
        if (p.get("layout") or "").lower() != "token":
            continue
        nm = (p.get("name") or "").strip()
        if not nm:
            continue
        key = nm.casefold()
        lang = (p.get("lang") or "en").lower()
        keep = idx.get(key)
        if (keep is None) or (keep.get("lang", "").lower() != "en" and lang == "en"):
            idx[key] = p
    return idx

def _lookup_token_by_name(name: str) -> Optional[Dict[str, Any]]:
    if not name:
        return None
    return _token_name_index().get(name.strip().casefold())

def _token_stub(name: str, type_line: Optional[str] = None) -> Dict[str, Any]:
    p = _lookup_token_by_name(name)
    if p:
        # return a lite object with image + links
        iu = _image_uris(p)
        return {
            "id": p.get("id"),
            "name": p.get("name"),
            "type_line": p.get("type_line"),
            "scryfall_uri": p.get("scryfall_uri"),
            "images": {"small": iu.get("small"), "normal": iu.get("normal")}
        }
    # fallback stub
    return {
        "id": None,
        "name": name,
        "type_line": type_line or "Token",
        "scryfall_uri": None,
        "images": {"small": None, "normal": None}
    }

def _dedupe_tokens(items: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out = []
    for t in items:
        key = ( (t.get("id") or ""),
                (t.get("name") or "").casefold(),
                (t.get("type_line") or "").casefold() )
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
    return out

def tokens_from_text(card_obj: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Very light-weight text scan for common tokens and 'copy' tokens.
    """
    txt = "  ".join(_oracle_texts(card_obj)).casefold()
    found: List[Dict[str, Any]] = []

    # generic artifact tokens
    for nm, tl in _GENERIC_TOKENS.items():
        if nm in txt and "token" in txt:
            found.append(_token_stub(nm.capitalize(), tl))

    # 'create a token that's a copy ...'
    if "token that's a copy" in txt or "token that’s a copy" in txt or "create a copy of target" in txt:
        found.append(_token_stub("Copy", "Token — Copy"))

    return _dedupe_tokens(found)

def tokens_from_print(card_obj: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Returns a list of token descriptors created by this card:
      [{ id?, name, type_line, scryfall_uri?, images:{small,normal} }]
    Uses 'all_parts' when present; otherwise falls back to tokens_from_text.
    """
    if not card_obj:
        return []

    items: List[Dict[str, Any]] = []

    # 1) Strong signal: 'all_parts' with component == 'token'
    for part in (card_obj.get("all_parts") or []):
        try:
            if (part.get("component") or "").lower() != "token":
                continue
            tok_id = part.get("id")
            tok_name = part.get("name") or ""
            # prefer exact print by id (faster and keeps art), else name index
            tok_print = find_print_by_id(tok_id) if tok_id else _lookup_token_by_name(tok_name)
            if tok_print:
                iu = _image_uris(tok_print)
                items.append({
                    "id": tok_print.get("id"),
                    "name": tok_print.get("name"),
                    "type_line": tok_print.get("type_line"),
                    "scryfall_uri": tok_print.get("scryfall_uri"),
                    "images": {"small": iu.get("small"), "normal": iu.get("normal")}
                })
            else:
                # minimal
                items.append(_token_stub(tok_name))
        except Exception:
            # never break page render on a bad part
            continue

    # 2) Fallback text heuristics (add any we didn't see yet)
    if not items:
        items.extend(tokens_from_text(card_obj))

    return _dedupe_tokens(items)

def tokens_from_oracle(oracle_id: Optional[str]) -> List[Dict[str, Any]]:
    """
    Union of tokens across all prints of a card (oracle_id).
    """
    if not oracle_id:
        return []
    out: List[Dict[str, Any]] = []
    for p in prints_for_oracle(oracle_id) or []:
        out.extend(tokens_from_print(p))
    return _dedupe_tokens(out)
# ---------------------------------------------------------------------------

# --- tokens_from_print --------------------------------------------------------
def _image_small_normal_from_print(p):
    """Return {'small':..., 'normal':...} for a (token) print dict."""
    if not p:
        return {"small": None, "normal": None}

    iu = p.get("image_uris") or {}
    if iu:
        return {"small": iu.get("small"), "normal": iu.get("normal") or iu.get("large")}
    faces = p.get("card_faces") or []
    if faces and isinstance(faces, list):
        iu2 = (faces[0] or {}).get("image_uris") or {}
        return {"small": iu2.get("small"), "normal": iu2.get("normal") or iu2.get("large")}
    return {"small": None, "normal": None}


def tokens_from_print(print_obj):
    """
    Return a list of token records created by this print.

    Uses Scryfall's 'all_parts' relationship when present (component=='token').
    Each record looks like:
      {
        'id': <scryfall id>,
        'name': <token name>,
        'type_line': <type line>,
        'images': {'small': <url or None>, 'normal': <url or None>},
      }

    Falls back gracefully if relationships are absent.
    """
    if not print_obj:
        return []

    parts = print_obj.get("all_parts") or []
    token_parts = [p for p in parts if (p.get("component") == "token")]

    out = []
    seen = set()
    for tp in token_parts:
        tid = tp.get("id")
        if not tid or tid in seen:
            continue
        seen.add(tid)

        # try to resolve the token print
        tok = None
        try:
            tok = find_print_by_id(tid)  # should already exist in this module
        except Exception:
            tok = None

        # token name/type_line from the resolved print if we have it; otherwise from the part
        name = (tok or {}).get("name") or tp.get("name")
        type_line = (tok or {}).get("type_line")

        images = _image_small_normal_from_print(tok)
        out.append({
            "id": tid,
            "name": name,
            "type_line": type_line,
            "images": images,
        })

    # Heuristic fallback: some very old data might lack all_parts.
    # If the oracle text clearly says "create ... token", we keep the UI simple and skip guessing images.
    if not out:
        text = (print_obj.get("oracle_text") or "").lower()
        if "create" in text and "token" in text:
            out.append({
                "id": None,
                "name": "Token",
                "type_line": None,
                "images": {"small": None, "normal": None},
            })

    return out

# --- Token helpers ------------------------------------------------------------

def _token_images_from_print(p):
    """Return {'small':..., 'normal':...} for a (token) print dict."""
    if not p:
        return {"small": None, "normal": None}
    iu = p.get("image_uris") or {}
    if iu:
        return {"small": iu.get("small"), "normal": iu.get("normal") or iu.get("large")}
    faces = p.get("card_faces") or []
    if faces and isinstance(faces, list):
        iu2 = (faces[0] or {}).get("image_uris") or {}
        return {"small": iu2.get("small"), "normal": iu2.get("normal") or iu2.get("large")}
    return {"small": None, "normal": None}


def tokens_from_print(print_obj):
    """
    Return a list of token records created by this print (via Scryfall 'all_parts').
    Each record: {'id','name','type_line','images':{'small','normal'}}
    """
    if not print_obj:
        return []

    parts = print_obj.get("all_parts") or []
    token_parts = [p for p in parts if (p.get("component") == "token")]
    out, seen = [], set()

    for tp in token_parts:
        tid = tp.get("id")
        if not tid or tid in seen:
            continue
        seen.add(tid)

        tok = None
        try:
            tok = find_print_by_id(tid)  # defined earlier in this file
        except Exception:
            tok = None

        name = (tok or {}).get("name") or tp.get("name")
        type_line = (tok or {}).get("type_line")
        imgs = _token_images_from_print(tok)

        out.append({"id": tid, "name": name, "type_line": type_line, "images": imgs})

    # Heuristic fallback if no relationships but the text says "create ... token"
    if not out:
        text = (print_obj.get("oracle_text") or "").lower()
        if "create" in text and "token" in text:
            out.append({"id": None, "name": "Token", "type_line": None, "images": {"small": None, "normal": None}})

    return out
