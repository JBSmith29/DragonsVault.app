"""Build session workflow helpers (proxy-only)."""

from __future__ import annotations

import os
import re
from collections import defaultdict
from typing import Iterable

from flask import abort, jsonify, render_template, request
from flask_login import current_user

from sqlalchemy import func, or_

from extensions import db
from models import BuildSession, BuildSessionCard, Card, Folder, FolderRole, OracleCoreRoleTag, OracleDeckTag
from core.domains.cards.services import scryfall_cache as sc
from core.domains.decks.services.build_recommendation_service import build_recommendation_sections
from core.domains.decks.services import build_session_mutation_service as mutation_service
from core.domains.decks.services import build_session_page_context_service as page_context_service
from core.domains.decks.services.commander_brackets import BRACKET_RULESET_EPOCH, evaluate_commander_bracket, spellbook_dataset_epoch
from core.domains.decks.services.commander_cache import compute_bracket_signature
from core.domains.decks.services.deck_tags import get_deck_tag_category, get_deck_tag_groups, resolve_deck_tag_from_slug
from core.domains.decks.services.edhrec_cache_service import (
    get_commander_synergy,
    get_commander_type_distribution,
)
from shared.cache.request_cache import request_cached
from core.shared.utils.symbols_cache import colors_to_icons, render_mana_html
from core.shared.utils.assets import static_url

_LEGACY_COLLECTION_FOLDER_NAMES = {"lands", "common", "uncommon", "rare", "mythic", "to add"}


def build_session_page(session_id: int):
    session = _get_session(session_id)
    if session is None:
        abort(404)
    sort_mode = _normalize_sort_mode(request.args.get("sort"))
    build_view = _normalize_build_view(request.args.get("build_view"))
    rec_source = request.args.get("rec_source") or "edhrec"
    edhrec_job_id = (request.args.get("edhrec_job_id") or "").strip() or None
    context = page_context_service.build_session_page_context(
        session,
        user_id=current_user.id if current_user.is_authenticated else None,
        sort_mode=sort_mode,
        build_view=build_view,
        rec_source=rec_source,
        edhrec_job_id=edhrec_job_id,
    )
    return render_template("decks/build_session.html", **context)


def api_build_session_insight(session_id: int):
    session = _get_session(session_id)
    if session is None:
        abort(404)
    payload = page_context_service.build_session_drawer_summary(session)
    return jsonify(payload)


def start_build_session():
    return mutation_service.start_build_session()


def add_card(session_id: int):
    return mutation_service.add_card(session_id)


def add_cards_bulk(session_id: int):
    return mutation_service.add_cards_bulk(session_id)


def add_cards_manual(session_id: int):
    return mutation_service.add_cards_manual(session_id)


def remove_card(session_id: int):
    return mutation_service.remove_card(session_id)


def update_quantity(session_id: int):
    return mutation_service.update_quantity(session_id)


def update_tags(session_id: int):
    return mutation_service.update_tags(session_id)


def update_name(session_id: int):
    return mutation_service.update_name(session_id)


def delete_session(session_id: int):
    return mutation_service.delete_session(session_id)


def refresh_edhrec(session_id: int):
    return mutation_service.refresh_edhrec(session_id)


def edhrec_status(session_id: int):
    return mutation_service.edhrec_status(session_id)


ensure_build_session_tables = mutation_service.ensure_build_session_tables
_ensure_build_session_tables = mutation_service._ensure_build_session_tables
_get_session = mutation_service._get_session
_normalized_tags = mutation_service._normalized_tags
_normalize_sort_mode = mutation_service._normalize_sort_mode
_normalize_build_view = mutation_service._normalize_build_view


def _rows_to_oracle_ids(rows) -> set[str]:
    return {
        str(row[0]).strip().casefold()
        for row in rows or []
        if row and row[0]
    }


def _resolve_oracle_ids_from_names(card_names: list[str]) -> set[str]:
    if not card_names:
        return set()
    resolved: set[str] = set()
    try:
        sc.ensure_cache_loaded()
    except Exception:
        return resolved
    for raw_name in card_names:
        name = (raw_name or "").strip()
        if not name:
            continue
        try:
            oracle_id = sc.unique_oracle_by_name(name)
        except Exception:
            oracle_id = None
        if oracle_id:
            resolved.add(str(oracle_id).strip().casefold())
    return resolved


