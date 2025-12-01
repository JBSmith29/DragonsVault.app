"""Build-A-Deck workflow routes and helpers."""

from __future__ import annotations

import hashlib
import re
from collections import OrderedDict, defaultdict
from typing import Any, Dict, List, Optional, Tuple

import requests
from flask import current_app, flash, jsonify, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from extensions import db, limiter
from models import Card, Folder
from models.role import Role, SubRole
from services.deck_synergy import analyze_deck
from services.deck_tags import DECK_TAG_GROUPS
from services.scryfall_cache import cache_ready, ensure_cache_loaded, prints_for_oracle, unique_oracle_by_name
from services.commander_utils import primary_commander_oracle_id, split_commander_oracle_ids

from .base import (
    _lookup_print_data,
    _safe_commit,
    views,
    color_identity_name,
    compute_folder_color_identity,
    limiter_key_user_or_ip,
)

_FALLBACK_SET_CODE = "CSTM"
_COLOR_BIT_MAP = {"W": 1, "U": 2, "B": 4, "R": 8, "G": 16}
_COLOR_ORDER = ("W", "U", "B", "R", "G")

build_post_limit = limiter.limit("60 per minute", methods=["POST"], key_func=limiter_key_user_or_ip) if limiter else (lambda f: f)


def _folder_name_exists(name: str, *, exclude_id: Optional[int] = None) -> bool:
    query = Folder.query.filter(func.lower(Folder.name) == name.lower())
    if exclude_id:
        query = query.filter(Folder.id != exclude_id)
    return db.session.query(query.exists()).scalar()


def _load_upgrade_plan(folder_id: int) -> Dict[str, List[str]]:
    plans = session.get("upgrade_plans") or {}
    entry = plans.get(str(folder_id)) or {}
    return {
        "adds": list(entry.get("adds") or []),
        "cuts": list(entry.get("cuts") or []),
    }


def _save_upgrade_plan(folder_id: int, adds: List[str], cuts: List[str]) -> None:
    plans = session.setdefault("upgrade_plans", {})
    if adds or cuts:
        plans[str(folder_id)] = {"adds": adds, "cuts": cuts}
    else:
        plans.pop(str(folder_id), None)
    session.modified = True


def _generate_unique_folder_name(base_name: str, *, exclude_id: Optional[int] = None) -> str:
    candidate = base_name
    suffix = 2
    while _folder_name_exists(candidate, exclude_id=exclude_id):
        candidate = f"{base_name} ({suffix})"
        suffix += 1
    return candidate


_LINE_RE = re.compile(r"^\s*(\d+)?\s*x?\s*(.+?)\s*$", re.IGNORECASE)
_ROLE_KEY_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _parse_bulk_card_lines(text: str) -> List[tuple[str, int]]:
    entries: "OrderedDict[str, list[Any]]" = OrderedDict()
    for raw_line in (text or "").splitlines():
        line = (raw_line or "").strip()
        if not line:
            continue
        match = _LINE_RE.match(line)
        if not match:
            continue
        qty = int(match.group(1) or 1)
        name = match.group(2).strip()
        if not name:
            continue
        key = name.lower()
        if key in entries:
            entries[key][1] += max(qty, 1)
        else:
            entries[key] = [name, max(qty, 1)]
            entries.move_to_end(key)
    return [(value[0], value[1]) for value in entries.values()]


def _resolve_card_payload_exact(card_name: str, *, original_name: str) -> Dict[str, Any]:
    if not cache_ready():
        ensure_cache_loaded()

    oracle_id: Optional[str] = None
    try:
        oracle_id = unique_oracle_by_name(card_name)
    except Exception as exc:
        current_app.logger.debug("unique_oracle_by_name failed for %r: %s", card_name, exc)
        oracle_id = None

    prints: List[Dict[str, Any]] = []
    if oracle_id:
        try:
            prints = prints_for_oracle(oracle_id) or []
        except Exception as exc:
            current_app.logger.warning("prints_for_oracle failed for %r: %s", card_name, exc)
            prints = []

    best = _pick_preferred_print(prints)
    if not best:
        best = _fetch_named_card(card_name)
        if best and not oracle_id:
            oracle_id = best.get("oracle_id")

    if not best:
        raise ValueError(f"Could not resolve a Scryfall print for '{original_name}'.")

    resolved_name = best.get("name") or card_name
    set_code = (best.get("set") or _FALLBACK_SET_CODE).upper()
    collector_number = best.get("collector_number") or _generate_default_collector(resolved_name)
    lang = (best.get("lang") or "en").lower()
    oracle = best.get("oracle_id") or oracle_id

    return {
        "name": resolved_name,
        "set_code": set_code,
        "collector_number": str(collector_number),
        "lang": lang,
        "oracle_id": oracle,
        "type_line": best.get("type_line") or "",
        "oracle_text": best.get("oracle_text") or "",
        "commander_legality": ((best.get("legalities") or {}).get("commander") or "").lower(),
    }


def _matches_allowed_colors(item: Dict[str, Any], allowed_colors: set[str]) -> bool:
    if not allowed_colors:
        return True

    matches = item.get("matches_deck_colors")
    if matches is not None:
        return bool(matches)

    letters: List[str] = []
    value = item.get("color_identity_letters")
    if isinstance(value, str):
        letters = list(value)
    elif isinstance(value, (list, tuple)):
        letters = [str(ch) for ch in value]
    elif isinstance(value, dict):
        letters = [str(ch) for ch in value.get("letters") or []]

    if not letters:
        value = item.get("color_identity")
        if isinstance(value, str):
            letters = list(value)
        elif isinstance(value, (list, tuple)):
            letters = [str(ch) for ch in value]
        elif isinstance(value, dict):
            letters = [str(ch) for ch in value.get("letters") or []]

    if not letters:
        return True

    normalized = {str(ch).strip().upper() for ch in letters if str(ch).strip()}
    if not normalized:
        return True
    return normalized.issubset(allowed_colors)


