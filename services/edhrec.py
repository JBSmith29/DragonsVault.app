"""EDHREC scraping and caching helpers for deck synergy features."""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import unicodedata
from threading import get_ident
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import unescape
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from uuid import uuid4
from urllib.parse import urlparse

import requests

__all__ = [
    "EdhrecError",
    "slugify_commander",
    "slugify_theme",
    "normalize_card_key",
    "load_commander",
    "load_theme",
    "ensure_commander_data",
    "ensure_theme_data",
    "commander_cardviews",
    "theme_cardviews",
    "merge_cardviews",
    "cache_root",
    "commander_cache_snapshot",
    "theme_cache_snapshot",
]

_NEXT_DATA_RE = re.compile(
    r'__NEXT_DATA__" type="application/json">(.*?)</script>',
    re.DOTALL,
)

_DEFAULT_HEADERS = {
    "User-Agent": os.getenv(
        "EDHREC_USER_AGENT",
        "DragonsVault/1.0 (+https://localhost)",
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.8",
    "Connection": "keep-alive",
}

_CACHE_ROOT = Path(os.getenv("EDHREC_CACHE_DIR", Path("data") / "edhrec"))
_COMMANDER_CACHE = _CACHE_ROOT / "commanders"
_THEME_CACHE = _CACHE_ROOT / "themes"
_CACHE_DEFAULT_TTL_HOURS = int(os.getenv("EDHREC_CACHE_TTL_HOURS", "72"))

_THEME_VARIANT_EXCLUSIONS = frozenset(
    {
        "exhibition",
        "core",
        "upgraded",
        "optimized",
        "cedh",
        "budget",
        "expensive",
        "primer",
        "cheap",
        "precon",
        "preconstructed",
        "average",
        "average-deck",
        "combo",
        "duel",
        "box",
    }
)

_LOG = logging.getLogger(__name__)


class EdhrecError(RuntimeError):
    """Raised when EDHREC data could not be fetched or parsed."""


@dataclass(frozen=True)
class CardView:
    """Normalized EDHREC card recommendation entry."""

    name: str
    slug: str
    category: str
    rank: int
    source_kind: str
    synergy: Optional[float]
    inclusion: Optional[float]
    num_decks: Optional[int]
    potential_decks: Optional[int]
    url: Optional[str]
    label: Optional[str]
    trend_zscore: Optional[float]
    source: str
    source_label: str
    tag: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable representation."""
        payload: Dict[str, Any] = {
            "name": self.name,
            "slug": self.slug,
            "category": self.category,
            "rank": self.rank,
            "source": self.source,
            "source_label": self.source_label,
            "source_kind": self.source_kind,
        }
        if self.tag:
            payload["tag"] = self.tag
        if self.synergy is not None:
            payload["synergy"] = self.synergy
        if self.inclusion is not None:
            payload["inclusion"] = self.inclusion
        if self.num_decks is not None:
            payload["num_decks"] = self.num_decks
        if self.potential_decks is not None:
            payload["potential_decks"] = self.potential_decks
        if self.url:
            payload["url"] = self.url
        if self.label:
            payload["label"] = self.label
        if self.trend_zscore is not None:
            payload["trend_zscore"] = self.trend_zscore
        return payload


def _ensure_cache_root() -> None:
    for path in (_CACHE_ROOT, _COMMANDER_CACHE, _THEME_CACHE):
        path.mkdir(parents=True, exist_ok=True)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now_utc().isoformat()


def _payload_age_hours(payload: Dict[str, Any]) -> Optional[float]:
    ts = payload.get("fetched_at")
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = _now_utc() - dt.astimezone(timezone.utc)
    return delta.total_seconds() / 3600.0


def _is_fresh(payload: Dict[str, Any], max_age_hours: Optional[int]) -> bool:
    if max_age_hours is None:
        max_age_hours = _CACHE_DEFAULT_TTL_HOURS
    if max_age_hours <= 0:
        return False
    age = _payload_age_hours(payload)
    if age is None:
        return False
    return age < max_age_hours


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    prefix = f"{path.name}.{os.getpid()}.{get_ident()}.{uuid4().hex}."
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=prefix,
        suffix=".tmp",
        delete=False,
    ) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        tmp_name = handle.name
    tmp_path = Path(tmp_name)
    try:
        tmp_path.replace(path)
    except Exception:
        # Attempt best-effort cleanup before bubbling the error up.
        tmp_path.unlink(missing_ok=True)
        raise


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return None


def _full_edhrec_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    if url.startswith("http://") or url.startswith("https://"):
        return url
    if not url.startswith("/"):
        url = f"/{url}"
    return f"https://edhrec.com{url}"


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalise_header(header: Any) -> Optional[str]:
    if isinstance(header, str):
        return header.strip() or None
    if isinstance(header, dict):
        for key in ("title", "label", "text"):
            v = header.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return None


def _slugify(
    value: str,
    *,
    plus_as_word: bool = False,
) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    text = text.replace("//", " ")
    text = text.replace("/", " ")
    text = text.replace("&", " and ")
    text = text.replace("@", " at ")
    if plus_as_word:
        text = text.replace("+", " plus ")
    else:
        text = text.replace("+", " ")
    text = text.replace("'", "")
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-{2,}", "-", text)
    return text.strip("-")


def slugify_commander(name: str) -> str:
    """Return the EDHREC slug for a commander name."""
    return _slugify(name, plus_as_word=True)


def slugify_theme(tag: str) -> str:
    """Return the EDHREC slug for a theme/deck tag."""
    return _slugify(tag, plus_as_word=True)


def normalize_card_key(name: str) -> str:
    """Return a slug key suitable for comparing EDHREC card entries."""
    return _slugify(name, plus_as_word=True)


def _extract_cardlists(json_dict: Dict[str, Any]) -> List[Dict[str, Any]]:
    cardlists: List[Dict[str, Any]] = []
    for entry in json_dict.get("cardlists", []) or []:
        if not isinstance(entry, dict):
            continue
        header = _normalise_header(entry.get("header"))
        cardviews: List[Dict[str, Any]] = []
        for idx, raw_view in enumerate(entry.get("cardviews") or []):
            if not isinstance(raw_view, dict):
                continue
            name = raw_view.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            slug = normalize_card_key(name)
            cardviews.append(
                {
                    "name": name,
                    "slug": slug,
                    "sanitized": raw_view.get("sanitized"),
                    "sanitized_wo": raw_view.get("sanitized_wo"),
                    "url": _full_edhrec_url(raw_view.get("url")),
                    "synergy": _safe_float(raw_view.get("synergy")),
                    "inclusion": _safe_float(raw_view.get("inclusion")),
                    "label": raw_view.get("label"),
                    "num_decks": raw_view.get("num_decks"),
                    "potential_decks": raw_view.get("potential_decks"),
                    "trend_zscore": _safe_float(raw_view.get("trend_zscore")),
                    "rank": idx + 1,
                }
            )
        cardlists.append(
            {
                "header": header,
                "tag": entry.get("tag"),
                "cardviews": cardviews,
            }
        )
    return cardlists


def _extract_panels(raw_panels: Any) -> Dict[str, Any]:
    panels: Dict[str, Any] = {}
    if not isinstance(raw_panels, dict):
        return panels
    piechart = raw_panels.get("piechart")
    if isinstance(piechart, dict):
        content: List[Dict[str, Any]] = []
        for item in piechart.get("content") or []:
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
            entry: Dict[str, Any] = {"label": label, "value": numeric_value}
            if item.get("color"):
                entry["color"] = item["color"]
            content.append(entry)
        if content:
            panels["piechart"] = {
                "title": piechart.get("title"),
                "content": content,
            }
    return panels


def _fetch_panels_from_json_api(url: str) -> Dict[str, Any]:
    parsed = urlparse(url)
    path_parts = [part for part in parsed.path.strip("/").split("/") if part]
    if not path_parts:
        return {}
    category = path_parts[0]
    slug_parts = path_parts[1:]
    if not slug_parts or category not in {"commanders", "themes"}:
        return {}
    api_path = "/".join(slug_parts)
    json_url = f"https://json.edhrec.com/pages/{category}/{api_path}.json"
    headers = dict(_DEFAULT_HEADERS)
    headers.setdefault("Referer", url)
    try:
        response = requests.get(json_url, headers=headers, timeout=30)
        if response.status_code == 404:
            return {}
        response.raise_for_status()
        data = response.json()
    except Exception:
        return {}
    return _extract_panels(data.get("panels"))


def _strip_html_tags(raw_html: str) -> str:
    return re.sub(r"<[^>]+>", "", raw_html)


def _extract_theme_options(html: str, commander_slug: Optional[str]) -> List[Dict[str, str]]:
    if not commander_slug or not html:
        return []
    pattern = re.compile(
        rf'<a[^>]+href="/commanders/{re.escape(commander_slug)}/([a-z0-9-]+)"[^>]*>(.*?)</a>',
        re.IGNORECASE | re.DOTALL,
    )
    options: List[Dict[str, str]] = []
    seen: set[str] = set()
    for match in pattern.finditer(html):
        theme_slug = match.group(1).strip().lower()
        if not theme_slug or theme_slug in _THEME_VARIANT_EXCLUSIONS:
            continue
        if theme_slug in seen:
            continue
        raw_label = match.group(2)
        if not isinstance(raw_label, str):
            continue
        label = unescape(_strip_html_tags(raw_label)).strip()
        label = re.sub(r"\s*\(\s*[\d,]+\s+deck[s]?\)\s*$", "", label, flags=re.IGNORECASE)
        label = re.sub(r"\s*\(\s*[\d,]+\s*\)\s*$", "", label)
        label = re.sub(r"(?<=\D)\s*[\d,]+\s*$", "", label)
        if not label:
            label = theme_slug.replace("-", " ").title()
        options.append({"slug": theme_slug, "label": label})
        seen.add(theme_slug)
    return options


def _download_json_dict(
    url: str,
    *,
    commander_slug: Optional[str] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any], List[Dict[str, str]]]:
    headers = dict(_DEFAULT_HEADERS)
    headers.setdefault("Referer", url)
    response = requests.get(url, headers=headers, timeout=30)
    if response.status_code == 404:
        raise EdhrecError(f"EDHREC page not found: {url}")
    response.raise_for_status()
    html = response.text
    match = _NEXT_DATA_RE.search(html)
    if not match:
        raise EdhrecError("EDHREC page did not contain Next.js payload.")
    raw = json.loads(match.group(1))
    try:
        container = raw["props"]["pageProps"]["data"]["container"]
    except KeyError as exc:
        raise EdhrecError(f"Unexpected EDHREC payload structure for {url}") from exc
    json_dict = container.get("json_dict")
    if not isinstance(json_dict, dict):
        raise EdhrecError("EDHREC JSON container was not a dictionary.")
    panels = _extract_panels(container.get("panels"))
    if not panels:
        fallback_panels = _fetch_panels_from_json_api(url)
        if fallback_panels:
            panels.update(fallback_panels)
    theme_options = _extract_theme_options(html, commander_slug)
    return json_dict, panels, theme_options


def _payload_from_json_dict(
    *,
    kind: str,
    slug: str,
    name: Optional[str],
    url: str,
    json_dict: Dict[str, Any],
    panels: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "kind": kind,
        "slug": slug,
        "name": name,
        "fetched_at": _now_iso(),
        "source_url": url,
        "cardlists": _extract_cardlists(json_dict),
    }
    if panels:
        payload["panels"] = panels
    meta_keys = ("card", "header", "description", "keywords")
    metadata: Dict[str, Any] = {}
    for key in meta_keys:
        value = json_dict.get(key)
        if isinstance(value, (dict, list, str, int, float)) and value not in (None, "", []):
            metadata[key] = value
    if metadata:
        payload["metadata"] = metadata
    return payload


def load_commander(slug: str) -> Optional[Dict[str, Any]]:
    """Load cached commander payload by slug."""
    return _read_json(_COMMANDER_CACHE / f"{slug}.json")


def load_theme(slug: str) -> Optional[Dict[str, Any]]:
    """Load cached theme payload by slug."""
    return _read_json(_THEME_CACHE / f"{slug}.json")


def _ensure_payload(
    *,
    cache_path: Path,
    slug: str,
    name: Optional[str],
    url: str,
    kind: str,
    force_refresh: bool,
    max_age_hours: Optional[int],
    extra_attrs: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    _ensure_cache_root()
    cached = _read_json(cache_path)
    payload = cached
    needs_download = force_refresh or payload is None
    if not needs_download:
        if not _is_fresh(payload, max_age_hours):
            needs_download = True
        elif kind == "commander" and payload.get("theme_options") is None:
            needs_download = True
        else:
            if extra_attrs:
                needs_write = any(payload.get(key) != value for key, value in extra_attrs.items())
                if needs_write:
                    payload = dict(payload)
                    payload.update(extra_attrs)
                    _write_json(cache_path, payload)
            return payload, None

    try:
        json_dict, panels, theme_options = _download_json_dict(
            url,
            commander_slug=slug if kind == "commander" else None,
        )
    except (EdhrecError, requests.RequestException, OSError) as exc:
        if cached:
            return cached, f"Using cached EDHREC data for {slug}: {exc}"
        return None, f"Unable to fetch EDHREC data for {slug}: {exc}"
    payload = _payload_from_json_dict(
        kind=kind,
        slug=slug,
        name=name,
        url=url,
        json_dict=json_dict,
        panels=panels,
    )
    if kind == "commander":
        payload["theme_options"] = theme_options
    if extra_attrs:
        payload.update(extra_attrs)
    _write_json(cache_path, payload)
    return payload, None


def ensure_commander_data(
    commander_name: str,
    *,
    theme_slug: Optional[str] = None,
    force_refresh: bool = False,
    max_age_hours: Optional[int] = None,
) -> Tuple[Optional[str], Optional[Dict[str, Any]], Optional[str]]:
    """
    Ensure cached data exists for the given commander.

    Returns (slug, payload, error_message). When an error occurs, payload will be None and
    error_message will contain a human-readable reason.
    """
    if not commander_name:
        return None, None, "Commander name is missing."
    slug_source = commander_name.partition("//")[0].strip()
    slug = slugify_commander(slug_source or commander_name)
    if not slug:
        return None, None, f"Unable to derive EDHREC slug for '{commander_name}'."
    cache_suffix = f"__{theme_slug}" if theme_slug else ""
    cache_path = _COMMANDER_CACHE / f"{slug}{cache_suffix}.json"
    url = f"https://edhrec.com/commanders/{slug}"
    if theme_slug:
        url = f"{url}/{theme_slug}"
    payload, error = _ensure_payload(
        cache_path=cache_path,
        slug=slug,
        name=commander_name,
        url=url,
        kind="commander",
        force_refresh=force_refresh,
        max_age_hours=max_age_hours,
        extra_attrs={"applied_theme": theme_slug},
    )
    if payload and "panels" not in payload:
        payload, error = _ensure_payload(
            cache_path=cache_path,
            slug=slug,
            name=commander_name,
            url=url,
            kind="commander",
            force_refresh=True,
            max_age_hours=max_age_hours,
            extra_attrs={"applied_theme": theme_slug},
        )
    return slug, payload, error


def ensure_theme_data(
    theme_name: str,
    *,
    force_refresh: bool = False,
    max_age_hours: Optional[int] = None,
) -> Tuple[Optional[str], Optional[Dict[str, Any]], Optional[str]]:
    """
    Ensure cached data exists for the given theme/deck tag.

    Returns (slug, payload, error_message). When an error occurs, payload will be None and
    error_message will contain a human-readable reason.
    """
    if not theme_name:
        return None, None, "Theme name is missing."
    slug = slugify_theme(theme_name)
    if not slug:
        return None, None, f"Unable to derive EDHREC slug for '{theme_name}'."
    cache_path = _THEME_CACHE / f"{slug}.json"
    payload, error = _ensure_payload(
        cache_path=cache_path,
        slug=slug,
        name=theme_name,
        url=f"https://edhrec.com/themes/{slug}",
        kind="theme",
        force_refresh=force_refresh,
        max_age_hours=max_age_hours,
    )
    if payload and "panels" not in payload:
        payload, error = _ensure_payload(
            cache_path=cache_path,
            slug=slug,
            name=theme_name,
            url=f"https://edhrec.com/themes/{slug}",
            kind="theme",
            force_refresh=True,
            max_age_hours=max_age_hours,
        )
    return slug, payload, error


def _cardviews_from_payload(
    payload: Optional[Dict[str, Any]],
    *,
    categories: Optional[Iterable[str]] = None,
) -> List[CardView]:
    if not payload:
        return []
    cardlists = payload.get("cardlists") or []
    if not isinstance(cardlists, list):
        return []
    category_filter = None
    if categories:
        category_filter = {c.lower() for c in categories}
    collected: List[CardView] = []
    for entry in cardlists:
        if not isinstance(entry, dict):
            continue
        header = entry.get("header")
        if not isinstance(header, str):
            continue
        if category_filter and header.lower() not in category_filter:
            continue
        tag = entry.get("tag")
        for raw in entry.get("cardviews") or []:
            if not isinstance(raw, dict):
                continue
            name = raw.get("name")
            slug = raw.get("slug") or normalize_card_key(name or "")
            if not name or not slug:
                continue
            collected.append(
                CardView(
                    name=name,
                    slug=slug,
                    category=header,
                    rank=int(raw.get("rank") or len(collected) + 1),
                    source_kind=str(payload.get("kind") or ""),
                    synergy=_safe_float(raw.get("synergy")),
                    inclusion=_safe_float(raw.get("inclusion")),
                    num_decks=raw.get("num_decks"),
                    potential_decks=raw.get("potential_decks"),
                    url=_full_edhrec_url(raw.get("url")),
                    label=raw.get("label"),
                    trend_zscore=_safe_float(raw.get("trend_zscore")),
                    source=str(payload.get("slug") or ""),
                    source_label=str(payload.get("name") or payload.get("slug") or ""),
                    tag=tag if isinstance(tag, str) else None,
                )
            )
    return collected


def commander_cardviews(
    payload: Optional[Dict[str, Any]],
    *,
    categories: Optional[Iterable[str]] = None,
) -> List[CardView]:
    """Return commander EDHREC card recommendations as CardView entries."""
    return _cardviews_from_payload(payload, categories=categories)


def theme_cardviews(
    payload: Optional[Dict[str, Any]],
    *,
    categories: Optional[Iterable[str]] = None,
) -> List[CardView]:
    """Return theme EDHREC card recommendations as CardView entries."""
    return _cardviews_from_payload(payload, categories=categories)


def merge_cardviews(*collections: Iterable[CardView]) -> Dict[str, CardView]:
    """
    Merge multiple cardview iterables, keeping the entry with the best synergy score.

    Returns a mapping of card slug -> CardView.
    """
    merged: Dict[str, CardView] = {}
    for collection in collections:
        for view in collection:
            existing = merged.get(view.slug)
            if existing is None:
                merged[view.slug] = view
                continue
            # Prefer the higher synergy score, fall back to higher inclusion if synergy missing.
            if (view.synergy or 0) > (existing.synergy or 0):
                merged[view.slug] = view
            elif view.synergy == existing.synergy and (view.inclusion or 0) > (existing.inclusion or 0):
                merged[view.slug] = view
    return merged


def cache_root() -> Path:
    """Return the root directory used for EDHREC cache files."""
    _ensure_cache_root()
    return _CACHE_ROOT


def _cache_snapshot(cache_dir: Path) -> Dict[str, Any]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    latest_dt: Optional[datetime] = None
    latest_slug: Optional[str] = None
    total = 0
    for file_path in cache_dir.glob("*.json"):
        total += 1
        payload = _read_json(file_path) or {}
        slug = payload.get("slug") or file_path.stem
        fetched_at = payload.get("fetched_at")
        try:
            dt = datetime.fromisoformat(fetched_at) if fetched_at else None
        except Exception:
            dt = None
        if dt and (latest_dt is None or dt > latest_dt):
            latest_dt = dt
            latest_slug = slug
    return {
        "count": total,
        "latest_slug": latest_slug,
        "latest_fetched_at": latest_dt.isoformat() if latest_dt else None,
        "path": str(cache_dir),
    }


def commander_cache_snapshot() -> Dict[str, Any]:
    """Return summary information about the commander cache."""
    return _cache_snapshot(_COMMANDER_CACHE)


def theme_cache_snapshot() -> Dict[str, Any]:
    """Return summary information about the theme cache."""
    return _cache_snapshot(_THEME_CACHE)

