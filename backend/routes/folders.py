"""Folder (deck) detail views and commander management endpoints."""

from __future__ import annotations

import math
import re
import secrets
from collections import Counter
from functools import lru_cache
from typing import Any, Dict, List, Set, Optional, Sequence
from urllib.parse import quote_plus

from flask import abort, flash, jsonify, redirect, render_template, request, url_for, current_app, session
from flask_login import login_required, current_user
from sqlalchemy import case, func
from sqlalchemy.orm import load_only

from extensions import cache, db, limiter
from models import Card, Folder, FolderShare, User
from services import scryfall_cache as sc
from services.scryfall_cache import cache_epoch, cache_ready, ensure_cache_loaded, find_by_set_cn, prints_for_oracle, unique_oracle_by_name
from services.commander_cache import compute_bracket_signature, get_cached_bracket, store_cached_bracket
from services.commander_utils import (
    CommanderSlot,
    MAX_COMMANDERS,
    merge_slots,
    primary_commander_name,
    primary_commander_oracle_id,
    slots_from_blobs,
    slots_from_payload,
    split_commander_oracle_ids,
)
from services.symbols_cache import colors_to_icons, render_mana_html
from services.deck_tags import DECK_TAG_GROUPS, TAG_CATEGORY_MAP, VALID_DECK_TAGS
from services.commander_brackets import (
    GAME_CHANGERS,
    BRACKET_REFERENCE,
    BRACKET_REFERENCE_BY_LEVEL,
    BRACKET_RULESET_EPOCH,
    SPELLBOOK_EARLY_COMBOS,
    SPELLBOOK_LATE_COMBOS,
    SPELLBOOK_RESULT_LABELS,
    evaluate_commander_bracket,
    spellbook_dataset_epoch,
)
from services.spellbook_sync import EARLY_MANA_VALUE_THRESHOLD, LATE_MANA_VALUE_THRESHOLD
from services.authz import ensure_folder_access
from utils.db import get_or_404

from .base import (
    _bulk_print_lookup,
    _commander_candidates_for_folder,
    _collector_number_numeric,
    _folder_id_name_map,
    _name_sort_expr,
    _prices_for_print,
    _safe_commit,
    compute_folder_color_identity,
    limiter_key_user_or_ip,
    views,
)


def _folder_name_exists_excluding(name: str, exclude_id: int | None = None) -> bool:
    normalized = (name or "").strip().lower()
    if not normalized:
        return False
    query = Folder.query.filter(func.lower(Folder.name) == normalized)
    if exclude_id:
        query = query.filter(Folder.id != exclude_id)
    return db.session.query(query.exists()).scalar()


def _generate_unique_folder_name_for_folder(base_name: str, *, exclude_id: int | None = None) -> str:
    candidate = base_name
    suffix = 2
    while _folder_name_exists_excluding(candidate, exclude_id):
        candidate = f"{base_name} ({suffix})"
        suffix += 1
    return candidate


def _commander_slots(folder: Folder) -> List[CommanderSlot]:
    return slots_from_blobs(folder.commander_name, folder.commander_oracle_id)


def _slot_from_values(name: Optional[str], oracle_id: Optional[str]) -> CommanderSlot | None:
    cleaned_name = (name or "").strip()
    cleaned_id = (oracle_id or "").strip()
    if not cleaned_name and not cleaned_id:
        return None
    return CommanderSlot(name=cleaned_name or None, oracle_id=cleaned_id or None)


def _apply_commander_update(
    folder: Folder,
    new_slots: Sequence[CommanderSlot],
    *,
    mode: str = "replace",
) -> tuple[bool, Optional[str]]:
    normalized_mode = "append" if (mode or "").strip().lower() == "append" else "replace"
    existing_slots = _commander_slots(folder)
    active_existing = [slot for slot in existing_slots if slot.name or slot.oracle_id]
    if normalized_mode == "append" and len(active_existing) >= MAX_COMMANDERS:
        return False, f"Up to {MAX_COMMANDERS} commanders can be assigned to a deck."
    name_blob, oracle_blob, normalized = merge_slots(
        existing_slots,
        new_slots,
        mode=normalized_mode,
        limit=MAX_COMMANDERS,
    )
    if not normalized:
        return False, "Commander details are missing."
    folder.commander_name = name_blob
    folder.commander_oracle_id = oracle_blob
    return True, None

_MASS_LAND_FEATURED = [
    "Vorinclex, Voice of Hunger",
    "Hall of Gemstone",
    "Contamination",
    "Cataclysm",
    "Dimensional Breach",
    "Epicenter",
    "Global Ruin",
    "Hokori, Dust Drinker",
    "Razia's Purification",
    "Rising Waters",
    "Soulscour",
    "Sunder",
    "Apocalypse",
    "Bearer of the Heavens",
    "Conversion",
    "Glaciers",
    "Pox",
    "Death Cloud",
    "Tangle Wire",
    "Restore Balance",
    "Realm Razer",
    "Spreading Algae",
    "Numot, the Devastator",
    "Giltleaf Archdruid",
    "Kudzu",
    "Demonic Hordes",
    "Urza's Sylex",
    "Infernal Darkness",
    "Trinisphere",
    "Worldfire",
    "Worldslayer",
    "Worldpurge",
    "Stasis",
]

_EXTRA_TURN_CHAINERS = [
    "Time Warp",
    "Temporal Manipulation",
    "Walk the Aeons",
    "Capture of Jingzhou",
    "Expropriate",
    "Time Stretch",
    "Nexus of Fate",
    "Timestream Navigator",
    "Sage of Hours",
    "Lighthouse Chronologist",
    "Time Sieve",
]

@lru_cache(maxsize=1024)
def _commander_card_snapshot(name: str, epoch: int) -> Dict[str, Any]:
    """Resolve and cache lightweight Scryfall details for reference cards."""
    _ = epoch  # ensure cache key includes the Scryfall cache generation
    try:
        oracle_id = unique_oracle_by_name(name)
    except Exception:
        oracle_id = None

    pr = None
    if oracle_id:
        try:
            prints = prints_for_oracle(oracle_id) or ()
        except Exception:
            prints = ()
        if prints:
            pr = prints[0]

    scryfall_id = None
    scryfall_uri = None
    set_code = None
    set_name = None
    collector_number = None
    thumb = None
    hover = None

    if pr:
        scryfall_id = pr.get("id")
        scryfall_uri = pr.get("scryfall_uri")
        set_code = pr.get("set")
        set_name = pr.get("set_name")
        collector_number = pr.get("collector_number")
        iu = pr.get("image_uris") or {}
        hover = iu.get("large") or iu.get("normal") or iu.get("small")
        thumb = iu.get("small") or iu.get("normal") or hover

    return {
        "name": name,
        "oracle_id": oracle_id,
        "scryfall_id": scryfall_id,
        "scryfall_uri": scryfall_uri,
        "set": set_code,
        "set_name": set_name,
        "collector_number": collector_number,
        "hover": hover,
        "thumb": thumb,
    }