def _pick_preferred_print(prints: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for pr in prints:
        if pr.get("digital"):
            continue
        if (pr.get("lang") or "en").lower() == "en":
            return pr
    for pr in prints:
        if (pr.get("lang") or "en").lower() == "en":
            return pr
    return prints[0] if prints else None


def _fetch_named_card(card_name: str) -> Optional[Dict[str, Any]]:
    try:
        response = requests.get(
            "https://api.scryfall.com/cards/named",
            params={"exact": card_name},
            timeout=6,
        )
        response.raise_for_status()
        return response.json()
    except Exception as exc:  # pragma: no cover - network fallback
        current_app.logger.warning("Scryfall named lookup failed for %r: %s", card_name, exc)
        return None


def _generate_default_collector(card_name: str) -> str:
    digest = hashlib.sha1(card_name.encode("utf-8")).hexdigest()
    return f"X{digest[:4].upper()}"


def _order_category_payloads(category_map: Dict[str, List[Dict[str, Any]]]) -> List[Tuple[str, List[Dict[str, Any]]]]:
    if not category_map:
        return []
    priority = [
        "High Synergy Cards",
        "Top Cards",
        "Game Changers",
        "Signature Cards",
        "New Cards",
        "Creature",
        "Creatures",
        "Artifact",
        "Artifacts",
        "Enchantment",
        "Enchantments",
        "Instant",
        "Instants",
        "Sorcery",
        "Sorceries",
        "Planeswalker",
        "Planeswalkers",
        "Land",
        "Lands",
    ]
    ordered: List[Tuple[str, List[Dict[str, Any]]]] = []
    seen: set[str] = set()
    for name in priority:
        if name in category_map and name not in seen:
            ordered.append((name, category_map[name]))
            seen.add(name)
    for name in sorted(category_map.keys()):
        if name not in seen:
            ordered.append((name, category_map[name]))
            seen.add(name)
    return ordered


def _resolve_card_payload(card_name: str) -> Dict[str, Any]:
    base = (card_name or "").strip()
    if not base:
        raise ValueError("Card name is required.")

    candidates: List[str] = []

    def _add_candidate(value: Optional[str]) -> None:
        value = (value or "").strip()
        if not value or value in candidates:
            return
        candidates.append(value)

    _add_candidate(base)

    if "//" in base:
        parts = [part.strip() for part in base.split("//") if part.strip()]
        if parts:
            _add_candidate(parts[0])
            if len(parts) > 1:
                _add_candidate(parts[1])
                _add_candidate(f"{parts[0]} // {parts[1]}")
                _add_candidate(f"{parts[0]}//{parts[1]}")

    if "/" in base:
        fragments = [frag.strip() for frag in base.split("/") if frag.strip()]
        if fragments:
            _add_candidate(fragments[0])

    if "," in base:
        _add_candidate(base.replace(",", ""))

    last_error: Optional[Exception] = None
    for candidate in candidates:
        try:
            return _resolve_card_payload_exact(candidate, original_name=base)
        except Exception as exc:
            last_error = exc
            continue

    raise ValueError(str(last_error) if last_error else f"Could not resolve a Scryfall print for '{card_name}'.")


def _add_card_to_folder(folder: Folder, card_name: str, quantity: int = 1) -> Tuple[Card, bool]:
    # Ensure the folder has a primary key so the card FK is valid.
    if folder.id is None:
        db.session.flush([folder])

    info = _resolve_card_payload(card_name)
    existing: Optional[Card] = None
    if info["oracle_id"]:
        existing = Card.query.filter_by(folder_id=folder.id, oracle_id=info["oracle_id"]).first()
    if not existing:
        existing = Card.query.filter_by(folder_id=folder.id, name=info["name"]).first()

    created = False
    if existing:
        existing.quantity = max(int(existing.quantity or 0) + max(quantity, 1), 1)
        card = existing
    else:
        card = Card(
            name=info["name"],
            set_code=info["set_code"],
            collector_number=info["collector_number"],
            folder_id=folder.id,
            oracle_id=info["oracle_id"],
            lang=info["lang"],
            is_foil=False,
            quantity=max(quantity, 1),
        )
        db.session.add(card)
        created = True

    return card, created


def _start_new_build() -> Any:
    commander_name = (request.form.get("commander_name") or "").strip()
    deck_name = (request.form.get("deck_name") or "").strip()
    deck_tag = (request.form.get("deck_tag") or "").strip()
    include_commander = "1" in request.form.getlist("include_commander")

    if not commander_name:
        flash("Commander name is required to start a build.", "warning")
        return redirect(url_for("views.build_a_deck"))

    try:
        commander_payload = _resolve_card_payload(commander_name)
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("views.build_a_deck"))

    type_line = (commander_payload.get("type_line") or "").lower()
    oracle_text = (commander_payload.get("oracle_text") or "").lower()
    legality = (commander_payload.get("commander_legality") or "").lower()
    is_legendary = "legendary" in type_line
    is_creature_or_pw = ("creature" in type_line) or ("planeswalker" in type_line)
    explicit_commander_text = "can be your commander" in oracle_text
    commander_legal = legality == "legal" or (is_legendary and is_creature_or_pw) or explicit_commander_text

    if not commander_legal:
        flash("Please choose a commander-legal card (legendary creature/planeswalker or marked as a commander).", "warning")
        return redirect(url_for("views.build_a_deck"))

    base_name = deck_name or f"Build: {commander_payload['name']}"
    folder_name = _generate_unique_folder_name(base_name)

    owner_name = None
    owner_user_id = None
    try:
        if current_user.is_authenticated:
            owner_user_id = current_user.id
            owner_name = (current_user.username or current_user.email or "").strip() or None
    except Exception:
        owner_name = None
        owner_user_id = None

    folder = Folder(
        name=folder_name,
        category=Folder.CATEGORY_BUILD,
        commander_name=commander_payload["name"],
        commander_oracle_id=commander_payload.get("oracle_id"),
        deck_tag=deck_tag or None,
        owner_user_id=owner_user_id,
        owner=owner_name,
    )
    db.session.add(folder)

    def _ensure_commander():
        if not include_commander:
            return
        with db.session.no_autoflush:
            try:
                _add_card_to_folder(folder, commander_payload["name"])
            except ValueError as exc:
                current_app.logger.warning("Unable to add commander card for %s: %s", commander_payload["name"], exc)

    _ensure_commander()

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        folder.name = _generate_unique_folder_name(folder.name)
        db.session.add(folder)
        _ensure_commander()
        db.session.commit()
    except Exception:
        _safe_commit()
    flash(f'Created build deck "{folder.name}".', "success")
    return redirect(url_for("views.build_a_deck", folder_id=folder.id))


