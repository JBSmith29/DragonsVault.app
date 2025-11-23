"""Dashboard landing page and lightweight API endpoints."""

from __future__ import annotations

from flask import jsonify, redirect, render_template, session, url_for
from sqlalchemy import func
from sqlalchemy.orm import load_only

from extensions import cache, db
from models import Card, Folder
from services import scryfall_cache as sc
from services.scryfall_cache import ensure_cache_loaded, set_name_for_code
from services.commander_utils import primary_commander_oracle_id, split_commander_oracle_ids
from services.symbols_cache import ensure_symbols_cache, render_mana_html, render_oracle_html

from .base import _collection_metadata, _lookup_print_data, color_identity_name, views


@cache.memoize(timeout=60)
def _dashboard_card_stats(user_key: str, collection_ids: tuple[int, ...], collection_lower: tuple[str, ...]) -> dict:
    """
    Aggregate collection-wide stats. Memoized per user to avoid repeated table scans.
    """
    _ = user_key  # ensure cache key scopes to the requesting user
    totals = (
        db.session.query(
            func.count(Card.id),
            func.coalesce(func.sum(Card.quantity), 0),
            func.count(func.distinct(Card.name)),
            func.count(func.distinct(func.lower(Card.set_code))),
        )
        .filter(Card.is_proxy.is_(False))
        .one()
    )
    total_rows, total_qty, unique_names, set_count = totals

    collection_qty = 0
    if collection_ids:
        collection_qty = (
            db.session.query(func.coalesce(func.sum(Card.quantity), 0))
            .filter(Card.folder_id.in_(collection_ids))
            .filter(Card.is_proxy.is_(False))
            .scalar()
            or 0
        )
    elif collection_lower:
        collection_qty = (
            db.session.query(func.coalesce(func.sum(Card.quantity), 0))
            .join(Folder, Card.folder_id == Folder.id)
            .filter(func.lower(Folder.name).in_(collection_lower))
            .filter(Card.is_proxy.is_(False))
            .scalar()
            or 0
        )

    return {
        "rows": int(total_rows or 0),
        "qty": int(total_qty or 0),
        "unique_names": int(unique_names or 0),
        "sets": int(set_count or 0),
        "collection_qty": int(collection_qty or 0),
    }


def _prefetch_commander_cards(folder_map: dict[int, Folder]) -> dict[int, Card]:
    """
    Pull all commander print candidates for the provided folders in one query
    to avoid per-deck lookups.
    """
    wanted: dict[int, set[str]] = {}
    oracle_pool: set[str] = set()
    for fid, folder in folder_map.items():
        ids = {oid.strip().lower() for oid in split_commander_oracle_ids(folder.commander_oracle_id) if oid.strip()}
        if ids:
            wanted[fid] = ids
            oracle_pool.update(ids)
    if not oracle_pool:
        return {}

    rows = (
        Card.query.options(
            load_only(
                Card.id,
                Card.name,
                Card.set_code,
                Card.collector_number,
                Card.oracle_id,
                Card.quantity,
                Card.folder_id,
            )
        )
        .filter(Card.folder_id.in_(wanted.keys()))
        .filter(Card.oracle_id.isnot(None))
        .filter(func.lower(Card.oracle_id).in_(oracle_pool))
        .order_by(Card.folder_id.asc(), Card.quantity.desc(), Card.id.asc())
        .all()
    )

    commander_cards: dict[int, Card] = {}
    for card in rows:
        fid = card.folder_id
        oid = (card.oracle_id or "").strip().lower()
        if fid in wanted and oid in wanted[fid] and fid not in commander_cards:
            commander_cards[fid] = card
    return commander_cards


@views.route("/")
def index():
    """Landing route that always forwards to the dashboard summary."""
    return redirect(url_for("views.dashboard"))