def _folder_detail_impl(folder_id: int, *, allow_shared: bool = False, share_token: str | None = None):
    """
    Deck/folder detail with:
      • commander thumbnail (owned printing)
      • color identity, mana pips/production, curve
      • tokens inferred
      • deck table with sorting & images
    """
    folder = get_or_404(Folder, folder_id)
    ensure_folder_access(folder, write=False if not allow_shared else False, allow_shared=allow_shared, share_token=share_token)
    commander_candidates = _commander_candidates_for_folder(folder_id)
    owner_name_options = sorted(
        {
            (name or "").strip()
            for (name,) in db.session.query(Folder.owner).filter(Folder.owner.isnot(None)).all()
            if (name or "").strip()
        },
        key=lambda val: val.lower(),
    )
    current_owner_default = None
    try:
        if current_user.is_authenticated:
            current_owner_default = (current_user.username or current_user.email or "").strip() or None
    except Exception:
        current_owner_default = None
    if current_owner_default and current_owner_default not in owner_name_options:
        owner_name_options = [current_owner_default] + owner_name_options

    sort = (request.args.get("sort") or "").strip().lower()
    direction = (request.args.get("dir") or "asc").strip().lower()
    reverse = direction == "desc"

    total_rows, total_qty = (
        db.session.query(func.count(Card.id), func.coalesce(func.sum(Card.quantity), 0))
        .filter(Card.folder_id == folder_id)
        .one()
    )

    have_cache = cache_ready() or ensure_cache_loaded()

    BASE_TYPES = ["Artifact", "Battle", "Creature", "Enchantment", "Instant", "Land", "Planeswalker", "Sorcery"]

    def parse_base_types(type_line: str):
        if not type_line:
            return []
        return [t for t in BASE_TYPES if t in type_line]

    mana_pip_counts = {"W": 0, "U": 0, "B": 0, "R": 0, "G": 0}
    production_counts = {"W": 0, "U": 0, "B": 0, "R": 0, "G": 0, "C": 0}
    type_counts = {t: 0 for t in BASE_TYPES}
    curve_bins = {"0": 0, "1": 0, "2": 0, "3": 0, "4": 0, "5": 0, "6": 0, "7+": 0}
    tokens_by_key = {}

    def _joined_oracle_text(pr: dict | None) -> str:
        if not pr:
            return ""
        parts = []
        txt = pr.get("oracle_text")
        if txt:
            parts.append(txt)
        for face in pr.get("card_faces") or []:
            ft = (face or {}).get("oracle_text")
            if ft:
                parts.append(ft)
        return " // ".join(p for p in parts if p)

    rows = (
        db.session.query(
            Card.id,
            Card.name,
            Card.set_code,
            Card.collector_number,
            Card.oracle_id,
            Card.lang,
            Card.is_foil,
            Card.folder_id,
            func.coalesce(Card.quantity, 0).label("qty"),
        )
        .filter(Card.folder_id == folder_id)
        .all()
    )

    folder_names = _folder_id_name_map()
    RE_COST_SYMBOL = re.compile(r"\{([^}]+)\}")

    def add_colored_pips_from_cost_string(cost_str: str, qty: int):
        if not cost_str:
            return
        for sym in RE_COST_SYMBOL.findall(cost_str):
            s_val = sym.upper()
            for ch in ("W", "U", "B", "R", "G"):
                if ch in s_val:
                    mana_pip_counts[ch] += qty

    def is_permanent_type(tline: str) -> bool:
        t_val = tline or ""
        return any(k in t_val for k in ("Land", "Artifact", "Creature", "Enchantment", "Planeswalker"))

    def colors_from_produced_mana(produced_mana) -> set:
        out = set()
        for s_val in (produced_mana or []):
            u_val = str(s_val).upper()
            if u_val in ("W", "U", "B", "R", "G", "C"):
                out.add(u_val)
        return out

    def colors_from_oracle_text_add(text: str) -> set:
        out = set()
        if not text:
            return out
        for sym in RE_COST_SYMBOL.findall(text):
            symbol = sym.upper()
            if "ADD" in text.upper():
                for ch in ("W", "U", "B", "R", "G", "C"):
                    if ch in symbol:
                        out.add(ch)
        if "any color" in text.lower():
            out.update({"W", "U", "B", "R", "G"})
        return out

    bracket_cards: List[Dict[str, Any]] = []

    for cid, name, scode, cn, oid, lang, is_foil, fid, qty in rows:
        qty = int(qty or 0) or 1

        p = None
        bracket_card = None
        if have_cache:
            try:
                if oid:
                    prs = prints_for_oracle(oid) or []
                    p = prs[0] if prs else None
                if not p:
                    p = find_by_set_cn(scode, cn, name)
            except Exception:
                p = None
        if p:
            bracket_card = {
                "name": sc.display_name_for_print(p) if hasattr(sc, "display_name_for_print") else p.get("name") or name,
                "type_line": sc.type_label_for_print(p) if hasattr(sc, "type_label_for_print") else p.get("type_line") or "",
                "oracle_text": _joined_oracle_text(p),
                "mana_value": p.get("cmc"),
                "quantity": qty,
                "mana_cost": p.get("mana_cost"),
                "produced_mana": p.get("produced_mana"),
                "game_changer": bool(p.get("game_changer")),
            }
        else:
            bracket_card = {
                "name": name,
                "type_line": "",
                "oracle_text": "",
                "mana_value": None,
                "quantity": qty,
                "game_changer": False,
            }

        tline = (p or {}).get("type_line") or ""

        for bt in parse_base_types(tline):
            type_counts[bt] += qty

        is_land = "Land" in tline

        if not is_land and p:
            mana_candidates = []
            if p.get("mana_cost"):
                mana_candidates.append(p.get("mana_cost"))
            for face in (p.get("card_faces") or []):
                if face.get("mana_cost"):
                    mana_candidates.append(face.get("mana_cost"))
            for mana_str in mana_candidates:
                add_colored_pips_from_cost_string(mana_str or "", qty)

        if not is_land and p:
            cmc = p.get("cmc")
            bucket = "0"
            if cmc is None:
                bucket = "0"
            else:
                try:
                    value = int(round(float(cmc)))
                except Exception:
                    value = None
                if value is None:
                    bucket = "0"
                else:
                    if value < 0:
                        value = 0
                    bucket = str(value) if value <= 6 else "7+"
            curve_bins[bucket] += qty

        if p:
            produced_mana = p.get("produced_mana") or []
            colors = set()
            if produced_mana:
                colors |= colors_from_produced_mana(produced_mana)
            else:
                text = (p.get("oracle_text") or "")
                for face in (p.get("card_faces") or []):
                    if face.get("oracle_text"):
                        text += " // " + face.get("oracle_text")
                colors |= colors_from_oracle_text_add(text)
            if colors and is_permanent_type(tline):
                for ch in colors:
                    if ch in production_counts:
                        production_counts[ch] += qty

        if not p or not have_cache:
            continue

        toks = []
        if hasattr(sc, "tokens_from_print"):
            try:
                toks = sc.tokens_from_print(p) or []
            except Exception:
                toks = []
        else:
            text = (p.get("oracle_text") or "")
            faces = p.get("card_faces") or []
            if faces:
                text = " // ".join([text] + [face.get("oracle_text") or "" for face in faces])
            if re.search(r"\bcreate\b.*\btoken\b", text, flags=re.IGNORECASE | re.DOTALL):
                toks = [
                    {
                        "id": None,
                        "name": "Token",
                        "type_line": "Token",
                        "images": {"small": None, "normal": None},
                    }
                ]

        src_img_url = None
        try:
            im = sc.image_for_print(p)
            src_img_url = im.get("small") or im.get("normal")
        except Exception:
            pass

        for token in toks:
            t_name = (token.get("name") or "Token").strip()
            t_line = (token.get("type_line") or "") or ""
            is_creature_token = "Creature" in t_line
            if is_creature_token:
                base_id = token.get("id") or t_name.lower()
                key = ("crea_per_source", cid, base_id)
            else:
                key = ("noncrea_by_name", t_name.lower())

            if key not in tokens_by_key:
                imgs = token.get("images") or {}
                tokens_by_key[key] = {
                    "id": token.get("id"),
                    "name": t_name,
                    "type_line": t_line,
                    "small": imgs.get("small"),
                    "normal": imgs.get("normal"),
                    "count": 0,
                    "sources": {},
                }
            tokens_by_key[key]["count"] += qty
            srcs = tokens_by_key[key]["sources"]
            if cid not in srcs:
                srcs[cid] = {"card_id": cid, "name": name, "qty": 0, "img": src_img_url}
            srcs[cid]["qty"] += qty

        if bracket_card:
            bracket_cards.append(bracket_card)

    deck_tokens = []
    for item in tokens_by_key.values():
        src_list = list(item["sources"].values())
        src_list.sort(key=lambda s_val: (s_val["name"].lower(), s_val["card_id"]))
        deck_tokens.append(
            {
                "id": item["id"],
                "name": item["name"],
                "type_line": item["type_line"],
                "small": item["small"],
                "normal": item["normal"],
                "count": item["count"],
                "sources": src_list,
            }
        )
    deck_tokens.sort(key=lambda tok: (tok["name"].lower(), tok.get("type_line") or ""))

    type_breakdown = [(t, type_counts[t]) for t in BASE_TYPES if type_counts[t] > 0]

    def _pip_src(ch):
        arr = colors_to_icons([ch], use_local=True)
        return arr[0] if arr else None

    mana_pip_dist = [(c_val, _pip_src(c_val), mana_pip_counts[c_val]) for c_val in ["W", "U", "B", "R", "G"] if mana_pip_counts[c_val] > 0]

    letters, _label = compute_folder_color_identity(folder_id)
    allowed = set(ch for ch in (letters or []) if ch in {"W", "U", "B", "R", "G"})
    allowed.add("C")
    land_mana_sources = [
        (c_val, _pip_src(c_val), production_counts[c_val])
        for c_val in ["W", "U", "B", "R", "G", "C"]
        if production_counts[c_val] > 0 and c_val in allowed
    ]

    curve_rows = []
    curve_order = ["0", "1", "2", "3", "4", "5", "6", "7+"]
    total_curve = sum(curve_bins.values()) or 1
    for bucket in curve_order:
        count = curve_bins[bucket]
        pct = int(round(100.0 * count / total_curve)) if total_curve else 0
        curve_rows.append({"label": bucket, "count": count, "pct": pct})

    name_col = _name_sort_expr()
    cn_num = _collector_number_numeric()
    cn_numeric_last = case((cn_num.is_(None), 1), else_=0)
    deck_cards = (
        Card.query.options(
            load_only(
                Card.id,
                Card.name,
                Card.set_code,
                Card.collector_number,
                Card.oracle_id,
                Card.lang,
                Card.is_foil,
                Card.folder_id,
                Card.quantity,
            )
        )
        .filter(Card.folder_id == folder_id)
        .order_by(
            name_col.asc(),
            Card.set_code.asc(),
            cn_numeric_last.asc(),
            cn_num.asc(),
            Card.collector_number.asc(),
        )
        .all()
    )

    def _price_from_print(pr, is_foil=False):
        try:
            prices = _prices_for_print(pr)
            if is_foil:
                for key in ("usd_foil", "usd", "usd_etched"):
                    value = prices.get(key)
                    if value:
                        price_val = float(str(value))
                        if price_val > 0:
                            return price_val
            else:
                for key in ("usd", "usd_foil"):
                    value = prices.get(key)
                    if value:
                        price_val = float(str(value))
                        if price_val > 0:
                            return price_val
        except Exception:
            pass
        return 0.0

    commander_ctx = None
    if not folder.is_collection:
        commander_stub = {
            "oracle_id": primary_commander_oracle_id(folder.commander_oracle_id),
            "name": primary_commander_name(folder.commander_name) or folder.commander_name,
        }
        epoch_val = cache_epoch() + BRACKET_RULESET_EPOCH + spellbook_dataset_epoch()
        signature = compute_bracket_signature(bracket_cards, commander_stub, epoch=epoch_val)
        commander_ctx = get_cached_bracket(folder.id, signature, epoch_val)
        if not commander_ctx:
            commander_ctx = evaluate_commander_bracket(bracket_cards, commander_stub)
            if folder.id:
                store_cached_bracket(folder.id, signature, epoch_val, commander_ctx)

    if not sc.cache_ready():
        sc.ensure_cache_loaded()
    image_map, display_name_map, type_line_map, rarity_map, color_icons_map = {}, {}, {}, {}, {}
    cmc_map = {}
    cmc_bucket_map: Dict[int, str] = {}
    color_letters_map = {}
    total_value_usd = 0.0
    cache_key = f"folder:{folder.id}" if getattr(folder, "id", None) else None
    print_map = _bulk_print_lookup(deck_cards, cache_key=cache_key, epoch=cache_epoch())

    commander_media: Optional[Dict[str, Any]] = None
    commander_media_list: List[Dict[str, Any]] = []

    def _assign_commander_media(print_obj: Optional[Dict[str, Any]], name_hint: Optional[str] = None):
        nonlocal commander_media
        if not print_obj:
            return
        art_uris = sc.image_for_print(print_obj) or {}
        image_src = art_uris.get("normal") or art_uris.get("small") or art_uris.get("large")
        hover_src = art_uris.get("large") or art_uris.get("normal") or art_uris.get("small")
        if not image_src and not hover_src:
            return
        media = {
            "name": name_hint or print_obj.get("name") or folder.commander_name,
            "image": image_src or hover_src,
            "hover": hover_src or image_src,
            "label": art_uris.get("label") or name_hint or folder.commander_name,
        }
        commander_media_list.append(media)
        if commander_media is None:
            commander_media = media

    commander_oracle_set = {oid.strip().lower() for oid in split_commander_oracle_ids(folder.commander_oracle_id)}

    for card in deck_cards:
        pr = print_map.get(card.id, {})

        if pr:
            im = sc.image_for_print(pr)
            image_map[card.id] = im.get("small") or im.get("normal")
        else:
            image_map[card.id] = None

        display_name = sc.display_name_for_print(pr) if pr else card.name
        display_name_map[card.id] = display_name
        type_line_map[card.id] = sc.type_label_for_print(pr) if pr else getattr(card, "type_line", None)
        rarity_map[card.id] = (pr or {}).get("rarity") or getattr(card, "rarity", None)

        cols = (pr or {}).get("color_identity") or (pr or {}).get("colors") or []
        if not cols:
            ci_val = getattr(card, "color_identity", None)
            if ci_val:
                cols = ci_val if isinstance(ci_val, (list, tuple)) else [ch for ch in str(ci_val) if ch in "WUBRG"]
        if not cols:
            cols = ["C"]

        letters_list = [str(x).upper() for x in cols if str(x).upper() in {"W", "U", "B", "R", "G"}]
        letters_norm = "".join(ch for ch in "WUBRG" if ch in set(letters_list)) if letters_list else "C"
        color_letters_map[card.id] = letters_norm
        color_icons_map[card.id] = colors_to_icons([str(x).upper() for x in cols if x], use_local=True)
        cmc_val = None
        if pr:
            try:
                raw_cmc = pr.get("cmc")
                cmc_val = float(raw_cmc) if raw_cmc is not None else None
            except (TypeError, ValueError):
                cmc_val = None
        if cmc_val is None:
            cmc_attr = getattr(card, "cmc", None)
            try:
                cmc_val = float(cmc_attr) if cmc_attr is not None else None
            except (TypeError, ValueError):
                cmc_val = None
        cmc_map[card.id] = cmc_val
        bucket = ""
        if cmc_val is not None:
            try:
                ivalue = int(cmc_val)
            except (TypeError, ValueError):
                ivalue = None
            if ivalue is not None:
                if ivalue < 0:
                    ivalue = 0
                bucket = str(ivalue) if ivalue <= 6 else "7+"
        cmc_bucket_map[card.id] = bucket

        qty = getattr(card, "quantity", 1) or 1
        is_foil = bool(getattr(card, "is_foil", False))
        price = _price_from_print(pr, is_foil=is_foil)
        total_value_usd += price * qty

        if commander_oracle_set:
            card_oracle = (getattr(card, "oracle_id", "") or "").strip().lower()
            if card_oracle and card_oracle in commander_oracle_set:
                _assign_commander_media(pr, display_name)

    if commander_media is None:
        primary_oid = primary_commander_oracle_id(folder.commander_oracle_id)
        if primary_oid:
            try:
                oracle_prints = prints_for_oracle(primary_oid) or []
            except Exception:
                oracle_prints = []
            if oracle_prints:
                _assign_commander_media(oracle_prints[0], folder.commander_name)

    if commander_media is None and folder.commander_name:
        name_hint = primary_commander_name(folder.commander_name) or folder.commander_name
        try:
            snapshot = _commander_card_snapshot(name_hint, cache_epoch())
        except Exception:
            snapshot = None
        if snapshot:
            image_src = snapshot.get("thumb") or snapshot.get("hover")
            hover_src = snapshot.get("hover") or snapshot.get("thumb")
            if image_src or hover_src:
                media = {
                    "name": snapshot.get("name") or folder.commander_name,
                    "image": image_src or hover_src,
                    "hover": hover_src or image_src,
                    "label": snapshot.get("set_name") or snapshot.get("set"),
                }
                commander_media_list.append(media)
                if commander_media is None:
                    commander_media = media

    bracket_card_links: Dict[str, int] = {}
    if commander_ctx:
        def _name_variants(name: str) -> Set[str]:
            if not name:
                return set()
            variants: Set[str] = set()
            parts = [name]
            if "//" in name:
                parts.extend([p.strip() for p in name.split("//") if p.strip()])
            for part in parts:
                clean = part.strip()
                if not clean:
                    continue
                variants.add(clean.lower())
                core = clean.split("(")[0].strip()
                if core:
                    variants.add(core.lower())
            return variants

        for card in deck_cards:
            base_name = (display_name_map.get(card.id) or card.name or "")
            for key in _name_variants(base_name):
                key = key.strip()
                if key:
                    bracket_card_links.setdefault(key, card.id)
            if card.name:
                for key in _name_variants(card.name):
                    key = key.strip()
                    if key:
                        bracket_card_links.setdefault(key, card.id)


    def _rarity_rank(rarity):
        rl = (rarity or "").lower()
        if rl in ("mythic", "mythic rare"):
            return 3
        if rl == "rare":
            return 2
        if rl == "uncommon":
            return 1
        if rl == "common":
            return 0
        return -1

    def _cn_key(cn_val):
        if cn_val is None:
            return (10**9, "")
        s_val = str(cn_val)
        digits = ""
        for ch in s_val:
            if ch.isdigit():
                digits += ch
            else:
                break
        return (int(digits) if digits else 10**9, s_val)

    if sort in {"name", "ctype", "colors", "rar", "set", "cn", "foil", "qty", "cmc"}:
        if sort == "name":
            deck_cards.sort(key=lambda x: ((display_name_map.get(x.id) or x.name or "").lower()), reverse=reverse)
        elif sort == "ctype":
            deck_cards.sort(key=lambda x: ((type_line_map.get(x.id) or x.type_line or "").lower()), reverse=reverse)
        elif sort == "colors":
            deck_cards.sort(key=lambda x: (color_letters_map.get(x.id) or "C"), reverse=reverse)
        elif sort == "rar":
            deck_cards.sort(key=lambda x: _rarity_rank(rarity_map.get(x.id) or x.rarity), reverse=reverse)
        elif sort == "set":
            deck_cards.sort(key=lambda x: ((x.set_code or "").upper()), reverse=reverse)
        elif sort == "cn":
            deck_cards.sort(key=lambda x: _cn_key(x.collector_number), reverse=reverse)
        elif sort == "foil":
            deck_cards.sort(key=lambda x: (1 if getattr(x, "is_foil", False) else 0), reverse=reverse)
        elif sort == "qty":
            deck_cards.sort(key=lambda x: (getattr(x, "quantity", 1) or 1), reverse=reverse)
        elif sort == "cmc":
            def _cmc_key(card):
                val = cmc_map.get(card.id)
                name_key = (display_name_map.get(card.id) or card.name or "").lower()
                if val is None:
                    return (1, 0.0, name_key)
                return (0, (-val if reverse else val), name_key)

            deck_cards.sort(key=_cmc_key)

    cards_link = url_for(
        "views.list_cards",
        folder=folder_id,
    )
    folder_tag_category = TAG_CATEGORY_MAP.get(folder.deck_tag)
    is_deck_folder = bool(folder and not folder.is_collection)

    return render_template(
        "decks/folder_detail.html",
        folder=folder,
        commander_candidates=commander_candidates,
        total_rows=total_rows,
        total_qty=total_qty,
        total_value_usd=total_value_usd,
        type_breakdown=type_breakdown,
        mana_pip_dist=mana_pip_dist,
        land_mana_sources=land_mana_sources,
        curve_rows=curve_rows,
        deck_tokens=deck_tokens,
        deck_cards=deck_cards,
        cards_link=cards_link,
        owner_name_options=owner_name_options,
        image_map=image_map,
        display_name_map=display_name_map,
        type_line_map=type_line_map,
        rarity_map=rarity_map,
        color_icons_map=color_icons_map,
        cmc_map=cmc_map,
        folder_names=folder_names,
        sort=sort,
        direction=direction,
        reverse=reverse,
        commander_bracket=commander_ctx,
        cmc_bucket_map=cmc_bucket_map,
        bracket_card_links=bracket_card_links,
        deck_tag_groups=DECK_TAG_GROUPS,
        folder_tag_category=folder_tag_category,
        commander_media=commander_media,
        move_targets=Folder.query.filter(
            Folder.owner_user_id == folder.owner_user_id,
            Folder.id != folder.id
        ).order_by(Folder.name).all() if folder.owner_user_id else [],
        is_deck_folder=is_deck_folder,
        commander_media_list=commander_media_list,
    )