@views.route("/build-a-deck", methods=["GET", "POST"])
@build_post_limit
def build_a_deck():
    if request.method == "POST":
        return _start_new_build()

    folder_id = request.args.get("folder_id", type=int)
    raw_load_flag = request.args.get("load_edhrec")
    load_flag = (raw_load_flag or "").strip().lower()
    load_explicit = raw_load_flag is not None
    load_requested = load_flag in {"1", "true", "yes"}
    load_edhrec = load_requested

    build_rows = (
        db.session.query(
            Folder.id,
            Folder.name,
            Folder.commander_name,
            func.coalesce(func.sum(Card.quantity), 0).label("qty"),
        )
        .outerjoin(Card, Card.folder_id == Folder.id)
        .filter(func.coalesce(Folder.category, Folder.CATEGORY_DECK) == Folder.CATEGORY_BUILD)
        .group_by(Folder.id, Folder.name, Folder.commander_name)
        .order_by(func.lower(Folder.name))
        .all()
    )
    build_decks = [
        {
            "id": fid,
            "name": name,
            "commander": commander,
            "qty": int(qty or 0),
        }
        for fid, name, commander, qty in build_rows
    ]

    if build_decks:
        folder_ids = [deck["id"] for deck in build_decks]
        folder_map = {f.id: f for f in Folder.query.filter(Folder.id.in_(folder_ids)).all()}

        def _ci_letters_from_print(pr: dict | None) -> str:
            ci_list = (pr or {}).get("color_identity") or []
            return "".join([c for c in "WUBRG" if c in ci_list])

        def _ci_icons_for_letters(letters: str) -> str:
            if not letters:
                return '<img class="mana mana-sm" src="/static/symbols/C.svg" alt="{C}">'
            return "".join(
                f'<img class="mana mana-sm" src="/static/symbols/{c}.svg" alt="{{{c}}}">' for c in letters
            )

        def _image_urls_from_print(pr: dict | None) -> tuple[str | None, str | None]:
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

        for deck in build_decks:
            fid = deck["id"]
            folder = folder_map.get(fid)
            presenter = {"name": deck.get("commander"), "small": None, "large": None, "alt": None}
            ci_letters = ""
            pr = None
            cmd_card = None

            if folder and getattr(folder, "commander_oracle_id", None):
                cmd_ids = split_commander_oracle_ids(folder.commander_oracle_id)
                if cmd_ids:
                    cmd_card = (
                        Card.query.filter(Card.folder_id == fid, Card.oracle_id.in_(cmd_ids))
                        .order_by(Card.quantity.desc())
                        .first()
                    )
                if cmd_card:
                    presenter["name"] = getattr(folder, "commander_name", None) or cmd_card.name
                    presenter["alt"] = presenter["name"] or "Commander"
                    pr = _lookup_print_data(
                        cmd_card.set_code, cmd_card.collector_number, cmd_card.name, cmd_card.oracle_id
                    )

            if not pr and folder and (folder.commander_oracle_id or folder.commander_name):
                pr = _lookup_print_data(
                    getattr(folder, "commander_set_code", None),
                    getattr(folder, "commander_collector_number", None),
                    getattr(folder, "commander_name", None),
                    primary_commander_oracle_id(getattr(folder, "commander_oracle_id", None)),
                )
                if not presenter["name"]:
                    presenter["name"] = getattr(folder, "commander_name", None) or (pr or {}).get("name")
                if not presenter["alt"]:
                    presenter["alt"] = presenter["name"] or (pr or {}).get("name") or "Commander"

            if pr:
                small, large = _image_urls_from_print(pr)
                presenter["small"] = small or presenter["small"]
                presenter["large"] = large or presenter["large"]
                ci_letters = _ci_letters_from_print(pr)

            if not ci_letters:
                letters, _label = compute_folder_color_identity(fid)
                ci_letters = letters or ""

            deck["commander_card"] = presenter
            deck["commander_hover"] = presenter.get("large")
            deck["color_identity_letters"] = ci_letters
            deck["color_identity_name"] = color_identity_name(ci_letters)
            deck["color_identity_icons"] = _ci_icons_for_letters(ci_letters)


    selected_folder: Optional[Folder] = None
    analysis: Optional[Dict[str, Any]] = None
    analysis_error: Optional[str] = None
    edhrec_errors: List[str] = []
    edhrec_loaded = False
    can_load_edhrec = False

    if folder_id:
        selected_folder = Folder.query.get(folder_id)
        if not selected_folder:
            flash("Deck not found.", "warning")
            return redirect(url_for("views.build_a_deck"))
        if selected_folder.is_collection:
            flash("Collection folders cannot be edited in Build-A-Deck.", "warning")
            return redirect(url_for("views.build_a_deck"))
        can_load_edhrec = bool(
            (selected_folder.commander_name or selected_folder.commander_oracle_id)
            and selected_folder.deck_tag
        )
        if not load_explicit:
            load_edhrec = can_load_edhrec
        else:
            load_edhrec = load_requested
        if load_explicit and load_edhrec and not can_load_edhrec:
            flash("Set both a commander and deck tag before loading EDHREC data.", "warning")
        edhrec_loaded = load_edhrec and can_load_edhrec
        try:
            analysis = analyze_deck(selected_folder.id)
        except Exception as exc:
            current_app.logger.exception("Build-A-Deck analysis failed for folder %s", selected_folder.id)
            analysis_error = str(exc)

    deck_summary = (analysis or {}).get("deck") or {}
    deck_cards = (analysis or {}).get("analysis_cards") or []
    deck_card_rows = (analysis or {}).get("deck_card_rows") or []
    role_summaries = (analysis or {}).get("roles") or []

    normalized_role_map: Dict[str, str] = {}
    seen_role_slugs: set[str] = set()
    for idx, role in enumerate(role_summaries):
        normalized = _normalize_role_key(role.get("key"), fallback=f"role-{idx}")
        base = normalized
        suffix = 2
        while normalized in seen_role_slugs:
            normalized = f"{base}-{suffix}"
            suffix += 1
        role["normalized_key"] = normalized
        seen_role_slugs.add(normalized)
        normalized_role_map[normalized] = normalized
        normalized_role_map[normalized.lower()] = normalized
        original_key = role.get("key")
        if original_key is not None:
            original_as_str = str(original_key)
            normalized_role_map[original_as_str] = normalized
            normalized_role_map[original_as_str.lower()] = normalized

    role_lookup: Dict[Any, Dict[str, Any]] = {}
    for role in role_summaries:
        original_key = role.get("key")
        if original_key is not None:
            role_lookup[original_key] = role
        normalized_key = role.get("normalized_key")
        if normalized_key:
            role_lookup[normalized_key] = role

    for idx, card in enumerate(deck_card_rows):
        roles = list(card.get("roles") or [])
        normalized_roles: List[str] = []
        for role_key in roles:
            lookup_key = ""
            if role_key is not None:
                lookup_key = str(role_key)
            normalized = normalized_role_map.get(lookup_key) or normalized_role_map.get(lookup_key.lower())
            if not normalized:
                normalized = _normalize_role_key(role_key, fallback=f"role-extra-{idx}")
            if normalized and normalized not in normalized_roles:
                normalized_roles.append(normalized)
        card["normalized_roles"] = normalized_roles

    deck_goldfish_groups: List[Dict[str, Any]] = []
    remaining_keys: set[str] = set()
    if deck_card_rows:
        def _normalize_name(value: Optional[str]) -> str:
            return str(value or "").strip().lower()

        commander_names: List[str] = []
        commander_summary = (deck_summary or {}).get("commander") if deck_summary else None
        if commander_summary and commander_summary.get("name"):
            commander_names.append(_normalize_name(commander_summary.get("name")))
        if selected_folder and selected_folder.commander_name:
            commander_names.append(_normalize_name(selected_folder.commander_name))

        type_buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for card in deck_card_rows:
            name_lower = _normalize_name(card.get("name"))
            primary_type = card.get("primary_type") or "Other"
            bucket_key = "Commander" if (name_lower and commander_names and name_lower in commander_names) else primary_type
            type_buckets[bucket_key].append(card)

        remaining_keys = set(type_buckets.keys())

        group_specs: List[Tuple[str, str, Tuple[str, ...]]] = [
            ("commander", "Commander", ("Commander",)),
            ("creatures", "Creatures", ("Creature",)),
            ("instants", "Instants", ("Instant",)),
            ("sorceries", "Sorceries", ("Sorcery",)),
            ("artifacts", "Artifacts", ("Artifact",)),
            ("enchantments", "Enchantments", ("Enchantment",)),
            ("planeswalkers", "Planeswalkers", ("Planeswalker",)),
            ("battles", "Battles", ("Battle",)),
            ("lands", "Lands", ("Land",)),
        ]

        def _goldfish_sort_key(card_row: Dict[str, Any]) -> Tuple[float, str]:
            mv = card_row.get("mana_value")
            try:
                mv_float = float(mv)
            except (TypeError, ValueError):
                mv_float = 0.0 if (card_row.get("primary_type") == "Land") else 99.0
            return mv_float, (card_row.get("name") or "").lower()

        def _sum_quantity(items: List[Dict[str, Any]]) -> int:
            total = 0
            for entry in items:
                try:
                    qty = int(entry.get("quantity") or 0)
                except (TypeError, ValueError):
                    qty = 1
                if qty <= 0:
                    qty = 1
                total += qty
            return total

        for key, label, types in group_specs:
            cards_for_group: List[Dict[str, Any]] = []
            for type_key in types:
                cards_for_group.extend(type_buckets.get(type_key, []))
                remaining_keys.discard(type_key)
            if not cards_for_group:
                continue
            cards_for_group.sort(key=_goldfish_sort_key)
            deck_goldfish_groups.append(
                {
                    "key": key,
                    "label": label,
                    "cards": cards_for_group,
                    "total": _sum_quantity(cards_for_group),
                }
            )

        leftover_cards: List[Dict[str, Any]] = []
        for rem_key in sorted(remaining_keys):
            leftover_cards.extend(type_buckets.get(rem_key, []))
        if leftover_cards:
            leftover_cards.sort(key=_goldfish_sort_key)
            deck_goldfish_groups.append(
                {
                    "key": "other",
                    "label": "Other",
                    "cards": leftover_cards,
                    "total": _sum_quantity(leftover_cards),
                }
            )

    deck_type_chart = ((analysis or {}).get("deck_type_chart") or {}).get("content") or []
    type_breakdown = (analysis or {}).get("type_breakdown") or []
    mana_pip_dist = (analysis or {}).get("mana_pip_dist") or []
    land_mana_sources = (analysis or {}).get("land_mana_sources") or []
    curve_rows = (analysis or {}).get("curve_rows") or []

    if not type_breakdown and deck_type_chart:
        type_breakdown = [(item.get("label"), item.get("value")) for item in deck_type_chart if item.get("label")]

    def _fallback_mana_pips(cards: List[Dict[str, Any]]) -> List[Tuple[str, str, int]]:
        counts: Dict[str, int] = {}
        sym_re = re.compile(r"\{([WUBRG]|C)\}", re.IGNORECASE)
        for card in cards or []:
            cost = (card.get("mana_cost") or "").upper()
            qty = max(int(card.get("quantity") or 0), 1)
            for match in sym_re.findall(cost):
                letter = match.upper()
                counts[letter] = counts.get(letter, 0) + qty
        ordered = []
        for letter in ["W", "U", "B", "R", "G", "C"]:
            val = counts.get(letter, 0)
            if val:
                ordered.append((letter, f"/static/symbols/{letter}.svg", val))
        return ordered

    def _fallback_land_sources(cards: List[Dict[str, Any]]) -> List[Tuple[str, str, int]]:
        counts: Dict[str, int] = {}
        for card in cards or []:
            primary_type = (card.get("primary_type") or "").lower()
            if "land" not in primary_type:
                continue
            qty = max(int(card.get("quantity") or 0), 1)
            colors = card.get("color_identity") or []
            if colors:
                for c in colors:
                    letter = str(c).upper()
                    counts[letter] = counts.get(letter, 0) + qty
            else:
                counts["C"] = counts.get("C", 0) + qty
        ordered = []
        for letter in ["W", "U", "B", "R", "G", "C"]:
            val = counts.get(letter, 0)
            if val:
                ordered.append((letter, f"/static/symbols/{letter}.svg", val))
        return ordered

    def _fallback_curve(cards: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        buckets = [0, 0, 0, 0, 0, 0, 0]  # 0,1,2,3,4,5,6+
        total = 0
        for card in cards or []:
            try:
                mv = float(card.get("mana_value"))
            except (TypeError, ValueError):
                continue
            qty = max(int(card.get("quantity") or 0), 1)
            total += qty
            if mv < 1:
                buckets[0] += qty
            elif mv < 2:
                buckets[1] += qty
            elif mv < 3:
                buckets[2] += qty
            elif mv < 4:
                buckets[3] += qty
            elif mv < 5:
                buckets[4] += qty
            elif mv < 6:
                buckets[5] += qty
            else:
                buckets[6] += qty
        labels = ["0", "1", "2", "3", "4", "5", "6+"]
        out: List[Dict[str, Any]] = []
        for idx, count in enumerate(buckets):
            pct = int(round((count * 100) / total)) if total else 0
            out.append({"label": labels[idx], "count": count, "pct": pct})
        return out

    if not mana_pip_dist and deck_card_rows:
        mana_pip_dist = _fallback_mana_pips(deck_card_rows)
    if not land_mana_sources and deck_card_rows:
        land_mana_sources = _fallback_land_sources(deck_card_rows)
    if not curve_rows and deck_card_rows:
        curve_rows = _fallback_curve(deck_card_rows)
    deck_list_lines: List[str] = []
    for item in deck_cards:
        name = item.get("name")
        if not name:
            continue
        try:
            qty = int(item.get("quantity") or 0)
        except (TypeError, ValueError):
            qty = 1
        if qty <= 0:
            qty = 1
        deck_list_lines.append(f"{qty} {name}")
    deck_list_text = "\n".join(deck_list_lines)

    edhrec_payload = (analysis or {}).get("edhrec") or {}
    recommended_cards = []
    coverage = {}
    theme_options = []
    tag_synergy = None
    edhrec_chart = None
    edhrec_category_rows: List[Tuple[str, List[Dict[str, Any]]]] = []

    allowed_colors: set[str] = set()
    letters_value = ""
    if selected_folder:
        letters_value = None
        ci_obj = (deck_summary.get("color_identity") if deck_summary else {}) or {}
        if isinstance(ci_obj, dict):
            letters_field = ci_obj.get("letters")
            if isinstance(letters_field, str):
                letters_value = letters_field
            elif isinstance(letters_field, (list, tuple)):
                letters_value = "".join(str(ch) for ch in letters_field)
        if not letters_value:
            letters_value, _label = compute_folder_color_identity(selected_folder.id)
        allowed_colors = {str(ch).upper() for ch in (letters_value or "") if str(ch).strip()}

    if edhrec_loaded and edhrec_payload:
        edhrec_errors = list(edhrec_payload.get("errors") or [])
        recommended_cards = (edhrec_payload.get("combined") or {}).get("missing") or []
        coverage = edhrec_payload.get("coverage") or {}
        theme_options = (edhrec_payload.get("commander") or {}).get("theme_options") or []
        tag_synergy = (analysis or {}).get("tag_synergy")
        charts = edhrec_payload.get("charts") or {}
        edhrec_chart = charts.get("type_distribution")
        category_map = edhrec_payload.get("categories") or {}
        edhrec_category_rows = _order_category_payloads(category_map)
    else:
        edhrec_errors = []

    upgrade_plan = {"adds": [], "cuts": []}
    playground_origin = None
    if selected_folder:
        upgrade_plan = _load_upgrade_plan(selected_folder.id)
        origin_map = session.get("playground_origins") or {}
        origin_id = origin_map.get(str(selected_folder.id))
        if origin_id:
            playground_origin = Folder.query.get(origin_id)

    if allowed_colors:
        if recommended_cards:
            recommended_cards = [
                item for item in recommended_cards if _matches_allowed_colors(item, allowed_colors)
            ]

        filtered_category_rows: List[Tuple[str, List[Dict[str, Any]]]] = []
        for label, items in edhrec_category_rows or []:
            filtered_items = [item for item in items if _matches_allowed_colors(item, allowed_colors)]
            if filtered_items:
                filtered_category_rows.append((label, filtered_items))
        edhrec_category_rows = filtered_category_rows

    template_name = "decks/build_a_deck_selected.html" if selected_folder else "decks/build_a_deck.html"

    hover_lookup: Dict[str, str] = {}

    def _get_attr(obj: Any, key: str) -> Any:
        if isinstance(obj, dict):
            return obj.get(key)
        return getattr(obj, key, None)

    def _collect_hover(payload: Any) -> None:
        if not payload:
            return
        if isinstance(payload, dict):
            name = (payload.get("name") or "").strip()
            hover = payload.get("hover_image") or payload.get("hover")
            if name and hover and name.lower() not in hover_lookup:
                hover_lookup[name.lower()] = hover
            for value in payload.values():
                if isinstance(value, (list, tuple, set)):
                    for item in value:
                        _collect_hover(item)
                elif isinstance(value, dict):
                    _collect_hover(value)
        elif isinstance(payload, (list, tuple, set)):
            for item in payload:
                _collect_hover(item)

    _collect_hover(_get_attr(deck_summary, "commander"))
    _collect_hover(deck_cards)
    _collect_hover(deck_card_rows)
    _collect_hover(recommended_cards)
    _collect_hover(role_summaries)
    if tag_synergy:
        _collect_hover(tag_synergy)
    for _, items in edhrec_category_rows:
        _collect_hover(items)

    deck_color_letters = letters_value or ""

    return render_template(
        template_name,
        build_decks=build_decks,
        selected_folder=selected_folder,
        deck_summary=deck_summary,
        deck_cards=deck_cards,
        deck_card_rows=deck_card_rows,
        deck_goldfish_groups=deck_goldfish_groups,
        recommended_cards=recommended_cards,
        role_summaries=role_summaries,
        role_lookup=role_lookup,
        deck_type_chart=deck_type_chart,
        deck_color_letters=deck_color_letters,
        coverage=coverage,
        theme_options=theme_options,
        tag_synergy=tag_synergy,
        analysis_error=analysis_error,
        edhrec_errors=edhrec_errors,
        edhrec_loaded=edhrec_loaded,
        can_load_edhrec=can_load_edhrec,
        edhrec_chart=edhrec_chart,
        edhrec_category_rows=edhrec_category_rows,
        deck_list_text=deck_list_text,
        type_breakdown=type_breakdown,
        mana_pip_dist=mana_pip_dist,
        land_mana_sources=land_mana_sources,
        curve_rows=curve_rows,
        disable_hx=bool(selected_folder),
        upgrade_plan=upgrade_plan,
        playground_origin=playground_origin,
        hover_lookup=hover_lookup,
        deck_tag_groups=DECK_TAG_GROUPS,
        mdfc_cards=(analysis or {}).get("mdfc_cards") or [],
    )


@views.route("/analysis/<int:folder_id>")
def deckbuilder_analysis(folder_id: int):
    folder = Folder.query.get_or_404(folder_id)

    try:
        analysis = analyze_deck(folder_id)
    except ValueError:
        return jsonify({"error": "Deck not found"}), 404
    except Exception as exc:
        current_app.logger.exception("Deck analysis failed for %s", folder_id, exc_info=True)
        return jsonify({"error": "Unable to analyze this deck"}), 500

    counts = {}
    labels = {}
    for role in analysis.get("roles") or []:
        key = str(role.get("key") or "").strip()
        label = str(role.get("label") or key).strip()
        current = int(role.get("current") or 0)
        if not key:
            continue
        counts[key] = current
        labels[key] = label

    return jsonify({"counts": counts, "labels": labels})


@views.get("/deckbuilder/search")
def deckbuilder_search():
    deck_id = request.args.get("deck_id", type=int)
    q_text = (request.args.get("q") or request.args.get("text") or "").strip()
    card_type = (request.args.get("card_type") or request.args.get("type") or "").strip()
    roles_param = (request.args.get("roles") or "").strip()
    subroles_param = (request.args.get("subroles") or "").strip()
    roles_params = request.args.getlist("roles")
    subroles_params = request.args.getlist("subroles")
    mv_min_raw = request.args.get("mv_min")
    mv_max_raw = request.args.get("mv_max")
    mv_min = request.args.get("mv_min", default=0, type=float)
    mv_max = request.args.get("mv_max", default=12, type=float)
    mv_filter_active = (mv_min_raw is not None) or (mv_max_raw is not None)
    selected_colors = [c.upper() for c in request.args.getlist("colors") if c]
    sort = (request.args.get("sort") or "name").lower()

    def _normalize_list(raw: List[str], extra: str) -> List[str]:
        values: List[str] = []
        if extra:
            values.extend(extra.split(","))
        values.extend(raw or [])
        cleaned = []
        seen = set()
        for val in values:
            item = val.strip()
            if not item or item in seen:
                continue
            cleaned.append(item)
            seen.add(item)
        return cleaned

    role_list = _normalize_list(roles_params, roles_param)
    subrole_list = _normalize_list(subroles_params, subroles_param)

    query = Card.query
    if deck_id:
        query = query.filter(Card.folder_id == deck_id)
    if role_list:
        query = query.join(Card.roles).filter(Role.label.in_(role_list))
    if subrole_list:
        query = query.join(Card.subroles).filter(SubRole.label.in_(subrole_list))
    if card_type:
        like_val = f"%{card_type.lower()}%"
        query = query.filter(func.lower(func.coalesce(Card.type_line, "")).like(like_val))
    if q_text:
        for tok in [t for t in q_text.split() if t]:
            query = query.filter(func.lower(Card.name).like(f"%{tok.lower()}%"))

    # Color filter based on color_identity_mask when available
    if selected_colors and hasattr(Card, "color_identity_mask"):
        mask = 0
        has_colorless = "C" in selected_colors
        for c in selected_colors:
            mask |= _COLOR_BIT_MAP.get(c, 0)
        if mask and has_colorless:
            query = query.filter(
                or_(
                    (Card.color_identity_mask.op("&")(mask)) == mask,
                    Card.color_identity_mask == 0,
                    Card.color_identity_mask.is_(None),
                )
            )
        elif mask:
            query = query.filter((Card.color_identity_mask.op("&")(mask)) == mask)
        if has_colorless and mask == 0:
            query = query.filter(or_(Card.color_identity_mask == 0, Card.color_identity_mask.is_(None)))

    if role_list or subrole_list:
        query = query.distinct()

    # Base ordering by name before in-memory sorting
    query = query.order_by(func.lower(Card.name))
    cards = (
        query.options(selectinload(Card.roles), selectinload(Card.subroles))
        .limit(400)
        .all()
    )

    def _colors_from_mask(mask: Optional[int]) -> str:
        if mask is None:
            return ""
        letters = [ltr for ltr in _COLOR_ORDER if mask & _COLOR_BIT_MAP[ltr]]
        return "".join(letters)

    def _meta_for_card(card: Card) -> Dict[str, Any]:
        meta = _lookup_print_data(card.set_code, card.collector_number, card.name, getattr(card, "oracle_id", None)) or {}
        mv_raw = meta.get("mana_value", meta.get("cmc"))
        mana_value = None
        try:
            mana_value = float(mv_raw)
        except (TypeError, ValueError):
            mana_value = getattr(card, "mana_value", None)
            try:
                mana_value = float(mana_value) if mana_value is not None else None
            except (TypeError, ValueError):
                mana_value = None
        color_identity = meta.get("color_identity") or meta.get("colors") or []
        colors_str = "".join(str(c or "").upper() for c in color_identity if c)
        if not colors_str:
            colors_str = getattr(card, "color_identity", None) or ""
        if not colors_str and hasattr(card, "color_identity_mask"):
            colors_str = _colors_from_mask(getattr(card, "color_identity_mask", None))
        type_line = card.type_line or meta.get("type_line") or ""
        return {"mana_value": mana_value, "colors": colors_str, "type_line": type_line}

    def _matches_colors(color_letters: str) -> bool:
        if not selected_colors:
            return True
        letters_set = set(color_letters or "")
        include_colorless = "C" in selected_colors
        non_c = [c for c in selected_colors if c != "C"]
        has_all_non_c = set(non_c).issubset(letters_set)
        if include_colorless and not non_c:
            return not letters_set
        if include_colorless and not letters_set:
            return True
        if non_c:
            return has_all_non_c
        return include_colorless or True

    results: List[Dict[str, Any]] = []
    for card in cards:
        meta = _meta_for_card(card)
        mana_value = meta["mana_value"]
        colors_str = meta["colors"]
        type_line_val = meta["type_line"]

        if mana_value is not None:
            if mana_value < mv_min or mana_value > mv_max:
                continue
        elif mv_filter_active:
            continue
        if card_type and card_type.lower() not in type_line_val.lower():
            continue
        if not _matches_colors(colors_str):
            continue
        if q_text:
            haystack = f"{card.name} {type_line_val}".lower()
            if not all(tok in haystack for tok in [t.lower() for t in q_text.split() if t]):
                continue

        role_labels = [r.label or r.key for r in (card.roles or [])]
        subrole_labels = [sr.label or sr.key for sr in (card.subroles or [])]
        primary_role = role_labels[0] if role_labels else None

        results.append(
            {
                "card": card,
                "mana_value": mana_value,
                "colors": colors_str,
                "type_line": type_line_val,
                "roles": role_labels,
                "subroles": subrole_labels,
                "primary_role": primary_role,
            }
        )

    def _sort_key(row: Dict[str, Any]):
        name_key = (row["card"].name or "").lower()
        if sort == "mv":
            mv = row.get("mana_value")
            return (mv is None, mv if mv is not None else 0, name_key)
        if sort == "color":
            return (row.get("colors") or "", name_key)
        if sort == "role":
            return ((row.get("primary_role") or "").lower(), name_key)
        return name_key

    results.sort(key=_sort_key)

    all_roles = Role.query.order_by(Role.label).all()
    all_subroles = SubRole.query.order_by(SubRole.label).all()

    return render_template(
        "deckbuilder/search.html",
        results=results,
        all_roles=all_roles,
        all_subroles=all_subroles,
        selected_roles=role_list,
        selected_subroles=subrole_list,
        selected_colors=selected_colors,
        mv_min=mv_min,
        mv_max=mv_max,
        card_type=card_type,
        q_text=q_text,
        sort=sort,
        deck_id=deck_id,
    )


@views.post("/build-a-deck/<int:folder_id>/update-commander")
@build_post_limit
def build_update_commander(folder_id: int):
    folder = Folder.query.get_or_404(folder_id)
    if folder.is_collection:
        flash("Collection folders cannot have commanders assigned here.", "warning")
        return redirect(url_for("views.build_a_deck", folder_id=folder_id))

    commander_name = (request.form.get("commander_name") or "").strip()
    include_card = "1" in request.form.getlist("include_commander")
    if not commander_name:
        flash("Commander name cannot be empty.", "warning")
        return redirect(url_for("views.build_a_deck", folder_id=folder_id))

    try:
        payload = _resolve_card_payload(commander_name)
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("views.build_a_deck", folder_id=folder_id))

    folder.commander_name = payload["name"]
    folder.commander_oracle_id = payload.get("oracle_id")

    if include_card:
        try:
            _add_card_to_folder(folder, payload["name"])
        except ValueError as exc:
            current_app.logger.warning("Unable to add commander card during update: %s", exc)

    _safe_commit()
    flash(f'Commander updated to {payload["name"]}.', "success")
    return redirect(request.referrer or url_for("views.build_a_deck", folder_id=folder_id))