@views.route("/dashboard")
def dashboard():
    """Render the high-level collection overview tiles and deck summaries."""
    collection_ids, _collection_names, collection_lower = _collection_metadata()

    stats_key = str(session.get("_user_id") or "anon")
    stats = _dashboard_card_stats(stats_key, tuple(sorted(collection_ids)), tuple(sorted(collection_lower)))
    total_rows = stats["rows"]
    total_qty = stats["qty"]
    unique_names = stats["unique_names"]
    set_count = stats["sets"]

    _ = ensure_cache_loaded()
    # Decks (folders NOT in excluded)
    deck_query = (
        db.session.query(
            Folder.id,
            Folder.name,
            func.coalesce(func.sum(Card.quantity), 0).label("qty"),
        )
        .outerjoin(Card, Card.folder_id == Folder.id)
        .filter(
            func.coalesce(Folder.category, Folder.CATEGORY_DECK) != Folder.CATEGORY_COLLECTION,
        )
    )
    if collection_lower:
        deck_query = deck_query.filter(~func.lower(Folder.name).in_(collection_lower))
    deck_rows = (
        deck_query.group_by(Folder.id, Folder.name)
        .order_by(func.coalesce(func.sum(Card.quantity), 0).desc(), Folder.name.asc())
        .all()
    )
    decks = [{"id": rid, "name": rname, "qty": int(rqty or 0)} for (rid, rname, rqty) in deck_rows]
    deck_count = len(decks)

    # Collection totals (excluded buckets)
    collection_qty = stats["collection_qty"]

    # Commander presenter + folder color identity
    dashboard_cmdr = {}
    deck_ci_letters, deck_ci_name, deck_ci_html = {}, {}, {}

    if decks:
        folder_ids = [d["id"] for d in decks]
        folder_map = {f.id: f for f in Folder.query.filter(Folder.id.in_(folder_ids)).all()}
        commander_cards = _prefetch_commander_cards(folder_map)

        def ci_letters_from_print(pr: dict) -> str:
            ci_list = (pr or {}).get("color_identity") or []
            return "".join([c for c in "WUBRG" if c in ci_list])

        def ci_html_from_letters(letters: str) -> str:
            if not letters:
                return '<span class="pip-row"><img class="mana mana-sm" src="/static/symbols/C.svg" alt="{C}"></span>'
            return (
                '<span class="pip-row">'
                + "".join(
                    f'<img class="mana mana-sm" src="/static/symbols/{c}.svg" alt="{{{c}}}">' for c in letters
                )
                + "</span>"
            )

        def image_urls_from_print(pr: dict) -> tuple[str | None, str | None]:
            """Extract commander art URLs regardless of single/double-faced layouts."""
            if not pr:
                return None, None
            iu = pr.get("image_uris") or {}
            if iu:
                return (
                    iu.get("small") or iu.get("normal") or iu.get("large"),
                    iu.get("normal") or iu.get("large") or iu.get("small"),
                )
            faces = pr.get("card_faces") or []
            if faces:
                fiu = (faces[0] or {}).get("image_uris") or {}
                return (
                    fiu.get("small") or fiu.get("normal") or fiu.get("large"),
                    fiu.get("normal") or fiu.get("large") or fiu.get("small"),
                )
            return None, None

        for fid in folder_ids:
            f = folder_map.get(fid)
            cmd_name, small, large = None, None, None
            alt = ""

            # Commander art: owned print in this folder
            pr = None
            cmd_card = None
            if f and getattr(f, "commander_oracle_id", None):
                cmd_card = commander_cards.get(fid)
                if cmd_card:
                    cmd_name = getattr(f, "commander_name", None) or cmd_card.name
                    alt = cmd_name or "Commander"
                    pr = _lookup_print_data(
                        cmd_card.set_code, cmd_card.collector_number, cmd_card.name, cmd_card.oracle_id
                    )

            # Fallback: use saved commander metadata even if the deck list lacks the print.
            if not pr and f and (f.commander_oracle_id or f.commander_name):
                pr = _lookup_print_data(
                    getattr(f, "commander_set_code", None),
                    getattr(f, "commander_collector_number", None),
                    getattr(f, "commander_name", None),
                    primary_commander_oracle_id(getattr(f, "commander_oracle_id", None)),
                )
                if not cmd_name:
                    cmd_name = getattr(f, "commander_name", None) or (pr or {}).get("name")
                if not alt:
                    alt = cmd_name or (pr or {}).get("name") or "Commander"

            if pr and (small is None and large is None):
                small, large = image_urls_from_print(pr)

            dashboard_cmdr[fid] = {
                "name": cmd_name,
                "small": small,
                "large": large,
                "alt": alt or (cmd_name or "Commander"),
            }

            letters = ci_letters_from_print(pr or {})
            deck_ci_letters[fid] = letters
            deck_ci_name[fid] = color_identity_name(letters)
            deck_ci_html[fid] = ci_html_from_letters(letters)

    return render_template(
        "decks/dashboard.html",
        stats={
            "rows": int(total_rows),
            "qty": int(total_qty),
            "unique_names": int(unique_names),
            "decks": int(deck_count),
            "collection_qty": int(collection_qty),
            "sets": int(set_count),
        },
        decks=decks,
        dashboard_cmdr=dashboard_cmdr,
        deck_ci_letters=deck_ci_letters,
        deck_ci_name=deck_ci_name,
        deck_ci_html=deck_ci_html,
    )


