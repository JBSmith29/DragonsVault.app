# services/scryfall_cache.py
from __future__ import annotations

import math
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple, Set, Iterable, Callable
from flask import current_app
from functools import lru_cache

from core.domains.cards.services import scryfall_catalog_service as catalog
from core.domains.cards.services import scryfall_cache_state_service as state_service
from core.domains.cards.services import scryfall_http_service as http_service
from core.domains.cards.services import scryfall_index_service as index_service
from core.domains.cards.services import scryfall_metadata_service as metadata_service
from core.domains.cards.services import scryfall_print_summary_service as print_summary
from core.domains.cards.services import scryfall_rulings_service as rulings_service
from core.domains.cards.services import scryfall_runtime_service as runtime_service
from core.domains.cards.services import scryfall_set_metadata_service as set_metadata_service
from core.domains.cards.services import scryfall_set_profile_service as set_profile

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

# rulings bulk (indexed by oracle_id)
_rulings_by_oracle: Dict[str, List[Dict[str, Any]]] = {}
_rulings_loaded_path: Optional[str] = None

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
    return runtime_service.guess_instance_data_root(file_path=__file__)

def _data_root() -> Path:
    return runtime_service.data_root(
        current_app=current_app,
        guess_instance_data_root_fn=_guess_instance_data_root,
    )

def default_cards_path(path: Optional[str] = None) -> str:
    return runtime_service.default_cards_path(path, data_root_fn=_data_root)

def rulings_bulk_path(path: Optional[str] = None) -> str:
    return runtime_service.rulings_bulk_path(path, data_root_fn=_data_root)

# Legacy constants (kept for callers that read a module attribute)
DEFAULT_CARDS_PATH = default_cards_path()
RULINGS_BULK_PATH = rulings_bulk_path()
DEFAULT_PATH = DEFAULT_CARDS_PATH  # legacy alias used by CLI
DEFAULT_MAX_AGE = 7 * 24 * 3600  # 7 days
RULINGS_MAX_AGE = 7 * 24 * 3600  # 7 days
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
    return index_service.cn_variants(cn)

def _cn_num(cn: str) -> Optional[int]:
    return index_service.cn_num(cn)


def normalize_color_identity(colors: Optional[Iterable[str]]) -> Tuple[str, int]:
    return metadata_service.normalize_color_identity(colors)


def _joined_oracle_text(print_data: Dict[str, Any]) -> str:
    return metadata_service._joined_oracle_text(print_data)


