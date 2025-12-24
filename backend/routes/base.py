"""Shared blueprint and helper utilities for DragonsVault routes."""

from __future__ import annotations

import re
from collections import OrderedDict
from functools import lru_cache
import time

from flask import Blueprint, current_app, redirect, render_template, request, url_for, flash
from flask_login import current_user
try:
    from flask_limiter.util import get_remote_address  # type: ignore
except Exception:  # pragma: no cover
    get_remote_address = None  # type: ignore
from sqlalchemy import Integer, case, cast, func, or_
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import load_only

from extensions import cache, db
from models import Card, Folder, FolderRole, SiteRequest
from services import scryfall_cache as sc
from services.pricing import (
    format_price_text as _format_price_text,
    prices_for_print as _prices_for_print,
    prices_for_print_exact as _prices_for_print_exact,
)
from services.scryfall_cache import cache_ready, ensure_cache_loaded, find_by_set_cn, prints_for_oracle, unique_oracle_by_name

views = Blueprint("views", __name__)

API_PAGE_SIZE = 175  # Scryfall /cards/search page size

# Historical defaults for collection buckets (used as fallback if nothing configured)
DEFAULT_COLLECTION_FOLDERS = {"lands", "common", "uncommon", "rare", "mythic", "to add"}

# Valid wishlist statuses
ALLOWED_WISHLIST_STATUSES = {"open", "to_fetch", "ordered", "acquired", "removed"}

_NO_VALUE = object()


def limiter_key_user_or_ip() -> str:
    """Use the authenticated user id when present; otherwise fall back to IP."""
    user_id = getattr(current_user, "id", None) or current_user.get_id()
    if user_id:
        return f"user:{user_id}"
    addr = None
    if get_remote_address:
        try:
            addr = get_remote_address()
        except Exception:
            addr = None
    addr = addr or request.remote_addr or "unknown"
    return f"ip:{addr}"


# ---------------------------------------------------------------------------
# Collection helpers
# ---------------------------------------------------------------------------

def _collection_rows_with_fallback() -> list[tuple[int | None, str | None]]:
    """Return (id, name) tuples for folders that represent collection buckets."""
    rows: list[tuple[int | None, str | None]] = []
    try:
        rows = (
            db.session.query(Folder.id, Folder.name)
            .join(FolderRole, FolderRole.folder_id == Folder.id)
            .filter(FolderRole.role == FolderRole.ROLE_COLLECTION)
            .order_by(func.lower(Folder.name))
            .all()
        )
    except SQLAlchemyError:
        current_app.logger.exception("Failed to load collection folders (primary query)")
        db.session.rollback()
    if rows:
        return rows

    # Last-resort hard-coded defaults so the UI can still render
    return [(None, name.title()) for name in sorted(DEFAULT_COLLECTION_FOLDERS)]


def _collection_folder_ids() -> set[int]:
    """Convenience accessor for the collection folder primary keys."""
    return {fid for fid, _ in _collection_rows_with_fallback() if fid is not None}


def _collection_folder_names() -> list[str]:
    """List the human-facing names of folders currently treated as collection buckets."""
    return [name for _, name in _collection_rows_with_fallback() if name]


def _collection_folder_lower_names() -> set[str]:
    """Return normalized names for folders explicitly tagged as collection buckets."""
    rows = _collection_rows_with_fallback()
    lowered = {(name or "").strip().lower() for fid, name in rows if fid is not None and name}
    return lowered


def _collection_metadata() -> tuple[list[int], list[str], set[str]]:
    """Gather ids/names in one go so expensive lookups happen once per request."""
    rows = _collection_rows_with_fallback()
    ids = [fid for fid, _ in rows if fid is not None]
    names = [name for _, name in rows if name]
    lowered = {(name or "").strip().lower() for fid, name in rows if fid is not None and name}
    return ids, names, lowered


# ---------------------------------------------------------------------------
# Scryfall print caching helpers
# ---------------------------------------------------------------------------

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
_FAILED_ORACLE_TTL = 300.0  # seconds


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
        for k, ts in list(_FAILED_ORACLE_CACHE.items()):
            if ts < cutoff:
                _FAILED_ORACLE_CACHE.pop(k, None)
        if len(_FAILED_ORACLE_CACHE) > 2048:
            excess = len(_FAILED_ORACLE_CACHE) - 2048
            for k in list(_FAILED_ORACLE_CACHE.keys())[:excess]:
                _FAILED_ORACLE_CACHE.pop(k, None)


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
            pr = sc.find_by_set_cn(set_code, collector_number, name) or {}
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
                (p for p in alts if (p.get("set") or "").strip().lower() == target_set and target_set), None
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
    # Sort by id to ensure deterministic ordering before fingerprinting
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


