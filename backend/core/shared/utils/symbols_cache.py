# services/symbols_cache.py
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests
from core.shared.utils.assets import static_url

# Where we store JSON + SVGs (always under backend/static/symbols)
STATIC_DIR = Path(__file__).resolve().parents[3] / "static"
SYMBOLS_DIR = STATIC_DIR / "symbols"
SYMBOLS_JSON = SYMBOLS_DIR / "scryfall_symbols.json"
_LEGACY_SYMBOLS_JSON = Path(os.getenv("SCRYFALL_DATA_DIR", "data")) / "scryfall_symbols.json"
_SYMBOL_REFRESH_BACKOFF_MINUTES = int(os.getenv("SCRYFALL_SYMBOL_BACKOFF_MINUTES", "15"))
_LOG = logging.getLogger(__name__)

# Runtime cache (module-level)
_SYMBOL_MAP: Optional[Dict[str, dict]] = None
_SRC_MAP_LOCAL: Optional[Dict[str, str]] = None
_SRC_MAP_REMOTE: Optional[Dict[str, str]] = None
_LAST_REFRESH_FAILURE_AT: Optional[datetime] = None

# Matches "{X}", "{2/W}", "{W/U}", "{B/P}", "{T}", "{C}", "{∞}", etc.
MANA_RE = re.compile(r"\{[^}]+\}")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _recent_failure() -> bool:
    if _LAST_REFRESH_FAILURE_AT is None:
        return False
    return (_now() - _LAST_REFRESH_FAILURE_AT) < timedelta(minutes=_SYMBOL_REFRESH_BACKOFF_MINUTES)


def _record_failure() -> None:
    global _LAST_REFRESH_FAILURE_AT
    _LAST_REFRESH_FAILURE_AT = _now()


def _clear_failure() -> None:
    global _LAST_REFRESH_FAILURE_AT
    _LAST_REFRESH_FAILURE_AT = None


def _normalize_filename(sym: str) -> str:
    """
    Turn a symbol string like '{G/U}' or '{B/P}' into a safe filename.
    """
    s = sym.strip().strip("{}")
    s = s.replace("/", "_").replace("∞", "inf").replace("½", "half").replace("☯", "yin")
    s = s.replace(" ", "")
    return f"{s}.svg"

def _download(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    dest.write_bytes(r.content)

def ensure_symbols_cache(
    force: bool = False,
    *,
    return_status: bool = False,
) -> Dict[str, dict] | Tuple[Dict[str, dict], bool]:
    """
    Loads (and if needed downloads) Scryfall symbology and local SVGs.
    Returns a map: symbol string -> symbol record (from Scryfall).
    When return_status is True, also returns whether the fetch hit the remote API.
    """
    global _SYMBOL_MAP, _SRC_MAP_LOCAL, _SRC_MAP_REMOTE

    # Load JSON if present (migrate legacy location if needed).
    if not force and not SYMBOLS_JSON.exists() and _LEGACY_SYMBOLS_JSON.exists():
        try:
            SYMBOLS_DIR.mkdir(parents=True, exist_ok=True)
            SYMBOLS_JSON.write_text(_LEGACY_SYMBOLS_JSON.read_text(encoding="utf-8"), encoding="utf-8")
        except Exception:
            pass

    if not force and SYMBOLS_JSON.exists():
        try:
            data = json.loads(SYMBOLS_JSON.read_text(encoding="utf-8"))
            items = data.get("data") or []
        except Exception:
            items = []
    else:
        items = []

    fetched_remote = False
    need_remote = force or not items
    if need_remote:
        if items and _recent_failure():
            _LOG.info("Skipping Scryfall symbology refresh (recent failure backoff).")
        else:
            try:
                resp = requests.get("https://api.scryfall.com/symbology", timeout=30)
                resp.raise_for_status()
                payload = resp.json()
                items = payload.get("data") or []
                SYMBOLS_DIR.mkdir(parents=True, exist_ok=True)
                SYMBOLS_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
                fetched_remote = True
                _clear_failure()
            except Exception as exc:
                _LOG.warning("Failed to download Scryfall symbology: %s", exc)
                _record_failure()
                if not items:
                    raise

    # Build symbol map and download local SVGs (if not present)
    sym_map = {}
    for rec in items:
        sym = rec.get("symbol")  # like "{W}"
        if not sym:
            continue
        sym_map[sym] = rec

        svg_uri = rec.get("svg_uri")
        if svg_uri:
            fn = _normalize_filename(sym)
            fp = SYMBOLS_DIR / fn
            if not fp.exists():
                try:
                    _download(svg_uri, fp)
                except Exception:
                    # Failing to download a single SVG shouldn't break the entire flow
                    pass

    _SYMBOL_MAP = sym_map
    # Build src maps
    _SRC_MAP_LOCAL = {}
    _SRC_MAP_REMOTE = {}
    for sym, rec in sym_map.items():
        fn = _normalize_filename(sym)
        local = static_url(f"symbols/{fn}")
        _SRC_MAP_LOCAL[sym] = local
        _SRC_MAP_REMOTE[sym] = rec.get("svg_uri") or rec.get("png_uri") or ""

    if return_status:
        return sym_map, fetched_remote
    return sym_map

def get_symbol_src_map(use_local: bool = True) -> Dict[str, str]:
    """
    Returns a map: symbol string -> src (local / remote).
    """
    global _SYMBOL_MAP, _SRC_MAP_LOCAL, _SRC_MAP_REMOTE
    if _SYMBOL_MAP is None or _SRC_MAP_LOCAL is None or _SRC_MAP_REMOTE is None:
        ensure_symbols_cache(force=False)
    return _SRC_MAP_LOCAL if use_local else _SRC_MAP_REMOTE

def render_mana_html(text: Optional[str], use_local: bool = True) -> str:
    """
    Replace all {...} tokens in a mana-cost-like string with <img class="mana"> icons.
    """
    if not text:
        return "—"
    src_map = get_symbol_src_map(use_local=use_local)

    def repl(m):
        sym = m.group(0)
        src = src_map.get(sym)
        alt = sym
        if not src:
            return alt  # fallback to plain text
        return f'<img class="mana" src="{src}" alt="{alt}" title="{alt}">'

    return MANA_RE.sub(repl, text)

def render_oracle_html(text: Optional[str], use_local: bool = True) -> str:
    """
    Replace mana symbols inside oracle text and convert newlines to <br>.
    """
    if not text:
        return "—"
    html = render_mana_html(text, use_local=use_local)
    html = html.replace("\n", "<br>")
    return html

def colors_to_icons(colors: Optional[List[str]], use_local: bool = True) -> List[str]:
    """
    Map color letters to corresponding single-color pips: W/U/B/R/G -> list of src URLs.
    """
    if not colors:
        return []
    src_map = get_symbol_src_map(use_local=use_local)
    out = []
    for c in colors:
        sym = f"{{{c.upper()}}}"
        src = src_map.get(sym)
        if src:
            out.append(src)
    return out