@views.post("/build-a-deck/<int:folder_id>/add-card")
@build_post_limit
def build_add_card(folder_id: int):
    folder = Folder.query.get_or_404(folder_id)
    if folder.is_collection:
        flash("Cannot add cards to collection folders from Build-A-Deck.", "warning")
        return redirect(request.referrer or url_for("views.build_a_deck", folder_id=folder_id))

    card_name = (request.form.get("card_name") or "").strip()
    quantity = request.form.get("quantity", type=int) or 1

    if not card_name:
        flash("Please provide a card name.", "warning")
        return redirect(request.referrer or url_for("views.build_a_deck", folder_id=folder_id))

    try:
        card, created = _add_card_to_folder(folder, card_name, quantity=quantity)
        _safe_commit()
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(request.referrer or url_for("views.build_a_deck", folder_id=folder_id))
    except Exception as exc:  # pragma: no cover - defensive
        current_app.logger.exception("Failed to add card %r to folder %s", card_name, folder_id)
        flash(f"Unable to add {card_name}: {exc}", "danger")
        return redirect(request.referrer or url_for("views.build_a_deck", folder_id=folder_id))

    if created:
        flash(f'Added {card.name} to "{folder.name}".', "success")
    else:
        flash(f'Updated {card.name} to {card.quantity} copies.', "info")

    return redirect(request.referrer or url_for("views.build_a_deck", folder_id=folder_id))