@views.get("/commander-brackets")
@limiter.limit("30 per minute", key_func=limiter_key_user_or_ip) if limiter else (lambda f: f)
def commander_brackets_info():
    focus_level = request.args.get("focus", type=int)
    if focus_level not in BRACKET_REFERENCE_BY_LEVEL:
        focus_level = None

    if not cache_ready():
        ensure_cache_loaded()
    epoch = cache_epoch()
    game_changers = [dict(_commander_card_snapshot(name, epoch)) for name in sorted(GAME_CHANGERS)]
    mass_land_cards = [dict(_commander_card_snapshot(name, epoch)) for name in _MASS_LAND_FEATURED]
    extra_turn_cards = [dict(_commander_card_snapshot(name, epoch)) for name in _EXTRA_TURN_CHAINERS]

    return render_template(
        "decks/commander_brackets.html",
        brackets=BRACKET_REFERENCE,
        focus_level=focus_level,
        focus_entry=BRACKET_REFERENCE_BY_LEVEL.get(focus_level) if focus_level else None,
        source_url="https://magic.wizards.com/en/news/announcements/commander-brackets-beta-update-october-21-2025",
        game_changers=game_changers,
        mass_land_cards=mass_land_cards,
        extra_turn_cards=extra_turn_cards,
    )


