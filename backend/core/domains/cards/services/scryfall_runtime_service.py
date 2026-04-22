"""Runtime, path, and live-fetch helpers for the Scryfall cache wrapper."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Callable, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def build_scryfall_session(
    *,
    user_agent: str,
    retry_total: int,
    retry_backoff: float,
    status_forcelist: tuple[int, ...],
) -> requests.Session:
    """Create a shared requests Session with retry and UA headers."""
    retry_kwargs = {
        "total": retry_total,
        "backoff_factor": retry_backoff,
        "status_forcelist": status_forcelist,
        "raise_on_status": False,
    }
    methods = frozenset(["GET", "HEAD", "OPTIONS"])
    try:
        retries = Retry(allowed_methods=methods, **retry_kwargs)
    except TypeError:
        retries = Retry(method_whitelist=methods, **retry_kwargs)

    adapter = HTTPAdapter(max_retries=retries)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update(
        {
            "User-Agent": user_agent,
            "Accept": "application/json",
        }
    )
    return session


def guess_instance_data_root(*, file_path: str, env: Optional[dict[str, str]] = None) -> Path:
    """Best-effort data root when no Flask app context is active."""
    environ = env or os.environ
    configured_root = environ.get("SCRYFALL_DATA_DIR")
    if configured_root:
        return Path(configured_root)
    instance_dir = environ.get("INSTANCE_DIR")
    if instance_dir:
        return Path(instance_dir) / "data"
    return Path(file_path).resolve().parents[5] / "instance" / "data"


def data_root(
    *,
    current_app: Any,
    guess_instance_data_root_fn: Callable[[], Path],
) -> Path:
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
        return guess_instance_data_root_fn()


def default_cards_path(
    path: Optional[str] = None,
    *,
    data_root_fn: Callable[[], Path],
) -> str:
    return path or str(data_root_fn() / "scryfall_default_cards.json")


def rulings_bulk_path(
    path: Optional[str] = None,
    *,
    data_root_fn: Callable[[], Path],
) -> str:
    return path or str(data_root_fn() / "scryfall_rulings.json")


def default_cache_exists(
    path: Optional[str] = None,
    *,
    default_cards_path_fn: Callable[[Optional[str]], str],
) -> bool:
    return os.path.exists(default_cards_path_fn(path))


def default_is_stale(
    path: Optional[str] = None,
    *,
    max_age: int,
    default_cards_path_fn: Callable[[Optional[str]], str],
) -> bool:
    resolved = default_cards_path_fn(path)
    if not os.path.exists(resolved):
        return True
    return (time.time() - os.path.getmtime(resolved)) > max_age


def fetch_live_print(
    set_code: str,
    collector_number: str,
    *,
    session_factory: Callable[[], Any],
    name_hint: Optional[str] = None,
    timeout: int = 8,
) -> Optional[dict[str, Any]]:
    """
    Fetch a single print directly from Scryfall when it is missing from the local cache.
    Attempts a direct /cards/{set}/{cn} lookup, then a set-scoped name search.
    """
    scode = (set_code or "").strip().lower()
    cn = str(collector_number or "").strip()
    if not scode or not cn:
        return None

    session = session_factory()
    try:
        response = session.get(f"https://api.scryfall.com/cards/{scode}/{cn}", timeout=timeout)
        if response.status_code == 200:
            return response.json()
    except Exception:
        pass

    if name_hint:
        try:
            response = session.get(
                "https://api.scryfall.com/cards/search",
                params={"q": f'!"{name_hint}" set:{scode} cn:{cn}'},
                timeout=timeout,
            )
            if response.status_code == 200:
                payload = response.json() or {}
                items = payload.get("data") or []
                if items:
                    return items[0]
        except Exception:
            pass
    return None


__all__ = [
    "build_scryfall_session",
    "data_root",
    "default_cache_exists",
    "default_cards_path",
    "default_is_stale",
    "fetch_live_print",
    "guess_instance_data_root",
    "rulings_bulk_path",
]