@lru_cache(maxsize=1)
def _folder_id_name_map() -> dict[int, str]:
    rows = db.session.query(Folder.id, Folder.name).all()
    return {fid: name for fid, name in rows}


def _move_folder_choices(exclude_folder_id: int | None = None) -> list[dict]:
    """Return folder options that the current user is allowed to move cards into."""
    from flask_login import current_user

    if not current_user.is_authenticated:
        return []

    query = Folder.query
    if not getattr(current_user, "is_admin", False):
        query = query.filter(or_(Folder.owner_user_id == current_user.id, Folder.owner_user_id.is_(None)))
    folders = query.order_by(func.lower(Folder.name)).all()

    options: list[dict] = []
    for folder in folders:
        if exclude_folder_id and folder.id == exclude_folder_id:
            continue
        options.append(
            {
                "id": folder.id,
                "name": folder.name or f"Folder {folder.id}",
                "is_collection": folder.is_collection,
                "is_proxy": folder.is_proxy_deck,
            }
        )
    return options


def _safe_commit() -> None:
    """Commit with rollback guard (avoid poisoning the session)."""
    try:
        db.session.commit()
    except SQLAlchemyError:
        current_app.logger.exception("Non-fatal commit failure; rolling back")
        db.session.rollback()


def _name_sort_expr():
    """Prefer Card.name_sort if schema has it, else lower(name)."""
    return getattr(Card, "name_sort", None) or func.lower(Card.name)


def _collector_number_numeric():
    """
    Naturalize collector_number for sorting:
      - Cast only when the entire collector_number is digits; otherwise None.
    """
    if db.engine.dialect.name == "sqlite":
        # SQLite lacks regex operator; use GLOB + negative match to ensure digits only.
        has_digits = Card.collector_number.op("GLOB")("[0-9]*")
        has_nondigit = Card.collector_number.op("GLOB")("*[^0-9]*")
        is_numeric = (Card.collector_number != "") & has_digits & ~has_nondigit
    else:
        is_numeric = Card.collector_number.op("~")(r"^[0-9]+$")
    return case((is_numeric, cast(Card.collector_number, Integer)), else_=None)


def _small_thumb_for_print(pr: dict | None) -> str | None:
    """
    Return a stable SMALL (146x204) image URL for any print, including MDFC/flip.
    Prefers the front face; falls back to first face with an image_uris.
    """
    if not pr:
        return None
    iu = (pr or {}).get("image_uris") or {}
    if iu.get("small"):
        return iu["small"]
    faces = (pr or {}).get("card_faces") or []
    for f in faces:
        fiu = (f or {}).get("image_uris") or {}
        if fiu.get("small"):
            return fiu["small"]
    return None


def _commander_candidates_for_folder(folder_id: int, limit: int = 60):
    """
    Likely commander candidates for a folder:
    Legendary permanents, enriched with image + print info.
    """
    if not sc.cache_ready():
        sc.ensure_cache_loaded()
    qs = (
        Card.query.filter(Card.folder_id == folder_id)
        .options(
            load_only(
                Card.id,
                Card.name,
                Card.set_code,
                Card.collector_number,
                Card.lang,
                Card.is_foil,
                Card.oracle_id,
                Card.type_line,
            )
        )
        .all()
    )

    def _image_from_print(pr):
        if not pr:
            return None
        iu = pr.get("image_uris") or {}
        if iu:
            return iu.get("small") or iu.get("normal") or iu.get("large")
        faces = pr.get("card_faces") or []
        if faces:
            iu = (faces[0] or {}).get("image_uris") or {}
            return iu.get("small") or iu.get("normal") or iu.get("large")
        return None

    out = []
    for c in qs:
        pr = _lookup_print_data(c.set_code, c.collector_number, c.name, c.oracle_id)
        tline = getattr(c, "type_line", "") or ""
        tl = tline.lower()
        if ("legendary" in tl) and ("creature" in tl or "artifact" in tl):
            out.append(
                {
                    "card_id": c.id,
                    "name": c.name,
                    "oracle_id": c.oracle_id,
                    "set_code": c.set_code,
                    "collector_number": c.collector_number,
                    "lang": c.lang,
                    "is_foil": bool(c.is_foil),
                    "image": _image_from_print(pr),
                    "type_line": tline,
                }
            )

    out.sort(key=lambda r: (r["name"] or "").lower())
    return out[:limit]