@views.get("/commander-spellbook-combos")
def commander_spellbook_combos():
    def _card_entries(combo):
        cards = []
        reqs = getattr(combo, "requirements", {}) or {}
        for name in combo.cards or ():
            key = name.casefold()
            qty = reqs.get(key, 1)
            encoded = quote_plus(name)
            cards.append(
                {
                    "name": name,
                    "quantity": qty if qty > 1 else 1,
                    "thumb": f"https://api.scryfall.com/cards/named?format=image&version=small&exact={encoded}",
                    "hover": f"https://api.scryfall.com/cards/named?format=image&version=large&exact={encoded}",
                    "search_url": f"https://scryfall.com/search?q=%21%22{encoded}%22",
                }
            )
        return cards

    def _serialize_combo(combo):
        categories = list(combo.result_categories or [])
        raw_mana_needed = combo.mana_needed or ""
        mana_icons_html: Optional[str] = None
        mana_note: str = ""
        identity = (combo.identity or "").strip().upper()
        color_letters = [letter for letter in identity if letter]
        if isinstance(raw_mana_needed, str) and raw_mana_needed.strip():
            mana_lines = [line for line in (raw_mana_needed or "").splitlines()]
            if mana_lines:
                icons_line = mana_lines[0].strip()
                if icons_line:
                    mana_icons_html = render_mana_html(icons_line, use_local=True)
                remaining = [line.strip() for line in mana_lines[1:] if line.strip()]
                mana_note = "\n".join(remaining)
        elif raw_mana_needed:
            mana_note = str(raw_mana_needed)
        return {
            "id": combo.id,
            "cards": _card_entries(combo),
            "results": list(combo.results or []),
            "mana_value": combo.mana_value_needed if combo.mana_value_needed is not None else "-",
            "mana_icons_html": mana_icons_html,
            "mana_note": mana_note,
            "mana_needed": raw_mana_needed,
            "mana_needed": raw_mana_needed,
            "result_labels": [SPELLBOOK_RESULT_LABELS.get(cat, cat.replace("_", " ")) for cat in categories],
            "categories": categories,
            "identity": identity,
            "colors": [letter.lower() for letter in color_letters],
            "url": combo.url or f"https://commanderspellbook.com/combo/{combo.id}",
            "normalized_mana_value": getattr(combo, "normalized_mana_value", None),
        }

    early_serialized = []
    for combo in SPELLBOOK_EARLY_COMBOS:
        payload = _serialize_combo(combo)
        payload["stage_key"] = "early"
        payload["stage_label"] = "Early Game"
        early_serialized.append(payload)

    late_serialized = []
    for combo in SPELLBOOK_LATE_COMBOS:
        payload = _serialize_combo(combo)
        payload["stage_key"] = "late"
        payload["stage_label"] = "Late Game"
        late_serialized.append(payload)

    category_counts = Counter()
    for combo in SPELLBOOK_EARLY_COMBOS + SPELLBOOK_LATE_COMBOS:
        for tag in combo.result_categories or ():
            category_counts[tag] += 1

    categories = [
        {
            "key": key,
            "label": SPELLBOOK_RESULT_LABELS.get(key, key.replace("_", " ")),
            "count": count,
        }
        for key, count in sorted(category_counts.items(), key=lambda item: (-item[1], item[0]))
    ]

    totals = {
        "early": len(early_serialized),
        "late": len(late_serialized),
        "total": len(early_serialized) + len(late_serialized),
    }

    thresholds = {
        "early": EARLY_MANA_VALUE_THRESHOLD,
        "late": LATE_MANA_VALUE_THRESHOLD,
    }

    combos = early_serialized + late_serialized

    search_raw = (request.args.get("q") or "").strip()
    search_term = search_raw.lower()

    selected_stage = (request.args.get("stage") or "").lower()
    if selected_stage not in {"early", "late"}:
        selected_stage = ""

    selected_categories = [value for value in request.args.getlist("category") if value]
    category_filters = [value.lower() for value in selected_categories]

    selected_colors = [
        value.lower()
        for value in request.args.getlist("color")
        if value and value.lower() in {"w", "u", "b", "r", "g", "c"}
    ]
    color_mode = (request.args.get("color_mode") or "contains").lower()
    if color_mode not in {"contains", "exact"}:
        color_mode = "contains"

    filtered_combos: List[Dict[str, Any]] = []
    for combo in combos:
        if selected_stage and combo["stage_key"] != selected_stage:
            continue
        if category_filters:
            combo_category_keys = [cat.lower() for cat in (combo.get("categories") or [])]
            if not any(cat in combo_category_keys for cat in category_filters):
                continue
        if selected_colors:
            combo_colors = [color.lower() for color in (combo.get("colors") or [])]
            if color_mode == "exact":
                if len(combo_colors) != len(selected_colors) or set(combo_colors) != set(selected_colors):
                    continue
            else:
                if not all(color in combo_colors for color in selected_colors):
                    continue
        if search_term:
            haystack_parts: List[str] = []
            haystack_parts.extend(card["name"] for card in combo.get("cards") or [])
            haystack_parts.extend(combo.get("results") or [])
            haystack_parts.extend(combo.get("result_labels") or [])
            haystack_parts.extend(combo.get("categories") or [])
            haystack_parts.append(combo.get("stage_label") or "")
            haystack_parts.append(combo.get("mana_note") or "")
            haystack_parts.append(combo.get("mana_needed") or "")
            haystack = " ".join(part for part in haystack_parts if part).lower()
            if search_term not in haystack:
                continue
        filtered_combos.append(combo)

    filtered_totals = {
        "early": sum(1 for combo in filtered_combos if combo["stage_key"] == "early"),
        "late": sum(1 for combo in filtered_combos if combo["stage_key"] == "late"),
    }
    filtered_totals["total"] = len(filtered_combos)

    sort = request.args.get("sort") or "stage"
    allowed_sorts = {"results", "stage", "mana"}
    if sort not in allowed_sorts:
        sort = "stage"

    direction = request.args.get("direction") or "asc"
    if direction not in {"asc", "desc"}:
        direction = "asc"
    reverse = direction == "desc"

    stage_order = {"early": 0, "late": 1}

    def _stage_key(combo: Dict[str, Any]) -> Tuple[Any, ...]:
        normalized = combo.get("normalized_mana_value")
        if normalized is None:
            normalized = float("inf")
        return (
            stage_order.get(combo["stage_key"], 99),
            normalized,
            " ".join(combo.get("results") or []).lower(),
        )

    def _results_key(combo: Dict[str, Any]) -> Tuple[Any, ...]:
        key = " ".join(combo.get("results") or []).lower()
        normalized = combo.get("normalized_mana_value")
        if normalized is None:
            normalized = float("inf")
        return (
            key,
            stage_order.get(combo["stage_key"], 99),
            normalized,
        )

    def _mana_key(combo: Dict[str, Any]) -> Tuple[Any, ...]:
        normalized = combo.get("normalized_mana_value")
        if normalized is None:
            normalized = float("inf")
        return (
            normalized,
            stage_order.get(combo["stage_key"], 99),
            " ".join(combo.get("results") or []).lower(),
        )

    sort_key_map = {
        "stage": _stage_key,
        "results": _results_key,
        "mana": _mana_key,
    }

    filtered_combos.sort(key=sort_key_map[sort], reverse=reverse)

    total = filtered_totals["total"]

    per_raw = request.args.get("per") or request.args.get("per_page") or request.args.get("page_size")
    try:
        per = int(per_raw)
    except (TypeError, ValueError):
        per = 25
    per = max(5, min(per, 100))

    page = request.args.get("page", type=int) or 1
    if page < 1:
        page = 1

    pages = max(1, math.ceil(total / per)) if per else 1
    if page > pages:
        page = pages

    start_idx = (page - 1) * per if total else 0
    end_idx = start_idx + per
    page_combos = filtered_combos[start_idx:end_idx]

    display_start = start_idx + 1 if total else 0
    display_end = min(end_idx, total)

    def _build_url(**updates: Any) -> str:
        args = request.args.to_dict(flat=False)
        for key, value in updates.items():
            if value is None:
                args.pop(key, None)
            else:
                if isinstance(value, list):
                    args[key] = value
                else:
                    args[key] = [value]
        params: Dict[str, Any] = {}
        for key, values in args.items():
            if len(values) == 1:
                params[key] = values[0]
            else:
                params[key] = values
        return url_for("views.commander_spellbook_combos", **params)

    return render_template(
        "decks/spellbook_combos.html",
        combos=page_combos,
        categories=categories,
        totals=totals,
        thresholds=thresholds,
        page=page,
        pages=pages,
        per=per,
        page_start=display_start,
        page_end=display_end,
        filtered_totals=filtered_totals,
        search_query=search_raw,
        selected_stage=selected_stage,
        selected_categories=selected_categories,
        selected_colors=selected_colors,
        color_mode=color_mode,
        sort=sort,
        direction=direction,
        build_url=_build_url,
    )