def _face_payload(face_data: Dict[str, Any], fallback: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return metadata_service._face_payload(face_data, fallback=fallback)


def metadata_from_print(print_data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    return metadata_service.metadata_from_print(print_data)


def _collector_sort_key(value: Optional[str]) -> Tuple[int, str]:
    return metadata_service._collector_sort_key(value)


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
    return metadata_service.search_local_cards(
        ensure_cache_loaded_fn=ensure_cache_loaded,
        cache=_cache,
        name=name,
        set_code=set_code,
        base_types=base_types,
        typal=typal,
        colors=colors,
        color_mode=color_mode,
        commander_only=commander_only,
        order=order,
        direction=direction,
        page=page,
        per=per,
    )

def _name_key(name: str) -> str:
    return index_service.name_key(name)

def _front_face_name(card_obj: Dict[str, Any]) -> str:
    return index_service.front_face_name(card_obj)


def _back_face_names(card_obj: Dict[str, Any]) -> List[str]:
    return index_service.back_face_names(card_obj)

def display_name_for_print(pr: Dict[str, Any]) -> str:
    return print_summary.display_name_for_print(pr)


def type_label_for_print(pr: Dict[str, Any]) -> str:
    return print_summary.type_label_for_print(pr)

def _clear_in_memory_prints():
    state_service.clear_in_memory_prints(
        globals(),
        clear_cached_set_profiles_fn=set_profile.clear_cached_set_profiles,
        bump_cache_epoch_fn=_bump_cache_epoch,
        cache_clearers=[prints_for_oracle.cache_clear, unique_oracle_by_name.cache_clear],
    )

# -----------------------------------------------------------------------------
# Default cards bulk (prints) — load & lookups
# -----------------------------------------------------------------------------
def default_cache_exists(path: Optional[str] = None) -> bool:
    return runtime_service.default_cache_exists(
        path,
        default_cards_path_fn=default_cards_path,
    )

def default_is_stale(path: Optional[str] = None, max_age: int = DEFAULT_MAX_AGE) -> bool:
    return runtime_service.default_is_stale(
        path,
        max_age=max_age,
        default_cards_path_fn=default_cards_path,
    )

def _prime_default_indexes() -> None:
    state_service.prime_default_indexes(
        globals(),
        prime_default_indexes_fn=index_service.prime_default_indexes,
        key_set_cn_fn=_key_set_cn,
        clear_cached_set_profiles_fn=set_profile.clear_cached_set_profiles,
        bump_cache_epoch_fn=_bump_cache_epoch,
        cache_clearers=[prints_for_oracle.cache_clear, unique_oracle_by_name.cache_clear],
    )

def load_default_cache(path: Optional[str] = None) -> bool:
    """Load default_cards JSON (prints) into memory and index it."""
    return state_service.load_default_cache(
        globals(),
        path=path,
        default_cards_path_fn=default_cards_path,
        prime_default_indexes_fn=_prime_default_indexes,
        clear_cached_catalog_fn=catalog.clear_cached_catalog,
    )

def reload_default_cache(path: Optional[str] = None) -> bool:
    return state_service.reload_default_cache(
        path=path,
        clear_in_memory_prints_fn=_clear_in_memory_prints,
        clear_cached_catalog_fn=catalog.clear_cached_catalog,
        load_default_cache_fn=load_default_cache,
    )

def find_by_set_cn(set_code: str, collector_number: str, name_hint: Optional[str] = None) -> Optional[Dict[str, Any]]:
    return index_service.find_by_set_cn(
        set_code,
        collector_number,
        name_hint=name_hint,
        by_set_cn=_by_set_cn,
        idx_by_set_num=_idx_by_set_num,
        idx_by_name=_idx_by_name,
        idx_by_front=_idx_by_front,
        key_set_cn_fn=_key_set_cn,
    )

def find_by_set_cn_loose(set_code: str, collector_number: str, name_hint: Optional[str] = None) -> Optional[Dict[str, Any]]:
    return index_service.find_by_set_cn_loose(
        set_code,
        collector_number,
        name_hint=name_hint,
        by_set_cn=_by_set_cn,
        idx_by_set_num=_idx_by_set_num,
    )


def fetch_live_print(set_code: str, collector_number: str, name_hint: Optional[str] = None) -> Optional[Dict[str, Any]]:
    return http_service.fetch_live_print(set_code, collector_number, name_hint=name_hint)


@lru_cache(maxsize=32768)
def prints_for_oracle(oracle_id: Optional[str]) -> Tuple[Dict[str, Any], ...]:
    return index_service.prints_for_oracle(oracle_id, by_oracle=_by_oracle)

def set_name_for_code(code: str) -> Optional[str]:
    global _set_names
    if not code:
        return None
    if _set_names is None:
        _set_names = set_metadata_service.build_set_name_map(_cache)
    return _set_names.get(code.lower())

def set_release_for_code(code: str) -> Optional[str]:
    """
    Return the earliest printed release date for a set, based on default_cards data.
    """
    global _set_releases
    if not code:
        return None
    if _set_releases is None:
        _set_releases = set_metadata_service.build_set_release_map(_cache)
    return _set_releases.get(code.lower())

def all_set_codes() -> List[str]:
    return set_metadata_service.all_set_codes(_cache)

def _build_set_profiles() -> Dict[str, Dict[str, Any]]:
    return set_profile.build_set_profiles(
        cache=_cache,
        ensure_cache_loaded_fn=ensure_cache_loaded,
    )

def set_profiles(set_codes: Optional[Iterable[str]] = None) -> Dict[str, Dict[str, Any]]:
    return set_profile.set_profiles(
        set_codes,
        cache=_cache,
        ensure_cache_loaded_fn=ensure_cache_loaded,
    )

def set_image_samples(set_code: str, *, per_set: int = 6) -> List[Dict[str, Any]]:
    return set_profile.set_image_samples(
        set_code,
        cache=_cache,
        image_uris_fn=_image_uris,
        per_set=per_set,
    )

def image_for_print(print_obj: Dict[str, Any]) -> Dict[str, Optional[str]]:
    return print_summary.image_for_print(print_obj, image_uris_fn=_image_uris)

def resolve_print_bundle(set_code: str, collector_number: str, name_hint: Optional[str] = None) -> Optional[Dict[str, Any]]:
    return print_summary.resolve_print_bundle(
        set_code,
        collector_number,
        name_hint=name_hint,
        find_by_set_cn_fn=find_by_set_cn,
        image_uris_fn=_image_uris,
    )

# -----------------------------------------------------------------------------
# Bulk rulings — single local file indexed by oracle_id (no per-card HTTP)
# -----------------------------------------------------------------------------
def rulings_bulk_exists(path: Optional[str] = None) -> bool:
    return rulings_service.rulings_bulk_exists(
        path,
        rulings_bulk_path_fn=rulings_bulk_path,
    )

def rulings_is_stale(path: Optional[str] = None, max_age: int = RULINGS_MAX_AGE) -> bool:
    return rulings_service.rulings_is_stale(
        path,
        rulings_bulk_path_fn=rulings_bulk_path,
        max_age=max_age,
    )

def load_rulings_bulk(path: Optional[str] = None) -> int:
    """
    Load rulings JSON (list of objects) and index by oracle_id.
    Returns number of rulings loaded.
    """
    global _rulings_by_oracle, _rulings_loaded_path
    _rulings_by_oracle, _rulings_loaded_path = rulings_service.load_rulings_bulk(
        path,
        rulings_bulk_path_fn=rulings_bulk_path,
    )
    return sum(len(v) for v in _rulings_by_oracle.values())

def rulings_for_oracle(oracle_id: str) -> List[Dict[str, Any]]:
    if not oracle_id:
        return []
    if not _rulings_by_oracle:
        if rulings_bulk_exists():
            load_rulings_bulk()
    return rulings_service.rulings_for_oracle(
        oracle_id,
        rulings_by_oracle=_rulings_by_oracle,
    )

# -----------------------------------------------------------------------------
# Download helpers (prints & rulings)
# -----------------------------------------------------------------------------
def fetch_bulk_index() -> List[Dict[str, Any]]:
    return http_service.fetch_bulk_index()


def get_bulk_metadata(kind: str) -> Optional[Dict[str, Any]]:
    return http_service.get_bulk_metadata(kind)


def get_bulk_download_uri(kind: str) -> Optional[str]:
    return http_service.get_bulk_download_uri(kind)


def get_default_cards_download_uri() -> Optional[str]:
    return http_service.get_default_cards_download_uri()


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
    return http_service.stream_download_to(
        path,
        url,
        chunk_size=chunk_size,
        etag_path=etag_path,
        progress_cb=progress_cb,
        timeout=timeout,
        force_download=force_download,
    )

# -----------------------------------------------------------------------------
# Diagnostics
# -----------------------------------------------------------------------------
def cache_stats(path: Optional[str] = None) -> Dict[str, Any]:
    from core.domains.cards.services import scryfall_bulk_service as bulk

    return bulk.cache_stats(
        path,
        default_cards_path_fn=default_cards_path,
        rulings_bulk_path_fn=rulings_bulk_path,
        default_is_stale_fn=default_is_stale,
        rulings_is_stale_fn=rulings_is_stale,
        prints_record_count=len(_cache),
        unique_set_count=len(all_set_codes()) if _cache else 0,
        unique_oracle_count=len(_by_oracle),
        by_set_cn_count=len(_by_set_cn),
        by_set_num_count=len(_idx_by_set_num),
        by_name_count=len(_idx_by_name),
        by_front_count=len(_idx_by_front),
        rulings_oracle_key_count=len(_rulings_by_oracle),
        rulings_entry_count=sum(len(v) for v in _rulings_by_oracle.values()) if _rulings_by_oracle else 0,
    )

# -----------------------------------------------------------------------------
# Name/Set helpers used by CLI and routes
# -----------------------------------------------------------------------------
# Keep only real mappings; do NOT alias VTHB->THB (we want each set's art)
def normalize_set_code(code: Optional[str]) -> str:
    return set_metadata_service.normalize_set_code(code)

def candidates_by_set_and_name(set_code: str, name: str) -> List[Dict[str, Any]]:
    return index_service.candidates_by_set_and_name(
        set_code,
        name,
        cache=_cache,
        normalize_set_code_fn=normalize_set_code,
    )

@lru_cache(maxsize=32768)
def unique_oracle_by_name(name: str) -> Optional[str]:
    return index_service.unique_oracle_by_name(
        name,
        idx_by_name=_idx_by_name,
        idx_by_front=_idx_by_front,
        idx_by_back=_idx_by_back,
    )

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
    return state_service.clear_cache_files(
        globals(),
        include_default_cards=include_default_cards,
        default_cards_path_fn=default_cards_path,
        rulings_bulk_path_fn=rulings_bulk_path,
        clear_in_memory_prints_fn=_clear_in_memory_prints,
        clear_cached_catalog_fn=catalog.clear_cached_catalog,
    )

# -----------------------------------------------------------------------------
# Optional: progress loader used by CLI (--progress)
# -----------------------------------------------------------------------------
def load_and_index_with_progress(path: Optional[str] = None, step: int = 5000, progress_cb=None) -> bool:
    return state_service.load_and_index_with_progress(
        globals(),
        path=path,
        default_cards_path_fn=default_cards_path,
        step=step,
        progress_cb=progress_cb,
        key_set_cn_fn=_key_set_cn,
        cn_num_fn=_cn_num,
        name_key_fn=_name_key,
        front_face_name_fn=_front_face_name,
        back_face_names_fn=_back_face_names,
        clear_cached_set_profiles_fn=set_profile.clear_cached_set_profiles,
        clear_cached_catalog_fn=catalog.clear_cached_catalog,
    )

# -----------------------------------------------------------------------------
# Scryfall - All Cards (search for browser & token helpers)
# -----------------------------------------------------------------------------
try:
    DEFAULT_PATH  # already defined in your file
except NameError:
    DEFAULT_PATH = "data/default-cards.json"


def get_all_prints():
    return catalog.get_all_prints(DEFAULT_PATH)


def find_print_by_id(sid: str):
    return catalog.find_print_by_id(DEFAULT_PATH, sid)


def search_prints(name_q: str | None = None, set_code: str | None = None, limit: int = 60, offset: int = 0):
    return catalog.search_prints(
        DEFAULT_PATH,
        name_q=name_q,
        set_code=set_code,
        limit=limit,
        offset=offset,
    )


def search_unique_cards(
    name_q: str | None = None,
    set_code: str | None = None,
    limit: int = 60,
    offset: int = 0,
    per_card_images: int = 8,
):
    return catalog.search_unique_cards(
        DEFAULT_PATH,
        name_q=name_q,
        set_code=set_code,
        limit=limit,
        offset=offset,
        per_card_images=per_card_images,
    )


def search_tokens(name_q: str | None = None, limit: int = 36) -> List[Dict[str, Any]]:
    return catalog.search_tokens(DEFAULT_PATH, name_q=name_q, limit=limit)


def tokens_from_print(print_obj) -> List[Dict[str, Any]]:
    return catalog.tokens_from_print(DEFAULT_PATH, print_obj)


def tokens_from_oracle(oracle_id: Optional[str]) -> List[Dict[str, Any]]:
    if not oracle_id:
        return []
    return catalog.tokens_from_oracle(DEFAULT_PATH, prints_for_oracle(oracle_id) or [])