def _img_url_for_print(p, size="normal"):
    if not p:
        return None
    iu = p.get("image_uris")
    if iu:
        return iu.get(size) or iu.get("large") or iu.get("png")
    faces = p.get("card_faces") or []
    for face in faces:
        iu = face.get("image_uris") or {}
        url = iu.get(size) or iu.get("large") or iu.get("png")
        if url:
            return url
    return None


def _unique_art_variants(prints):
    """Unique art by illustration_id/id (preserves first occurence order)."""
    seen = set()
    out = []
    for p in prints or []:
        illus = p.get("illustration_id") or p.get("id")
        if illus and illus not in seen:
            seen.add(illus)
            out.append(p)
    return out


def _normalize_name(s: str) -> str:
    """Normalize a card name for deduping/comparison."""
    s = (s or "").strip()
    s = s.replace("’", "'").strip('"').strip("'")
    s = re.sub(r"\s+", " ", s)
    return s.lower()


def _norm_folder(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")


def _available_folder_ids() -> set[int]:
    return _collection_folder_ids()


# ---------------------------------------------------------------------------
# Color identity helpers (and Jinja filter)
# ---------------------------------------------------------------------------

WUBRG_ORDER = "WUBRG"

_CI_NAME_BY_SET = {
    frozenset(): "Colorless",
    frozenset(("W",)): "White",
    frozenset(("U",)): "Blue",
    frozenset(("B",)): "Black",
    frozenset(("R",)): "Red",
    frozenset(("G",)): "Green",
    # Guilds
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
    # Shards
    frozenset(("W", "U", "G")): "Bant",
    frozenset(("W", "U", "B")): "Esper",
    frozenset(("U", "B", "R")): "Grixis",
    frozenset(("B", "R", "G")): "Jund",
    frozenset(("W", "R", "G")): "Naya",
    # Wedges
    frozenset(("W", "B", "G")): "Abzan",
    frozenset(("W", "U", "R")): "Jeskai",
    frozenset(("W", "B", "R")): "Mardu",
    frozenset(("U", "B", "G")): "Sultai",
    frozenset(("U", "R", "G")): "Temur",
    # 4-color nicknames
    frozenset(("W", "U", "B", "R")): "Yore",
    frozenset(("U", "B", "R", "G")): "Glint",
    frozenset(("B", "R", "G", "W")): "Dune",
    frozenset(("R", "G", "W", "U")): "Ink",
    frozenset(("G", "W", "U", "B")): "Witch",
    # 5c
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


@views.app_template_filter("ci_name")
def jinja_ci_name(ci):
    return color_identity_name(ci)


def compute_folder_color_identity(folder_id: int):
    """
    Return (letters, label) for the folder's color identity using Scryfall cache.
    letters: e.g., "WUG" or "" (colorless). label: friendly name.
    """
    seen = set()
    rows = (
        db.session.query(Card.color_identity, Card.colors)
        .filter(Card.folder_id == folder_id)
        .all()
    )
    if not rows:
        return "", color_identity_name([])

    def _letters_from_value(value):
        if not value:
            return []
        if isinstance(value, (list, tuple, set)):
            raw = [str(v).upper() for v in value]
        else:
            raw = [ch for ch in str(value).upper()]
        return [ch for ch in raw if ch in WUBRG_ORDER]

    for ci_val, colors_val in rows:
        letters = _letters_from_value(ci_val) or _letters_from_value(colors_val)
        for ch in letters:
            seen.add(ch)

    letters = "".join([c for c in WUBRG_ORDER if c in seen])
    label = color_identity_name(letters)
    return letters, label


__all__ = [
    "ALLOWED_WISHLIST_STATUSES",
    "API_PAGE_SIZE",
    "DEFAULT_COLLECTION_FOLDERS",
    "views",
    "_collection_rows_with_fallback",
    "_available_folder_ids",
    "_bulk_print_lookup",
    "_collection_folder_ids",
    "_collection_folder_lower_names",
    "_collection_folder_names",
    "_collection_metadata",
    "_move_folder_choices",
    "_commander_candidates_for_folder",
    "_format_price_text",
    "_folder_id_name_map",
    "_img_url_for_print",
    "_lookup_print_data",
    "_name_sort_expr",
    "_normalize_name",
    "_prices_for_print",
    "_prices_for_print_exact",
    "_safe_commit",
    "_small_thumb_for_print",
    "_unique_art_variants",
    "color_identity_name",
    "compute_folder_color_identity",
    "jinja_ci_name",
]
@views.route("/", methods=["GET"])
def landing_page():
    from flask_login import current_user
    from flask import request, url_for, redirect, render_template

    if current_user.is_authenticated:
        dest = request.args.get("next") or url_for("views.dashboard")
        return redirect(dest)
    return render_template("landing.html")


LAST_UPDATED_TEXT = "November 17, 2025"


@views.route("/legal/terms")
def terms_of_service():
    return render_template("legal/terms.html", last_updated=LAST_UPDATED_TEXT)


@views.route("/legal/privacy")
def privacy_policy():
    return render_template("legal/privacy.html", last_updated=LAST_UPDATED_TEXT)


@views.route("/legal/accessibility")
def accessibility_statement():
    return render_template("legal/accessibility.html", last_updated=LAST_UPDATED_TEXT)


@views.route("/legal/disclaimer")
def legal_disclaimer():
    return render_template("legal/disclaimer.html", last_updated=LAST_UPDATED_TEXT)


@views.route("/legal/coppa")
def coppa_notice():
    return render_template("legal/coppa.html", last_updated=LAST_UPDATED_TEXT)


@views.route("/legal/cookie-policy")
def cookie_policy():
    return render_template("legal/cookie.html", last_updated=LAST_UPDATED_TEXT)


@views.route("/legal/terms-of-use")
def terms_of_use():
    return render_template("legal/terms_use.html", last_updated=LAST_UPDATED_TEXT)


@views.route("/legal/shipping-policy")
def shipping_policy():
    return render_template("legal/shipping.html", last_updated=LAST_UPDATED_TEXT)


@views.route("/legal/returns-policy")
def returns_policy():
    return render_template("legal/returns.html", last_updated=LAST_UPDATED_TEXT)


@views.route("/legal/do-not-sell", methods=["GET", "POST"])
def do_not_sell():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        email = (request.form.get("email") or "").strip()
        flash("Your opt-out request has been received. We will process it within 45 days.", "success")
        return redirect(url_for("views.do_not_sell"))
    return render_template("legal/do_not_sell.html", last_updated=LAST_UPDATED_TEXT)


@views.route("/legal/do-not-share", methods=["GET", "POST"])
def do_not_share():
    if request.method == "POST":
        flash("Your request to limit sharing has been received. We will confirm via email.", "success")
        return redirect(url_for("views.do_not_share"))
    return render_template("legal/do_not_share.html", last_updated=LAST_UPDATED_TEXT)


@views.route("/about")
def about_page():
    return render_template("site/about.html")


@views.route("/contact", methods=["GET", "POST"])
def contact_page():
    if request.method == "POST":
        form_kind = (request.form.get("form_kind") or "").strip().lower()
        if form_kind == "site_request":
            title = (request.form.get("request_title") or "").strip()
            details = (request.form.get("request_details") or "").strip()
            requester_name = (request.form.get("requester_name") or "").strip() or None
            requester_email = (request.form.get("requester_email") or "").strip() or None
            raw_type = (request.form.get("request_type") or SiteRequest.TYPE_BUG).strip().lower()
            request_type = raw_type if raw_type in SiteRequest.TYPES else SiteRequest.TYPE_BUG
            if not title or not details or not requester_email:
                flash("Please add a title, details, and contact email for your request.", "warning")
            else:
                new_request = SiteRequest(
                    title=title,
                    details=details,
                    request_type=request_type,
                    requester_name=requester_name,
                    requester_email=requester_email,
                    status=SiteRequest.STATUS_NOT_STARTED,
                )
                db.session.add(new_request)
                _safe_commit()
                flash("Thanks! Your request is now queued for the admin team.", "success")
            return redirect(url_for("views.contact_page"))

        name = (request.form.get("name") or "").strip()
        email = (request.form.get("email") or "").strip()
        message = (request.form.get("message") or "").strip()
        if not name or not email or not message:
            flash("Please fill out all fields before submitting.", "warning")
        else:
            flash("Thanks for reaching out! We'll reply soon.", "success")
        return redirect(url_for("views.contact_page"))
    return render_template("site/contact.html")