def _collection_oracle_ids(user_id: int | None) -> set[str]:
    if not user_id:
        return set()
    rows = (
        db.session.query(Card.oracle_id)
        .join(Folder, Card.folder_id == Folder.id)
        .outerjoin(FolderRole, FolderRole.folder_id == Folder.id)
        .filter(
            Folder.owner_user_id == user_id,
            Card.oracle_id.isnot(None),
            or_(
                FolderRole.role == FolderRole.ROLE_COLLECTION,
                Folder.category == Folder.CATEGORY_COLLECTION,
                func.lower(Folder.category) == Folder.CATEGORY_COLLECTION,
            ),
        )
        .distinct()
        .all()
    )
    oracle_ids = _rows_to_oracle_ids(rows)
    if oracle_ids:
        return oracle_ids

    fallback_rows = (
        db.session.query(Card.oracle_id)
        .join(Folder, Card.folder_id == Folder.id)
        .filter(
            Folder.owner_user_id == user_id,
            Card.oracle_id.isnot(None),
            func.lower(Folder.name).in_(_LEGACY_COLLECTION_FOLDER_NAMES),
        )
        .distinct()
        .all()
    )
    oracle_ids = _rows_to_oracle_ids(fallback_rows)
    if oracle_ids:
        return oracle_ids

    fallback_name_rows = (
        db.session.query(Card.name)
        .join(Folder, Card.folder_id == Folder.id)
        .outerjoin(FolderRole, FolderRole.folder_id == Folder.id)
        .filter(
            Folder.owner_user_id == user_id,
            Card.name.isnot(None),
            or_(
                FolderRole.role == FolderRole.ROLE_COLLECTION,
                Folder.category == Folder.CATEGORY_COLLECTION,
                func.lower(Folder.category) == Folder.CATEGORY_COLLECTION,
                func.lower(Folder.name).in_(_LEGACY_COLLECTION_FOLDER_NAMES),
            ),
        )
        .distinct()
        .all()
    )
    return _resolve_oracle_ids_from_names([row[0] for row in fallback_name_rows if row and row[0]])


def _collection_name_keys(user_id: int | None) -> set[str]:
    if not user_id:
        return set()
    rows = (
        db.session.query(Card.name)
        .join(Folder, Card.folder_id == Folder.id)
        .outerjoin(FolderRole, FolderRole.folder_id == Folder.id)
        .filter(
            Folder.owner_user_id == user_id,
            Card.name.isnot(None),
            or_(
                FolderRole.role == FolderRole.ROLE_COLLECTION,
                Folder.category == Folder.CATEGORY_COLLECTION,
                func.lower(Folder.category) == Folder.CATEGORY_COLLECTION,
                func.lower(Folder.name).in_(_LEGACY_COLLECTION_FOLDER_NAMES),
            ),
        )
        .distinct()
        .all()
    )
    return {
        str(row[0]).strip().casefold()
        for row in rows
        if row and row[0]
    }


def _build_oracle_ids(entries: Iterable[BuildSessionCard]) -> set[str]:
    oracle_ids: set[str] = set()
    for entry in entries or []:
        oracle_id = (entry.card_oracle_id or "").strip()
        if not oracle_id:
            continue
        qty = int(entry.quantity or 0)
        if qty <= 0:
            continue
        oracle_ids.add(oracle_id)
    return oracle_ids


def _recommendation_oracle_ids(sections: list[dict] | None) -> set[str]:
    oracle_ids: set[str] = set()
    for section in sections or []:
        for card in section.get("cards") or []:
            oracle_id = (card.get("oracle_id") or "").strip()
            if oracle_id:
                oracle_ids.add(oracle_id)
    return oracle_ids


def _mark_collection_cards(
    sections: list[dict] | None,
    owned_oracles: set[str],
    owned_name_keys: set[str] | None = None,
) -> None:
    if not sections:
        return
    owned_lookup = {
        str(oracle_id).strip().casefold()
        for oracle_id in owned_oracles
        if oracle_id
    }
    name_lookup = {
        str(name_key).strip().casefold()
        for name_key in (owned_name_keys or set())
        if name_key
    }
    for section in sections:
        for card in section.get("cards") or []:
            oracle_id = (card.get("oracle_id") or "").strip()
            name_key = (card.get("name") or "").strip().casefold()
            card["in_collection"] = bool(
                (oracle_id and oracle_id.casefold() in owned_lookup)
                or (name_key and name_key in name_lookup)
            )


def _is_basic_land(type_line: str | None) -> bool:
    lowered = (type_line or "").lower()
    return "land" in lowered and "basic" in lowered


def _mark_build_cards(sections: list[dict] | None, build_oracles: set[str]) -> None:
    if not sections:
        return
    for section in sections:
        for card in section.get("cards") or []:
            oracle_id = (card.get("oracle_id") or "").strip()
            if not oracle_id:
                continue
            type_line = (card.get("type_line") or "").strip()
            if not type_line:
                type_line = _oracle_meta(oracle_id).get("type_line") or ""
                card["type_line"] = type_line
            card["is_basic_land"] = bool(card.get("is_basic_land")) or _is_basic_land(type_line)
            card["in_build"] = oracle_id in build_oracles