@views.post("/folders/<int:folder_id>/tag/set")
@login_required
def set_folder_tag(folder_id: int):
    folder = get_or_404(Folder, folder_id)
    ensure_folder_access(folder, write=True)
    if folder.is_collection:
        message = "Tags can only be set for deck folders."
        if request.is_json:
            return jsonify({"ok": False, "error": message}), 400
        flash(message, "warning")
        return redirect(url_for("views.folder_detail", folder_id=folder_id))

    payload = request.get_json(silent=True) or {}
    tag = payload.get("tag") or request.form.get("tag") or ""
    tag = tag.strip()

    if not tag:
        message = "No tag provided."
        if request.is_json:
            return jsonify({"ok": False, "error": message}), 400
        flash(message, "danger")
        return redirect(request.referrer or url_for("views.folder_detail", folder_id=folder_id))

    if tag not in VALID_DECK_TAGS:
        message = "Invalid tag selection."
        if request.is_json:
            return jsonify({"ok": False, "error": message}), 400
        flash(message, "danger")
        return redirect(request.referrer or url_for("views.folder_detail", folder_id=folder_id))

    folder.deck_tag = tag

    # Keep the commander card present when changing tags in build decks.
    commander_name = (folder.commander_name or "").strip()
    if commander_name and (folder.category == Folder.CATEGORY_BUILD or not folder.category):
        existing_cmd = (
            Card.query.filter(Card.folder_id == folder.id, func.lower(Card.name) == func.lower(commander_name))
            .options(load_only(Card.id))
            .first()
        )
        if not existing_cmd:
            try:
                # Import lazily to avoid circular imports at module load.
                from .build import _add_card_to_folder  # type: ignore

                _add_card_to_folder(folder, commander_name)
            except Exception as exc:  # pragma: no cover - best-effort guard
                current_app.logger.warning(
                    "Unable to re-add commander card '%s' after tag update for folder %s: %s",
                    commander_name,
                    folder.id,
                    exc,
                )

    _safe_commit()

    category = TAG_CATEGORY_MAP.get(tag)
    if request.is_json:
        return jsonify({"ok": True, "tag": tag, "category": category})

    flash(f'Deck tag set to "{tag}".', "success")
    return redirect(request.referrer or url_for("views.folder_detail", folder_id=folder_id))