@views.post("/build-a-deck/<int:folder_id>/queue-add")
@build_post_limit
def build_queue_add_cards(folder_id: int):
    """AJAX endpoint to add a batch of cards without reloading the page."""
    folder = Folder.query.get_or_404(folder_id)
    if folder.is_collection:
        return (
            jsonify(success=False, message="Cannot add cards to collection folders from Build-A-Deck."),
            400,
        )

    payload = request.get_json(silent=True) or {}
    entries = payload.get("cards") or []
    if not isinstance(entries, list) or not entries:
        return jsonify(success=False, message="No cards supplied."), 400

    added: list[dict[str, object]] = []
    warnings: list[str] = []
    total_qty = 0

    for raw in entries:
        name = (raw.get("card_name") or raw.get("name") or "").strip() if isinstance(raw, dict) else ""
        qty_raw = raw.get("quantity") if isinstance(raw, dict) else None
        try:
            qty = int(qty_raw)
        except (TypeError, ValueError):
            qty = 1
        qty = max(qty, 1)
        if not name:
            warnings.append("Skipped entry without a card name.")
            continue
        try:
            card, created = _add_card_to_folder(folder, name, quantity=qty)
            added.append(
                {
                    "name": card.name,
                    "requested_qty": qty,
                    "total_qty": card.quantity,
                    "created": created,
                }
            )
            total_qty += qty
        except ValueError as exc:
            warnings.append(str(exc))
        except Exception as exc:  # pragma: no cover - defensive
            current_app.logger.exception("Failed queued add for %r in folder %s", name, folder_id)
            warnings.append(f"Unable to add {name}: {exc}")

    if added:
        _safe_commit()

    status = 200 if added else 400
    message = (
        f"Queued {len(added)} card{'s' if len(added) != 1 else ''} ({total_qty} copies)."
        if added
        else "No cards were added."
    )
    return jsonify(success=bool(added), message=message, added=added, warnings=warnings), status