def _collection_recommendation_sections(
    commander_oracle_id: str,
    tags: list[str] | None,
    owned_oracles: set[str],
    role_needs: set[str] | None,
    *,
    exclude_oracles: set[str] | None = None,
    sort_mode: str = "synergy",
) -> list[dict]:
    if not commander_oracle_id or not owned_oracles:
        return []
    try:
        sc.ensure_cache_loaded()
    except Exception:
        return []

    commander_identity = _color_identity_set(commander_oracle_id)
    excluded = {oid.casefold() for oid in (exclude_oracles or set()) if oid}
    selected_tags = [tag for tag in _normalized_tags(tags) if tag]
    role_needs = {role for role in (role_needs or set()) if role}

    edhrec_rows = get_commander_synergy(
        commander_oracle_id,
        selected_tags,
        prefer_tag_specific=True,
        limit=None,
    )
    edhrec_map: dict[str, dict] = {}
    for rec in edhrec_rows:
        oracle_id = (rec.get("oracle_id") or "").strip()
        if not oracle_id:
            continue
        edhrec_map[oracle_id] = rec

    tag_map: dict[str, set[str]] = defaultdict(set)
    if selected_tags:
        rows = (
            db.session.query(OracleDeckTag.oracle_id, OracleDeckTag.tag)
            .filter(OracleDeckTag.tag.in_(selected_tags))
            .all()
        )
        for oracle_id, tag in rows:
            if not oracle_id or not tag:
                continue
            tag_map[oracle_id].add(str(tag))

    role_map: dict[str, set[str]] = defaultdict(set)
    if role_needs:
        rows = (
            db.session.query(OracleCoreRoleTag.oracle_id, OracleCoreRoleTag.role)
            .filter(OracleCoreRoleTag.role.in_(role_needs))
            .all()
        )
        for oracle_id, role in rows:
            if not oracle_id or not role:
                continue
            role_map[oracle_id].add(str(role))

    detail_cache: dict[str, dict] = {}
    grouped: dict[str, list[dict]] = defaultdict(list)

    # Build collection-only recommendations without duplicating EDHRec list entries.
    for oracle_id in owned_oracles:
        if not oracle_id:
            continue
        if oracle_id.casefold() in excluded:
            continue
        if oracle_id not in edhrec_map and oracle_id not in tag_map and oracle_id not in role_map:
            continue
        card_identity = _color_identity_set(oracle_id)
        if card_identity and not card_identity.issubset(commander_identity):
            continue
        payload = _oracle_payload(oracle_id)
        if not payload:
            continue
        detail = _oracle_detail(oracle_id, detail_cache)
        type_line = detail.get("type_line") or ""
        type_group = _collection_type_group(type_line)
        if not type_group:
            continue
        is_basic_land = _is_basic_land(type_line)

        reasons: list[str] = []
        synergy_score = None
        synergy_percent = None
        synergy_rank = None
        inclusion_percent = None
        edhrec_rec = edhrec_map.get(oracle_id)
        if edhrec_rec:
            synergy_score = edhrec_rec.get("synergy_score")
            synergy_percent = edhrec_rec.get("synergy_percent")
            synergy_rank = edhrec_rec.get("synergy_rank")
            inclusion_percent = edhrec_rec.get("inclusion_percent")
            reasons.append("edhrec synergy")

        tag_matches = sorted(tag_map.get(oracle_id, set()))
        if tag_matches:
            reasons.append(f"tag: {', '.join(tag_matches[:2])}")

        role_matches = sorted(role_map.get(oracle_id, set()))
        if role_matches:
            reasons.append(f"fills {role_matches[0].lower()}")

        score = float(synergy_score or 0.0)
        score += 0.12 * len(tag_matches)
        score += 0.1 * len(role_matches)

        if synergy_percent is None and synergy_score is not None:
            try:
                synergy_percent = round(float(synergy_score) * 100.0, 1)
            except (TypeError, ValueError):
                synergy_percent = None

        grouped[type_group].append(
            {
                "oracle_id": oracle_id,
                "name": payload.get("name") or oracle_id,
                "image": payload.get("image"),
                "type_group": type_group,
                "type_line": type_line,
                "is_basic_land": is_basic_land,
                "synergy_score": synergy_score,
                "synergy_percent": synergy_percent,
                "inclusion_percent": inclusion_percent,
                "synergy_rank": synergy_rank,
                "role_score": len(role_matches),
                "need_score": len(role_matches),
                "score": score,
                "reasons": reasons,
            }
        )

    all_cards = [card for cards in grouped.values() for card in cards]
    max_score = max((float(card.get("score") or 0.0) for card in all_cards), default=0.0)
    if max_score > 0:
        for card in all_cards:
            card["collection_score_percent"] = round((float(card.get("score") or 0.0) / max_score) * 100.0, 1)
    else:
        for card in all_cards:
            card["collection_score_percent"] = None

    sections: list[dict] = []
    for label in _COLLECTION_GROUP_ORDER:
        cards = grouped.get(label, [])
        if not cards:
            continue
        sections.append(
            {
                "key": _slugify(label),
                "label": label,
                "cards": _sort_collection_cards(cards, sort_mode),
                "default_open": label in {"Creatures", "Lands"},
                "count": len(cards),
            }
        )

    leftovers = grouped.get("Other", [])
    if leftovers:
        sections.append(
            {
                "key": "other",
                "label": "Other",
                "cards": _sort_collection_cards(leftovers, sort_mode),
                "default_open": False,
                "count": len(leftovers),
            }
        )

    return sections
def _oracle_name(oracle_id: str) -> str | None:
    if not oracle_id:
        return None
    try:
        sc.ensure_cache_loaded()
        prints = sc.prints_for_oracle(oracle_id) or []
    except Exception:
        return None
    if not prints:
        return None
    return (prints[0].get("name") or "").strip() or None


def _oracle_payload(oracle_id: str | None, *, fallback: str | None = None) -> dict:
    if not oracle_id:
        return {"oracle_id": None, "name": fallback or "", "image": None, "colors": []}
    try:
        sc.ensure_cache_loaded()
        prints = sc.prints_for_oracle(oracle_id) or []
    except Exception:
        prints = []
    name = fallback or oracle_id
    image = None
    colors: list[str] = []
    if prints:
        pr = prints[0]
        name = (pr.get("name") or "").strip() or name
        colors = pr.get("color_identity") or pr.get("colors") or []
        image_uris = pr.get("image_uris") or {}
        if not image_uris:
            faces = pr.get("card_faces") or []
            if faces:
                image_uris = (faces[0] or {}).get("image_uris") or {}
        image = image_uris.get("normal") or image_uris.get("large") or image_uris.get("small")
    return {"oracle_id": oracle_id, "name": name, "image": image, "colors": colors}