@views.post("/folders/<int:folder_id>/tag/clear")
@login_required
def clear_folder_tag(folder_id: int):
    folder = get_or_404(Folder, folder_id)
    ensure_folder_access(folder, write=True)
    if folder.is_collection:
        message = "Tags can only be cleared on deck folders."
        if request.is_json:
            return jsonify({"ok": False, "error": message}), 400
        flash(message, "warning")
        return redirect(url_for("views.folder_detail", folder_id=folder_id))

    folder.deck_tag = None
    _safe_commit()

    if request.is_json:
        return jsonify({"ok": True})

    flash("Deck tag cleared.", "info")
    return redirect(request.referrer or url_for("views.folder_detail", folder_id=folder_id))


@views.post("/folders/<int:folder_id>/owner/set")
@login_required
def set_folder_owner(folder_id: int):
    folder = get_or_404(Folder, folder_id)
    ensure_folder_access(folder, write=True)
    if folder.is_collection:
        message = "Owner can only be set for deck folders."
        if request.is_json:
            return jsonify({"ok": False, "error": message}), 400
        flash(message, "warning")
        return redirect(url_for("views.folder_detail", folder_id=folder_id))

    payload = request.get_json(silent=True) or {}
    owner_value = payload.get("owner")
    if owner_value is None:
        owner_value = request.form.get("owner")

    owner_value = (owner_value or "").strip()
    folder.owner = owner_value or None
    _safe_commit()

    if request.is_json:
        return jsonify({"ok": True, "owner": folder.owner})

    if owner_value:
        flash(f'Deck owner set to "{owner_value}".', "success")
    else:
        flash("Deck owner cleared.", "info")
    return redirect(request.referrer or url_for("views.folder_detail", folder_id=folder_id))