@views.post("/build-a-deck/<int:folder_id>/bulk-add")
@build_post_limit
def build_bulk_add_cards(folder_id: int):
    folder = Folder.query.get_or_404(folder_id)
    if folder.is_collection:
        flash("Cannot add cards to collection folders from Build-A-Deck.", "warning")
        return redirect(request.referrer or url_for("views.build_a_deck", folder_id=folder_id))

    raw = request.form.get("bulk_cards") or ""
    entries = _parse_bulk_card_lines(raw)
    if not entries:
        flash("Please enter at least one card (e.g. '3 Sol Ring').", "warning")
        return redirect(request.referrer or url_for("views.build_a_deck", folder_id=folder_id))

    success_count = 0
    total_added = 0
    warnings: list[str] = []

    for name, qty in entries:
        try:
            card, created = _add_card_to_folder(folder, name, quantity=qty)
            success_count += 1
            total_added += qty
        except ValueError as exc:
            warnings.append(str(exc))
        except Exception as exc:  # pragma: no cover - defensive
            current_app.logger.exception("Failed to bulk add card %r to folder %s", name, folder_id)
            warnings.append(f"Unable to add {name}: {exc}")

    if success_count:
        _safe_commit()
        flash(f"Processed {success_count} card name{'s' if success_count != 1 else ''} ({total_added} copies).", "success")
    else:
        flash("No cards were added.", "warning")

    for msg in warnings[:5]:
        flash(msg, "warning")
    if len(warnings) > 5:
        flash(f"{len(warnings) - 5} additional warnings suppressed.", "warning")

    return redirect(request.referrer or url_for("views.build_a_deck", folder_id=folder_id))