@views.route("/api/card/<int:card_id>")
def api_card(card_id):
    """Lightweight JSON used by hover/quick-view."""
    ensure_symbols_cache(force=False)

    card = Card.query.get_or_404(card_id)
    have_cache = ensure_cache_loaded()

    # Representative print
    best = _lookup_print_data(card.set_code, card.collector_number, card.name, card.oracle_id) if have_cache else {}

    def _img(obj):
        if not obj:
            return {"small": None, "normal": None, "large": None}
        iu = obj.get("image_uris") or {}
        if iu:
            return {
                "small": iu.get("small"),
                "normal": iu.get("normal"),
                "large": iu.get("large") or iu.get("png"),
            }
        faces = obj.get("card_faces") or []
        if faces:
            iu2 = (faces[0] or {}).get("image_uris") or {}
            return {
                "small": iu2.get("small"),
                "normal": iu2.get("normal"),
                "large": iu2.get("large") or iu2.get("png"),
            }
        return {"small": None, "normal": None, "large": None}

    def _oracle_text(obj):
        if not obj:
            return None
        faces = obj.get("card_faces") or []
        if faces:
            parts = [f.get("oracle_text") for f in faces if f.get("oracle_text")]
            return " // ".join(parts) if parts else None
        return obj.get("oracle_text")

    raw_name = (best or {}).get("name") or card.name
    raw_mana = (best or {}).get("mana_cost")
    raw_text = _oracle_text(best)

    info = {
        "name": raw_name,
        "mana_cost": raw_mana,
        "mana_cost_html": render_mana_html(raw_mana, use_local=False),
        "type_line": (best or {}).get("type_line"),
        "oracle_text": raw_text,
        "oracle_text_html": render_oracle_html(raw_text, use_local=False),
        "colors": (best or {}).get("colors") or [],
        "color_identity": (best or {}).get("color_identity") or [],
        "rarity": (best or {}).get("rarity"),
        "set": (best or {}).get("set") or (card.set_code or ""),
        "collector_number": (best or {}).get("collector_number") or card.collector_number,
        "scryfall_uri": (best or {}).get("scryfall_uri"),
        "scryfall_set_uri": (best or {}).get("scryfall_set_uri"),
        "cmc": (best or {}).get("cmc"),
        "set_name": (best or {}).get("set_name") or (set_name_for_code(card.set_code) if have_cache else None),
        "legalities": (best or {}).get("legalities") or {},
        "commander_legality": ((best or {}).get("legalities") or {}).get("commander"),
    }

    images = []
    im = _img(best)
    images.append({"small": im["small"], "normal": im["normal"], "large": im["large"]})
    info["scryfall_id"] = (best or {}).get("id")

    return jsonify(
        {
            "card": {"id": card.id, "quantity": card.quantity, "folder": card.folder.name if card.folder else None},
            "info": info,
            "images": images,
        }
    )


# --- Faces helper ------------------------------------------------------------
def _faces_from_scry_json(j):
    faces = []
    if not j:
        return faces
    if j.get("card_faces"):
        for f in j["card_faces"]:
            u = f.get("image_uris") or {}
            faces.append({"large": u.get("large"), "normal": u.get("normal"), "small": u.get("small")})
    else:
        u = j.get("image_uris") or {}
        if u:
            faces.append({"large": u.get("large"), "normal": u.get("normal"), "small": u.get("small")})
    # filter out Nones and de-dup while preserving order
    out = []
    seen = set()
    for f in faces:
        key = (f.get("large"), f.get("normal"), f.get("small"))
        if key in seen:
            continue
        seen.add(key)
        out.append(f)
    return out


@views.route("/api/print/<sid>/faces", methods=["GET"])
def api_print_faces(sid):
    """Provide client-side render helpers with the available image faces for a print."""
    data = None
    try:
        if ensure_cache_loaded():
            from services.scryfall_cache import get_print_by_id

            data = get_print_by_id(sid)
    except Exception:
        data = None

    if data is None:
        # Fallback direct fetch (only if you allow outbound HTTP in dev)
        try:
            import requests

            r = requests.get(f"https://api.scryfall.com/cards/{sid}", timeout=6)
            if r.ok:
                data = r.json()
        except Exception:
            pass

    return jsonify({"faces": _faces_from_scry_json(data)})


__all__ = ["api_card", "api_print_faces", "dashboard", "index"]