def _price_to_float(value: object | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _cheapest_price_for_oracle(oracle_id: str, cache: dict[str, str | None]) -> str | None:
    cached = cache.get(oracle_id)
    if cached is not None:
        return cached
    min_usd = None
    min_eur = None
    min_tix = None
    try:
        sc.ensure_cache_loaded()
        prints = sc.prints_for_oracle(oracle_id) or []
    except Exception:
        prints = []
    for pr in prints:
        prices = pr.get("prices") or {}
        for key in ("usd", "usd_foil", "usd_etched"):
            val = _price_to_float(prices.get(key))
            if val and val > 0:
                min_usd = val if min_usd is None or val < min_usd else min_usd
        for key in ("eur", "eur_foil"):
            val = _price_to_float(prices.get(key))
            if val and val > 0:
                min_eur = val if min_eur is None or val < min_eur else min_eur
        val = _price_to_float(prices.get("tix"))
        if val and val > 0:
            min_tix = val if min_tix is None or val < min_tix else min_tix
    if min_usd is not None:
        price_text = f"${min_usd:.2f}"
    elif min_eur is not None:
        price_text = f"EUR {min_eur:.2f}"
    elif min_tix is not None:
        price_text = f"{min_tix:.2f} TIX"
    else:
        price_text = None
    cache[oracle_id] = price_text
    return price_text


def _session_cards(entries: Iterable[BuildSessionCard]) -> list[dict]:
    cards: list[dict] = []
    detail_cache: dict[str, dict] = {}
    price_cache: dict[str, str | None] = {}
    for entry in entries:
        oracle_id = (entry.card_oracle_id or "").strip()
        if not oracle_id:
            continue
        payload = _oracle_payload(oracle_id)
        detail = _oracle_detail(oracle_id, detail_cache)
        price_text = _cheapest_price_for_oracle(oracle_id, price_cache)
        raw_costs = [cost for cost in (detail.get("mana_costs") or []) if cost]
        mana_cost_line = " // ".join(raw_costs) if raw_costs else ""
        mana_cost_html = render_mana_html(mana_cost_line, use_local=True) if mana_cost_line else None
        cmc_raw = detail.get("cmc")
        cmc_val = None
        if cmc_raw is not None:
            try:
                cmc_val = float(cmc_raw)
            except (TypeError, ValueError):
                cmc_val = None
        if cmc_val is None:
            cmc_bucket = ""
        else:
            bucket_val = int(round(cmc_val))
            if bucket_val < 0:
                bucket_val = 0
            cmc_bucket = str(bucket_val) if bucket_val <= 6 else "7+"
        cards.append(
            {
                "oracle_id": oracle_id,
                "name": payload["name"],
                "image": payload["image"],
                "quantity": int(entry.quantity or 0),
                "type_line": detail.get("type_line") or "",
                "cmc_bucket": cmc_bucket,
                "cmc_value": cmc_val,
                "price_text": price_text,
                "mana_cost_html": mana_cost_html,
            }
        )
    cards.sort(key=lambda item: (item["name"].casefold(), item["oracle_id"]))
    return cards


def _group_session_cards_by_type(cards: list[dict]) -> list[dict]:
    groups: dict[str, list[dict]] = {label: [] for label in _COLLECTION_GROUP_ORDER}
    extras: list[dict] = []
    for card in cards:
        label = _collection_type_group(card.get("type_line") or "")
        if label in groups:
            groups[label].append(card)
        else:
            extras.append(card)

    def _sort_key(card: dict) -> tuple:
        cmc = card.get("cmc_value")
        name = (card.get("name") or "").casefold()
        oracle_id = card.get("oracle_id") or ""
        if cmc is None:
            return (1, 0, name, oracle_id)
        return (0, cmc, name, oracle_id)

    grouped: list[dict] = []
    for label in _COLLECTION_GROUP_ORDER:
        entries = groups.get(label) or []
        if entries:
            entries.sort(key=_sort_key)
            grouped.append({"label": label, "cards": entries})
    if extras:
        extras.sort(key=_sort_key)
        grouped.append({"label": "Other", "cards": extras})
    return grouped


def _deck_metrics(entries: Iterable[BuildSessionCard]) -> dict:
    items = list(entries or [])
    oracle_ids = {entry.card_oracle_id for entry in items if entry.card_oracle_id}
    role_map = _oracle_role_map(oracle_ids)

    total_cards = 0
    land_count = 0
    mana_pip_non_land = {c: 0 for c in ["W", "U", "B", "R", "G"]}
    production_counts = {c: 0 for c in ["W", "U", "B", "R", "G", "C"]}
    curve_buckets = {"0": 0, "1": 0, "2": 0, "3": 0, "4": 0, "5": 0, "6": 0, "7+": 0}
    missing_cmc = 0
    role_counts = {key: 0 for key in _ROLE_BUCKETS}
    detail_cache: dict[str, dict] = {}

    for entry in items:
        oracle_id = (entry.card_oracle_id or "").strip()
        if not oracle_id:
            continue
        qty = int(entry.quantity or 0)
        if qty <= 0:
            continue
        total_cards += qty
        meta = _oracle_detail(oracle_id, detail_cache)
        type_line = (meta.get("type_line") or "").lower()
        cmc_raw = meta.get("cmc")
        if cmc_raw is None:
            cmc = None
        else:
            try:
                cmc = float(cmc_raw)
            except (TypeError, ValueError):
                cmc = None
        if "land" in type_line:
            land_count += qty
        else:
            for mana_cost in meta.get("mana_costs") or []:
                _add_colored_pips(mana_cost, qty, mana_pip_non_land)
        if meta.get("is_permanent"):
            for ch in _colors_from_oracle_text_add(meta.get("oracle_text") or ""):
                production_counts[ch] += qty
        if "land" not in type_line:
            if cmc is None:
                missing_cmc += qty
            else:
                bucket_val = int(round(cmc))
                if bucket_val < 0:
                    bucket_val = 0
                bucket = str(bucket_val) if bucket_val <= 6 else "7+"
                curve_buckets[bucket] += qty
        roles = role_map.get(oracle_id, set())
        for key, bucket_roles in _ROLE_BUCKETS.items():
            if roles & bucket_roles:
                role_counts[key] += qty

    non_land_count = max(total_cards - land_count, 0)
    deck_health, role_needs = _deck_health(role_counts)
    phase = "exploration" if total_cards < 20 else "refinement"

    total_curve = sum(curve_buckets.values()) or 1
    curve_rows = []
    for label in ["0", "1", "2", "3", "4", "5", "6", "7+"]:
        count = int(curve_buckets.get(label) or 0)
        pct = int(round(100.0 * count / total_curve)) if total_curve else 0
        curve_rows.append({"label": label, "count": count, "pct": pct})

    return {
        "total_cards": total_cards,
        "land_count": land_count,
        "non_land_count": non_land_count,
        "mana_pip_dist": _mana_pip_dist(mana_pip_non_land),
        "land_mana_sources": _mana_source_dist(production_counts),
        "curve_buckets": curve_buckets,
        "curve_rows": curve_rows,
        "missing_cmc": missing_cmc,
        "role_counts": role_counts,
        "deck_health": deck_health,
        "role_needs": role_needs,
        "phase": phase,
    }


def _oracle_role_map(oracle_ids: set[str]) -> dict[str, set[str]]:
    if not oracle_ids:
        return {}
    rows = (
        db.session.query(OracleCoreRoleTag.oracle_id, OracleCoreRoleTag.role)
        .filter(OracleCoreRoleTag.oracle_id.in_(oracle_ids))
        .all()
    )
    role_map: dict[str, set[str]] = {}
    for oracle_id, role in rows:
        if not oracle_id or not role:
            continue
        role_map.setdefault(oracle_id, set()).add(str(role))
    return role_map


def _oracle_meta(oracle_id: str) -> dict:
    try:
        sc.ensure_cache_loaded()
        prints = sc.prints_for_oracle(oracle_id) or []
    except Exception:
        return {}
    if not prints:
        return {}
    pr = prints[0]
    return {
        "type_line": pr.get("type_line") or "",
        "cmc": pr.get("cmc") or 0.0,
    }


def _oracle_detail(oracle_id: str, cache: dict[str, dict]) -> dict:
    cached = cache.get(oracle_id)
    if cached is not None:
        return cached
    payload = {"type_line": "", "cmc": None, "mana_costs": [], "oracle_text": "", "is_permanent": False}
    try:
        sc.ensure_cache_loaded()
        prints = sc.prints_for_oracle(oracle_id) or []
    except Exception:
        cache[oracle_id] = payload
        return payload
    if not prints:
        cache[oracle_id] = payload
        return payload
    pr = prints[0]
    type_line = pr.get("type_line") or ""
    payload["type_line"] = type_line
    payload["cmc"] = pr.get("cmc")
    payload["mana_costs"] = _mana_costs_from_faces(pr)
    payload["oracle_text"] = _oracle_text_from_faces(pr)
    payload["is_permanent"] = _is_permanent_type(type_line)
    cache[oracle_id] = payload
    return payload


def _mana_costs_from_faces(print_obj: dict) -> list[str]:
    faces = print_obj.get("card_faces") or []
    face_costs: list[str] = []
    for face in faces:
        if not isinstance(face, dict):
            continue
        face_cost = face.get("mana_cost")
        if face_cost:
            face_costs.append(str(face_cost))
    if face_costs:
        return [cost for cost in face_costs if cost]
    mana_cost = print_obj.get("mana_cost")
    if mana_cost:
        return [str(mana_cost)]
    return []


def _oracle_text_from_faces(print_obj: dict) -> str:
    texts: list[str] = []
    oracle_text = print_obj.get("oracle_text")
    if oracle_text:
        texts.append(str(oracle_text))
    faces = print_obj.get("card_faces") or []
    for face in faces:
        if not isinstance(face, dict):
            continue
        face_text = face.get("oracle_text")
        if face_text:
            texts.append(str(face_text))
    return " // ".join([t for t in texts if t])


def _is_permanent_type(type_line: str) -> bool:
    lowered = (type_line or "").lower()
    return any(token in lowered for token in ("land", "creature", "artifact", "enchantment", "planeswalker", "battle"))


def _add_colored_pips(mana_cost: str, qty: int, counts: dict[str, int]) -> None:
    for symbol in RE_COST_SYMBOL.findall(mana_cost or ""):
        token = symbol.upper()
        for ch in ("W", "U", "B", "R", "G"):
            if ch in token:
                counts[ch] += qty


def _colors_from_oracle_text_add(text: str) -> set[str]:
    out: set[str] = set()
    if not text:
        return out
    upper = text.upper()
    if "ADD" not in upper:
        return out
    for sym in RE_COST_SYMBOL.findall(text):
        token = sym.upper()
        for ch in ("W", "U", "B", "R", "G", "C"):
            if ch in token:
                out.add(ch)
    if "ANY COLOR" in upper:
        out.update({"W", "U", "B", "R", "G"})
    return out


def _mana_pip_dist(counts: dict[str, int]) -> list[tuple[str, str | None, int]]:
    dist: list[tuple[str, str | None, int]] = []
    for c in ["W", "U", "B", "R", "G"]:
        value = int(counts.get(c) or 0)
        if value <= 0:
            continue
        icons = colors_to_icons([c], use_local=True)
        dist.append((c, icons[0] if icons else None, value))
    return dist


def _mana_source_dist(counts: dict[str, int]) -> list[tuple[str, str | None, int]]:
    dist: list[tuple[str, str | None, int]] = []
    for c in ["W", "U", "B", "R", "G", "C"]:
        value = int(counts.get(c) or 0)
        if value <= 0:
            continue
        icon = None
        if c in {"W", "U", "B", "R", "G"}:
            icons = colors_to_icons([c], use_local=True)
            icon = icons[0] if icons else None
        dist.append((c, icon, value))
    return dist


def _color_identity_set(oracle_id: str) -> set[str]:
    if not oracle_id:
        return set()
    try:
        prints = sc.prints_for_oracle(oracle_id) or []
    except Exception:
        return set()
    if not prints:
        return set()
    identity_raw = prints[0].get("color_identity") or prints[0].get("colors") or []
    letters, _ = sc.normalize_color_identity(identity_raw)
    return set(letters)


def _type_breakdown_for_entries(entries: Iterable[BuildSessionCard]) -> list[tuple[str, int]]:
    type_counts = {t: 0 for t in _BASE_TYPES}
    for entry in entries or []:
        oracle_id = (entry.card_oracle_id or "").strip()
        if not oracle_id:
            continue
        qty = int(entry.quantity or 0)
        if qty <= 0:
            continue
        type_line = (_oracle_meta(oracle_id).get("type_line") or "").lower()
        if not type_line:
            continue
        for t in _BASE_TYPES:
            if t.lower() in type_line:
                type_counts[t] += qty
    return [(t, int(type_counts.get(t, 0))) for t in _BASE_TYPES]


def _primary_type_for_distribution(type_line: str) -> str | None:
    lowered = (type_line or "").lower()
    if not lowered:
        return None
    for t in _DISTRIBUTION_PRIORITY:
        if t.lower() in lowered:
            return t
    return None


def _distribution_breakdown_for_entries(entries: Iterable[BuildSessionCard]) -> list[tuple[str, int]]:
    type_counts = {t: 0 for t in _DISTRIBUTION_TYPES}
    detail_cache: dict[str, dict] = {}
    for entry in entries or []:
        oracle_id = (entry.card_oracle_id or "").strip()
        if not oracle_id:
            continue
        qty = int(entry.quantity or 0)
        if qty <= 0:
            continue
        type_line = (_oracle_detail(oracle_id, detail_cache).get("type_line") or "").lower()
        if not type_line:
            continue
        primary = _primary_type_for_distribution(type_line)
        if primary and primary in type_counts:
            type_counts[primary] += qty
    return [(t, int(type_counts.get(t, 0))) for t in _DISTRIBUTION_TYPES]


def _edhrec_type_breakdown(
    commander_oracle_id: str,
    tags: list[str] | None,
) -> list[tuple[str, int]]:
    if not commander_oracle_id:
        return []
    tag_label = None
    if tags:
        tag_label = resolve_deck_tag_from_slug(tags[0])
        tag_label = (tag_label or "").strip() or None

    dist_rows = get_commander_type_distribution(commander_oracle_id, tag=tag_label)
    if tag_label and not dist_rows:
        dist_rows = get_commander_type_distribution(commander_oracle_id, tag=None)
    if dist_rows:
        counts = {label: int(count or 0) for label, count in dist_rows if label}
        trimmed = {label: int(counts.get(label, 0)) for label in _DISTRIBUTION_TYPES}
        return [(label, int(trimmed.get(label, 0))) for label in _DISTRIBUTION_TYPES]
    return []


def _curve_bucket(cmc: float) -> str:
    if cmc <= 2:
        return "0-2"
    if cmc <= 4:
        return "3-4"
    if cmc <= 6:
        return "5-6"
    return "7+"


def _deck_health(role_counts: dict) -> tuple[list[dict], set[str]]:
    health: list[dict] = []
    role_needs: set[str] = set()
    for key, config in _ROLE_TARGETS.items():
        count = int(role_counts.get(key, 0))
        target = config["target"]
        status = _status_label(count, target)
        if status == "low":
            role_needs |= config["roles"]
        health.append(
            {
                "key": key,
                "label": config["label"],
                "count": count,
                "target": target,
                "status": status,
            }
        )
    return health, role_needs


def _status_label(count: int, target: int) -> str:
    if target <= 0:
        return "ok"
    if count < max(int(target * 0.7), 1):
        return "low"
    if count > int(target * 1.4):
        return "high"
    return "ok"


def _primary_type_for_breakdown(type_line: str) -> str | None:
    lowered = (type_line or "").lower()
    if not lowered:
        return None
    for t in _TYPE_PRIORITY:
        if t.lower() in lowered:
            return t
    return None


def _normalize_distribution_total(counts: dict[str, int], *, target_total: int) -> dict[str, int]:
    total = sum(int(value or 0) for value in counts.values())
    if total <= 0 or total == target_total:
        return counts
    adjusted = {key: int(value or 0) for key, value in counts.items()}
    if total == target_total + 1:
        if adjusted.get("Creature", 0) > 0:
            adjusted["Creature"] -= 1
            return adjusted
    scaled = {}
    for key, value in adjusted.items():
        scaled[key] = int(round((value / total) * target_total)) if total else 0
    current = sum(scaled.values())
    if current == target_total:
        return scaled
    ordered = sorted(scaled.items(), key=lambda item: item[1], reverse=True)
    idx = 0
    while current != target_total and ordered:
        key = ordered[idx % len(ordered)][0]
        if current < target_total:
            scaled[key] += 1
            current += 1
        else:
            if scaled[key] > 0:
                scaled[key] -= 1
                current -= 1
        idx += 1
    return scaled


def _build_session_bracket_context(session: BuildSession, entries: Iterable[BuildSessionCard]) -> dict:
    detail_cache: dict[str, dict] = {}
    bracket_cards: list[dict[str, object]] = []
    for entry in entries or []:
        oracle_id = (entry.card_oracle_id or "").strip()
        if not oracle_id:
            continue
        qty = int(entry.quantity or 0)
        if qty <= 0:
            continue
        detail = _oracle_detail(oracle_id, detail_cache)
        costs = [cost for cost in (detail.get("mana_costs") or []) if cost]
        bracket_cards.append(
            {
                "name": _oracle_name(oracle_id) or oracle_id,
                "type_line": detail.get("type_line") or "",
                "oracle_text": detail.get("oracle_text") or "",
                "mana_value": detail.get("cmc"),
                "quantity": qty,
                "mana_cost": " // ".join(costs) if costs else None,
                "produced_mana": None,
            }
        )

    commander_stub = {
        "oracle_id": session.commander_oracle_id,
        "name": session.commander_name or _oracle_name(session.commander_oracle_id or ""),
    }
    epoch = sc.cache_epoch() + BRACKET_RULESET_EPOCH + spellbook_dataset_epoch()
    signature = compute_bracket_signature(bracket_cards, commander_stub, epoch=epoch)
    cache_key = ("build_session_bracket", session.id, signature, epoch)
    commander_ctx = request_cached(
        cache_key,
        lambda: evaluate_commander_bracket(bracket_cards, commander_stub),
    )
    spellbook_details = commander_ctx.get("spellbook_details") or []
    if len(spellbook_details) > 8:
        spellbook_details = spellbook_details[:8]
    return {
        "level": commander_ctx.get("level"),
        "label": commander_ctx.get("label"),
        "score": commander_ctx.get("score"),
        "summary_points": commander_ctx.get("summary_points") or [],
        "spellbook_combos": spellbook_details,
    }


_build_session_drawer_summary = page_context_service.build_session_drawer_summary


def _commander_drawer_payload(oracle_id: str | None, fallback_name: str | None) -> dict:
    placeholder_thumb = static_url("img/card-placeholder.svg")
    if not oracle_id:
        return {"name": fallback_name or "Commander", "image": placeholder_thumb, "hover": placeholder_thumb}
    try:
        sc.ensure_cache_loaded()
        prints = sc.prints_for_oracle(oracle_id) or []
    except Exception:
        prints = []
    if not prints:
        return {"name": fallback_name or "Commander", "image": placeholder_thumb, "hover": placeholder_thumb}
    pr = prints[0]
    name = fallback_name or (pr.get("name") or "Commander")
    image_uris = pr.get("image_uris") or {}
    if not image_uris:
        faces = pr.get("card_faces") or []
        if faces:
            image_uris = (faces[0] or {}).get("image_uris") or {}
    image = image_uris.get("small") or image_uris.get("normal") or image_uris.get("large")
    hover = image_uris.get("large") or image_uris.get("normal") or image_uris.get("small")
    return {
        "name": name,
        "image": image or placeholder_thumb,
        "hover": hover or image or placeholder_thumb,
    }


def _curve_rows_for_entries(entries: Iterable[BuildSessionCard]) -> list[dict]:
    bins = {"0": 0, "1": 0, "2": 0, "3": 0, "4": 0, "5": 0, "6": 0, "7+": 0}
    detail_cache: dict[str, dict] = {}
    for entry in entries or []:
        oracle_id = (entry.card_oracle_id or "").strip()
        if not oracle_id:
            continue
        qty = int(entry.quantity or 0)
        if qty <= 0:
            continue
        meta = _oracle_detail(oracle_id, detail_cache)
        type_line = (meta.get("type_line") or "").lower()
        if "land" in type_line:
            continue
        cmc_raw = meta.get("cmc")
        if cmc_raw is None:
            cmc = None
        else:
            try:
                cmc = float(cmc_raw)
            except (TypeError, ValueError):
                cmc = None
        if cmc is None:
            continue
        bucket_val = int(round(cmc))
        if bucket_val < 0:
            bucket_val = 0
        bucket = str(bucket_val) if bucket_val <= 6 else "7+"
        bins[bucket] += qty

    max_curve = max(bins.values()) if bins else 0
    rows = []
    for bucket in ["0", "1", "2", "3", "4", "5", "6", "7+"]:
        count = int(bins.get(bucket) or 0)
        if count <= 0:
            continue
        pct = 100.0 * count / max_curve if max_curve else 0.0
        rows.append({"label": bucket, "count": count, "pct": pct})
    return rows


def _normalize_sort_mode(raw: str | None) -> str:
    value = (raw or "").strip().lower()
    if value in {"role", "need"}:
        return value
    return "synergy"


def _normalize_build_view(raw: str | None) -> str:
    value = (raw or "").strip().lower()
    if value in {"list", "gallery", "type"}:
        return value
    return ""


def _edhrec_estimate_seconds(tags: list[str] | None) -> int:
    try:
        interval = float(os.getenv("EDHREC_INGEST_INTERVAL", "1.0"))
    except (TypeError, ValueError):
        interval = 1.0
    interval = max(1.0, interval)
    request_count = 1 + (1 if tags else 0)
    estimate = (request_count * interval) + 3
    return int(round(estimate))


def _slugify(label: str) -> str:
    return (label or "").strip().lower().replace(" ", "-")


def _collection_type_group(type_line: str) -> str:
    lowered = (type_line or "").lower()
    for label, tokens in _COLLECTION_TYPE_GROUPS:
        if any(token in lowered for token in tokens):
            return label
    return "Other" if lowered else ""


def _sort_collection_cards(cards: list[dict], sort_mode: str) -> list[dict]:
    mode = (sort_mode or "synergy").strip().lower()
    if mode == "role":
        key = lambda item: (-item.get("role_score", 0), -item.get("score", 0.0), item.get("name", ""))
    elif mode == "need":
        key = lambda item: (-item.get("need_score", 0), -item.get("score", 0.0), item.get("name", ""))
    else:
        key = lambda item: (
            -float(item.get("synergy_score") or 0.0),
            -float(item.get("score") or 0.0),
            item.get("synergy_rank") or 999999,
            item.get("name", ""),
        )
    return sorted(cards, key=key)


_ROLE_BUCKETS = {
    "ramp": {"Ramp", "Fixing", "Treasure"},
    "draw": {"Draw", "Selection", "Advantage"},
    "interaction": {"Removal", "Wipe", "Counter", "Bounce", "Tax", "Stax", "Hate"},
    "wincon": {"Finisher", "Payoff", "Go Wide", "Go Tall", "Voltron", "Engine"},
}

_ROLE_TARGETS = {
    "ramp": {"label": "ramp", "target": 10, "roles": _ROLE_BUCKETS["ramp"]},
    "draw": {"label": "card draw", "target": 8, "roles": _ROLE_BUCKETS["draw"]},
    "interaction": {"label": "interaction", "target": 8, "roles": _ROLE_BUCKETS["interaction"]},
    "wincon": {"label": "win conditions", "target": 2, "roles": _ROLE_BUCKETS["wincon"]},
}

_BASE_TYPES = ["Artifact", "Battle", "Creature", "Enchantment", "Instant", "Land", "Planeswalker", "Sorcery"]
_TYPE_PRIORITY = ["Land", "Creature", "Instant", "Sorcery", "Artifact", "Enchantment", "Planeswalker", "Battle"]
_DISTRIBUTION_TYPES = ["Land", "Enchantment", "Artifact", "Sorcery", "Instant", "Creature", "Planeswalker"]
_DISTRIBUTION_PRIORITY = ["Land", "Creature", "Instant", "Sorcery", "Artifact", "Enchantment", "Planeswalker"]

_COLLECTION_GROUP_ORDER = [
    "Creatures",
    "Instants",
    "Sorceries",
    "Enchantments",
    "Artifacts",
    "Planeswalkers",
    "Lands",
]

_COLLECTION_TYPE_GROUPS = [
    ("Creatures", ("creature",)),
    ("Instants", ("instant",)),
    ("Sorceries", ("sorcery",)),
    ("Enchantments", ("enchantment",)),
    ("Artifacts", ("artifact",)),
    ("Planeswalkers", ("planeswalker",)),
    ("Lands", ("land",)),
]

RE_COST_SYMBOL = re.compile(r"\{([^}]+)\}")


__all__ = [
    "add_card",
    "add_cards_bulk",
    "add_cards_manual",
    "build_session_page",
    "remove_card",
    "start_build_session",
    "ensure_build_session_tables",
    "update_tags",
    "update_name",
    "update_quantity",
    "delete_session",
    "api_build_session_insight",
]