@views.post("/build-a-deck/<int:folder_id>/remove-card")
@build_post_limit
def build_remove_card(folder_id: int):
    folder = Folder.query.get_or_404(folder_id)
    if folder.is_collection:
        flash("Cannot modify collection folders from Build-A-Deck.", "warning")
        return redirect(request.referrer or url_for("views.build_a_deck", folder_id=folder_id))

    card_id = request.form.get("card_id", type=int)
    mode = (request.form.get("mode") or "").strip().lower()
    if not card_id:
        flash("No card specified to remove.", "warning")
        return redirect(request.referrer or url_for("views.build_a_deck", folder_id=folder_id))

    card = Card.query.filter_by(id=card_id, folder_id=folder.id).first()
    if not card:
        flash("Card not found in this deck.", "warning")
        return redirect(request.referrer or url_for("views.build_a_deck", folder_id=folder_id))

    if mode == "remove_all" or (card.quantity or 1) <= 1:
        db.session.delete(card)
        message = f'Removed {card.name} from "{folder.name}".'
    else:
        card.quantity = max(int(card.quantity or 1) - 1, 0)
        message = f'Reduced {card.name} to {card.quantity} copies.'

    _safe_commit()
    flash(message, "info")
    return redirect(request.referrer or url_for("views.build_a_deck", folder_id=folder_id))