@views.post("/folders/<int:folder_id>/proxy/set")
@login_required
def set_folder_proxy(folder_id: int):
    folder = get_or_404(Folder, folder_id)
    ensure_folder_access(folder, write=True)
    if folder.is_collection:
        message = "Proxy status can only be changed on deck folders."
        if request.is_json:
            return jsonify({"ok": False, "error": message}), 400
        flash(message, "warning")
        return redirect(url_for("views.folder_detail", folder_id=folder_id))

    payload = request.get_json(silent=True) or {}
    raw_flag = payload.get("is_proxy")
    if raw_flag is None:
        raw_flag = request.form.get("is_proxy")
    desired = str(raw_flag).strip().lower() in {"1", "true", "yes", "on"}

    folder.is_proxy = desired
    _safe_commit()

    message = "Marked deck as proxy." if desired else "Marked deck as owned."
    level = "success" if desired else "info"

    if request.is_json:
        return jsonify({"ok": True, "is_proxy": desired})

    flash(message, level)
    return redirect(request.referrer or url_for("views.folder_detail", folder_id=folder_id))


@views.post("/folders/<int:folder_id>/rename")
@login_required
def rename_proxy_deck(folder_id: int):
    folder = get_or_404(Folder, folder_id)
    ensure_folder_access(folder, write=True)
    new_name = (request.form.get("new_name") or "").strip()
    if not new_name:
        flash("Deck name cannot be empty.", "warning")
        return redirect(request.referrer or url_for("views.folder_detail", folder_id=folder_id))

    final_name = new_name
    if _folder_name_exists_excluding(new_name, folder.id):
        final_name = _generate_unique_folder_name_for_folder(new_name, exclude_id=folder.id)
        flash(f'Deck name in use. Renamed to "{final_name}".', "info")

    if (folder.name or "").strip() == final_name:
        flash("Deck name unchanged.", "info")
        return redirect(request.referrer or url_for("views.folder_detail", folder_id=folder_id))

    folder.name = final_name
    _safe_commit()
    flash("Deck name updated.", "success")
    return redirect(request.referrer or url_for("views.folder_detail", folder_id=folder_id))


@views.get("/folders/<int:folder_id>/cards.json")
def folder_cards_json(folder_id):
    folder = get_or_404(Folder, folder_id)
    ensure_folder_access(folder, write=False)
    cards = (
        Card.query.filter_by(folder_id=folder.id)
        .order_by(Card.name.asc(), Card.set_code.asc(), Card.collector_number.asc())
        .all()
    )
    payload = [
        {
            "id": c.id,
            "name": c.name,
            "oracle_id": c.oracle_id,
            "set_code": (c.set_code or "").lower(),
            "collector_number": c.collector_number or "",
            "lang": (c.lang or "en").lower(),
            "is_foil": bool(c.is_foil),
            "quantity": c.quantity or 1,
        }
        for c in cards
    ]
    return jsonify(payload)

@views.get("/api/folders/<int:folder_id>/commander-candidates")
@login_required
def api_folder_commander_candidates(folder_id: int):
    folder = get_or_404(Folder, folder_id)
    ensure_folder_access(folder, write=False)
    if folder.is_collection:
        message = "Commander can only be set for deck folders."
        return jsonify({"ok": False, "error": message}), 400

    candidates = _commander_candidates_for_folder(folder_id)
    return jsonify(
        {
            "ok": True,
            "folder": {
                "id": folder.id,
                "name": folder.name,
                "commander_name": folder.commander_name,
            },
            "candidates": candidates,
        }
    )

@views.post("/folders/<int:folder_id>/set_commander")
@login_required
def set_folder_commander(folder_id: int):
    folder = get_or_404(Folder, folder_id)
    ensure_folder_access(folder, write=True)
    if folder.is_collection:
        flash("Commander can only be set for deck folders.", "warning")
        return redirect(url_for("views.folder_detail", folder_id=folder_id))

    oid = (request.form.get("oracle_id") or "").strip()
    name = (request.form.get("name") or "").strip() or None
    mode = (request.form.get("mode") or "replace").strip().lower()
    slot = _slot_from_values(name, oid)
    if not slot:
        flash("Missing commander name.", "danger")
        return redirect(url_for("views.folder_detail", folder_id=folder_id))

    updated, error = _apply_commander_update(folder, [slot], mode=mode)
    if not updated:
        flash(error or "Unable to update commander.", "danger")
        return redirect(url_for("views.folder_detail", folder_id=folder_id))

    _safe_commit()
    final_label = folder.commander_name or slot.name or "Commander"
    flash(f'Set commander for "{folder.name}" to {final_label}.', "success")
    return redirect(request.referrer or url_for("views.folder_detail", folder_id=folder_id))


@views.post("/folders/<int:folder_id>/clear_commander")
@login_required
def clear_folder_commander(folder_id: int):
    folder = get_or_404(Folder, folder_id)
    ensure_folder_access(folder, write=True)
    if folder.is_collection:
        flash("Commander can only be cleared on deck folders.", "warning")
        return redirect(url_for("views.folder_detail", folder_id=folder_id))

    folder.commander_oracle_id = None
    folder.commander_name = None
    _safe_commit()
    flash(f'Cleared commander for "{folder.name}".', "success")
    return redirect(request.referrer or url_for("views.folder_detail", folder_id=folder_id))