@views.post("/build-a-deck/<int:folder_id>/update-card-quantity")
@build_post_limit
def build_update_card_quantity(folder_id: int):
    folder = Folder.query.get_or_404(folder_id)
    if folder.is_collection:
        flash("Cannot modify collection folders from Build-A-Deck.", "warning")
        return redirect(request.referrer or url_for("views.build_a_deck", folder_id=folder_id))

    card_id = request.form.get("card_id", type=int)
    raw_quantity = (request.form.get("quantity") or "").strip()

    if not card_id:
        flash("No card specified to update.", "warning")
        return redirect(request.referrer or url_for("views.build_a_deck", folder_id=folder_id))

    card = Card.query.filter_by(id=card_id, folder_id=folder.id).first()
    if not card:
        flash("Card not found in this deck.", "warning")
        return redirect(request.referrer or url_for("views.build_a_deck", folder_id=folder_id))

    try:
        target_quantity = int(raw_quantity)
    except (TypeError, ValueError):
        flash("Please enter a valid quantity.", "warning")
        return redirect(request.referrer or url_for("views.build_a_deck", folder_id=folder_id))

    if target_quantity < 0:
        flash("Quantity cannot be negative.", "warning")
        return redirect(request.referrer or url_for("views.build_a_deck", folder_id=folder_id))

    target_quantity = min(target_quantity, 999)

    if target_quantity == 0:
        db.session.delete(card)
        _safe_commit()
        flash(f'Removed {card.name} from "{folder.name}".', "info")
        return redirect(request.referrer or url_for("views.build_a_deck", folder_id=folder_id))

    current_quantity = int(card.quantity or 0)
    if target_quantity == current_quantity:
        flash(f"{card.name} already has {target_quantity} copy{'ies' if target_quantity != 1 else ''}.", "info")
        return redirect(request.referrer or url_for("views.build_a_deck", folder_id=folder_id))

    card.quantity = target_quantity
    _safe_commit()
    flash(f'Set {card.name} to {target_quantity} copy{"ies" if target_quantity != 1 else ""}.', "success")
    return redirect(request.referrer or url_for("views.build_a_deck", folder_id=folder_id))


@views.post("/build-a-deck/<int:folder_id>/upgrade-plan")
@build_post_limit
def build_update_upgrade_plan(folder_id: int):
    folder = Folder.query.get_or_404(folder_id)
    if folder.is_collection:
        flash("Cannot manage upgrade plans for collection folders.", "warning")
        return redirect(request.referrer or url_for("views.build_a_deck", folder_id=folder_id))

    mode = (request.form.get("mode") or "addition").lower()
    action = (request.form.get("action") or "add").lower()
    card_name = (request.form.get("card_name") or "").strip()

    plan = _load_upgrade_plan(folder_id)
    if mode not in {"addition", "add", "cut", "removal"}:
        mode = "addition"
    target_key = "adds" if mode in {"addition", "add"} else "cuts"
    target_list = plan[target_key]

    message = None
    if action == "clear":
        plan = {"adds": [], "cuts": []}
        message = "Upgrade plan cleared."
    elif action == "remove":
        if card_name and card_name in target_list:
            target_list.remove(card_name)
            message = f"Removed {card_name} from upgrade plan."
    else:
        if card_name:
            if card_name not in target_list:
                target_list.append(card_name)
                list_label = "upgrade list" if target_key == "adds" else "cut list"
                message = f'Added {card_name} to {list_label}.'
            elif action == "toggle":
                target_list.remove(card_name)
                message = f"Removed {card_name} from upgrade plan."

    _save_upgrade_plan(folder_id, plan["adds"], plan["cuts"])
    if message:
        flash(message, "success")
    return redirect(request.referrer or url_for("views.build_a_deck", folder_id=folder_id))


@views.post("/build-a-deck/<int:folder_id>/rename")
@build_post_limit
def build_rename_deck(folder_id: int):
    folder = Folder.query.get_or_404(folder_id)
    new_name = (request.form.get("name") or "").strip()
    if not new_name:
        flash("Deck name cannot be empty.", "warning")
        return redirect(url_for("views.build_a_deck", folder_id=folder_id))

    final_name = new_name
    if _folder_name_exists(new_name, exclude_id=folder.id):
        final_name = _generate_unique_folder_name(new_name, exclude_id=folder.id)
        flash(f'Deck name in use. Renamed to "{final_name}".', "info")

    folder.name = final_name
    _safe_commit()
    flash("Deck name updated.", "success")
    return redirect(request.referrer or url_for("views.build_a_deck", folder_id=folder_id))


@views.post("/build-a-deck/<int:folder_id>/set-tag")
@build_post_limit
def build_set_tag(folder_id: int):
    folder = Folder.query.get_or_404(folder_id)
    if folder.is_collection:
        flash("Collection folders cannot use deck tags.", "warning")
        return redirect(url_for("views.build_a_deck", folder_id=folder_id))

    tag_value = (request.form.get("tag") or "").strip()
    if tag_value:
        folder.deck_tag = tag_value
        message = f'Set deck tag to "{tag_value}".'
        level = "success"
    else:
        folder.deck_tag = None
        message = "Cleared deck tag."
        level = "info"

    _safe_commit()
    flash(message, level)
    return redirect(request.referrer or url_for("views.build_a_deck", folder_id=folder_id))


@views.post("/build-a-deck/<int:folder_id>/promote")
@build_post_limit
def build_promote(folder_id: int):
    folder = Folder.query.get_or_404(folder_id)
    if folder.category == Folder.CATEGORY_DECK:
        flash("This deck is already in the main deck list.", "info")
        return redirect(url_for("views.build_a_deck", folder_id=folder_id))

    folder.category = Folder.CATEGORY_DECK
    if "upgrade_plans" in session:
        session["upgrade_plans"].pop(str(folder_id), None)
    if "playground_origins" in session:
        session["playground_origins"].pop(str(folder_id), None)
    session.modified = True
    _safe_commit()
    flash(f'Promoted "{folder.name}" to the main deck list.', "success")
    return redirect(url_for("views.decks_overview"))


@views.post("/build-a-deck/<int:folder_id>/delete")
@build_post_limit
def build_delete(folder_id: int):
    folder = Folder.query.get_or_404(folder_id)
    name = folder.name or "Deck"
    db.session.delete(folder)
    if "upgrade_plans" in session:
        session["upgrade_plans"].pop(str(folder_id), None)
    if "playground_origins" in session:
        session["playground_origins"].pop(str(folder_id), None)
    session.modified = True
    _safe_commit()
    flash(f'Deleted "{name}" and its cards.', "info")
    return redirect(url_for("views.build_a_deck"))


__all__ = [
    "build_a_deck",
    "deckbuilder_search",
    "deckbuilder_analysis",
    "build_add_card",
    "build_bulk_add_cards",
    "build_delete",
    "build_promote",
    "build_remove_card",
    "build_update_upgrade_plan",
    "build_rename_deck",
    "build_set_tag",
    "build_update_commander",
]

def _normalize_role_key(raw: Any, *, fallback: str) -> str:
    """Return a normalized slug for a role key."""
    value = ""
    if isinstance(raw, str):
        value = raw.strip().lower()
    elif raw is not None:
        value = str(raw).strip().lower()
    if not value:
        value = fallback
    else:
        value = _ROLE_KEY_SLUG_RE.sub("-", value).strip("-")
        if not value:
            value = fallback
    return value