@views.post("/folders/<int:folder_id>/commander/set")
@login_required
def set_commander(folder_id):
    folder = get_or_404(Folder, folder_id)
    ensure_folder_access(folder, write=True)
    if folder.is_collection:
        message = "Commander can only be set for deck folders."
        if request.is_json:
            return jsonify({"ok": False, "error": message}), 400
        flash(message, "warning")
        return redirect(url_for("views.folder_detail", folder_id=folder_id))

    data = request.get_json(silent=True) or {}
    card_id = data.get("card_id") or request.form.get("card_id")
    payload_mode = data.get("mode") or request.form.get("mode")
    mode = (payload_mode or "replace").strip().lower()
    commanders_payload = data.get("commanders") if isinstance(data.get("commanders"), list) else None

    resolved_name = data.get("name") or request.form.get("name")
    resolved_oracle_id = data.get("oracle_id") or request.form.get("oracle_id")

    if card_id:
        card = Card.query.filter_by(id=int(card_id), folder_id=folder.id).first()
        if not card:
            if request.is_json:
                return jsonify({"ok": False, "error": "Card not found in this deck"}), 404
            abort(404)
        resolved_name = card.name
        if not resolved_oracle_id:
            resolved_oracle_id = card.oracle_id
            if not resolved_oracle_id:
                try:
                    found = find_by_set_cn(card.set_code, card.collector_number, card.name)
                    if found:
                        resolved_oracle_id = found.get("oracle_id")
                except Exception:
                    pass

    slots: List[CommanderSlot] = []
    if commanders_payload is not None:
        slots = slots_from_payload(commanders_payload)
    else:
        slot = _slot_from_values(resolved_name, resolved_oracle_id)
        if slot:
            slots = [slot]

    if not slots:
        error_message = "Missing commander details."
        if request.is_json:
            return jsonify({"ok": False, "error": error_message}), 400
        flash(error_message, "danger")
        return redirect(request.referrer or url_for("views.folder_detail", folder_id=folder_id))

    updated, error = _apply_commander_update(folder, slots, mode=mode)
    if not updated:
        if request.is_json:
            return jsonify({"ok": False, "error": error or "Unable to update commander."}), 400
        flash(error or "Unable to update commander.", "danger")
        return redirect(request.referrer or url_for("views.folder_detail", folder_id=folder_id))

    _safe_commit()

    if request.is_json:
        return jsonify({"ok": True, "name": folder.commander_name})
    final_label = folder.commander_name or resolved_name or "Commander"
    flash(f"Commander set to {final_label}", "success")
    return redirect(request.referrer or url_for("views.folder_detail", folder_id=folder_id))


@views.post("/folders/<int:folder_id>/commander/clear")
@login_required
def clear_commander(folder_id):
    folder = get_or_404(Folder, folder_id)
    ensure_folder_access(folder, write=True)
    if folder.is_collection:
        message = "Commander can only be cleared on deck folders."
        if request.is_json:
            return jsonify({"ok": False, "error": message}), 400
        flash(message, "warning")
        return redirect(url_for("views.folder_detail", folder_id=folder_id))

    folder.commander_name = None
    folder.commander_oracle_id = None
    _safe_commit()
    if request.is_json:
        return jsonify({"ok": True})
    flash("Commander cleared.", "info")
    return redirect(request.referrer or url_for("views.folder_detail", folder_id=folder_id))


@views.route("/folders/<int:folder_id>/sharing", methods=["GET", "POST"])
@limiter.limit("30 per minute", methods=["POST"], key_func=limiter_key_user_or_ip) if limiter else (lambda f: f)
@login_required
def folder_sharing(folder_id: int):
    folder = get_or_404(Folder, folder_id)
    ensure_folder_access(folder, write=True)

    if request.method == "POST":
        action = (request.form.get("action") or "").strip().lower()
        if action == "toggle_public":
            target_state = request.form.get("state")
            if target_state is not None:
                folder.is_public = target_state in {"1", "true", "yes", "on"}
            else:
                folder.is_public = not folder.is_public
            db.session.commit()
            flash("Public sharing enabled." if folder.is_public else "Public sharing disabled.", "success")
            return redirect(url_for("views.folder_sharing", folder_id=folder_id))
        if action == "regenerate_token":
            token = folder.ensure_share_token()
            db.session.commit()
            session["share_token_preview"] = token
            flash("Share link updated.", "success")
            return redirect(url_for("views.folder_sharing", folder_id=folder_id))
        if action == "clear_token":
            folder.revoke_share_token()
            db.session.commit()
            flash("Share link disabled.", "info")
            return redirect(url_for("views.folder_sharing", folder_id=folder_id))
        if action == "add_share":
            identifier = (request.form.get("share_identifier") or "").strip().lower()
            if not identifier:
                flash("Provide an email or username.", "warning")
            else:
                target = (
                    User.query.filter(func.lower(User.email) == identifier).first()
                    or User.query.filter(func.lower(User.username) == identifier).first()
                )
                if not target:
                    flash("No user found with that email or username.", "warning")
                elif target.id == folder.owner_user_id:
                    flash("You already own this folder.", "info")
                else:
                    existing = FolderShare.query.filter_by(folder_id=folder.id, shared_user_id=target.id).first()
                    if existing:
                        flash("That user already has access.", "info")
                    else:
                        share = FolderShare(folder_id=folder.id, shared_user_id=target.id)
                        db.session.add(share)
                        db.session.commit()
                        flash(f"Shared with {target.username or target.email}.", "success")
            return redirect(url_for("views.folder_sharing", folder_id=folder_id))
        if action == "remove_share":
            share_id = request.form.get("share_id")
            if share_id and share_id.isdigit():
                share = FolderShare.query.filter_by(id=int(share_id), folder_id=folder.id).first()
                if share:
                    db.session.delete(share)
                    db.session.commit()
                    flash("Removed access.", "info")
            return redirect(url_for("views.folder_sharing", folder_id=folder_id))

    share_entries = (
        FolderShare.query.filter(FolderShare.folder_id == folder.id)
        .join(User, User.id == FolderShare.shared_user_id)
        .order_by(func.lower(User.email))
        .all()
    )
    token = session.pop("share_token_preview", None)
    share_link = url_for("views.shared_folder_by_token", share_token=token, _external=True) if token else None
    return render_template("decks/folder_sharing.html", folder=folder, shares=share_entries, share_link=share_link)


@views.route("/folders/<int:folder_id>")
@login_required
def folder_detail(folder_id):
    return _folder_detail_impl(folder_id)


@views.get("/api/folder/<int:folder_id>/counts")
@login_required
def folder_counts(folder_id: int):
    """Return lightweight unique/quantity counts for a folder (used to refresh stats)."""
    folder = get_or_404(Folder, folder_id)
    ensure_folder_access(folder, write=False, allow_shared=True)
    unique_count, total_qty = (
        db.session.query(func.count(Card.id), func.coalesce(func.sum(Card.quantity), 0))
        .filter(Card.folder_id == folder_id)
        .one()
    )
    return jsonify({"ok": True, "unique": int(unique_count or 0), "total": int(total_qty or 0)})


@views.route("/shared/folder/<int:folder_id>")
@login_required
def shared_folder_detail(folder_id):
    return _folder_detail_impl(folder_id, allow_shared=True)


@views.route("/shared/<string:share_token>")
@login_required
def shared_folder_by_token(share_token: str):
    token_hash = Folder._hash_share_token(share_token)
    folder = Folder.query.filter(Folder.share_token_hash == token_hash).first()
    if not folder:
        folder = getattr(Folder, "share_token", None) and Folder.query.filter_by(share_token=share_token).first()
    if not folder:
        abort(404)
    return _folder_detail_impl(folder.id, allow_shared=True, share_token=share_token)


__all__ = [
    "api_folder_commander_candidates",
    "clear_commander",
    "clear_folder_commander",
    "commander_brackets_info",
    "commander_spellbook_combos",
    "folder_cards_json",
    "folder_detail",
    "folder_sharing",
    "clear_folder_tag",
    "set_folder_owner",
    "set_folder_proxy",
    "rename_proxy_deck",
    "set_commander",
    "set_folder_tag",
    "set_folder_commander",
    "shared_folder_detail",
    "shared_folder_by_token",
]
