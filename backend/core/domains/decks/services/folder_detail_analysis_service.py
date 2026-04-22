"""Row analysis helpers for folder detail rendering."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func

from extensions import db
from models import Card
from core.domains.cards.services import scryfall_cache as sc
from core.domains.cards.services.scryfall_cache import cache_ready, ensure_cache_loaded, find_by_set_cn

CARD_TYPE_GROUPS = [
    ("Creatures", "Creature"),
    ("Instants", "Instant"),
    ("Sorceries", "Sorcery"),
    ("Artifacts", "Artifact"),
    ("Enchantments", "Enchantment"),
    ("Planeswalkers", "Planeswalker"),
    ("Lands", "Land"),
    ("Battles", "Battle"),
]

_MANA_SYMBOL_RE = re.compile(r"\{([^}]+)\}")
WUBRG = ("W", "U", "B", "R", "G")
_COMMON_TOKEN_KINDS = [
    ("treasure", "Treasure"),
    ("food", "Food"),
    ("clue", "Clue"),
    ("blood", "Blood"),
    ("map", "Map"),
    ("powerstone", "Powerstone"),
]


@dataclass(slots=True)
class FolderDetailRowAnalysis:
    total_rows: int
    total_qty: int
    type_breakdown: list[tuple[str, int]]
    deck_tokens: list[dict[str, Any]]
    bracket_cards: list[dict[str, Any]]


def type_group_label(type_line: str) -> str:
    lowered = (type_line or "").lower()
    for label, token in CARD_TYPE_GROUPS:
        if token.lower() in lowered:
            return label
    return "Other"


def oracle_text_from_faces_json(faces_json: Any) -> str:
    if not faces_json:
        return ""
    faces = faces_json.get("faces") if isinstance(faces_json, dict) else faces_json
    if not isinstance(faces, list):
        return ""
    parts = [
        face.get("oracle_text")
        for face in faces
        if isinstance(face, dict) and face.get("oracle_text")
    ]
    return " // ".join(parts)


def type_line_from_print_payload(print_payload: dict[str, Any] | None) -> str:
    if not isinstance(print_payload, dict):
        return ""
    direct = str(print_payload.get("type_line") or "").strip()
    if direct:
        return direct
    faces = print_payload.get("card_faces")
    if not isinstance(faces, list):
        return ""
    parts = []
    for face in faces:
        if not isinstance(face, dict):
            continue
        value = str(face.get("type_line") or "").strip()
        if value:
            parts.append(value)
    if not parts:
        return ""
    return " // ".join(parts)


def artifact_production_colors(oracle_text: str | None) -> set[str]:
    if not oracle_text:
        return set()
    upper = oracle_text.upper()
    if "ADD" not in upper:
        return set()

    out: set[str] = set()
    for sym in _MANA_SYMBOL_RE.findall(oracle_text):
        symbol = sym.upper()
        for ch in WUBRG:
            if ch in symbol:
                out.add(ch)

    if "ANY COLOR" in upper:
        out.update(WUBRG)
    return out


def _faces_list(faces_json: Any) -> list[dict[str, Any]]:
    if not faces_json:
        return []
    if isinstance(faces_json, dict):
        faces = faces_json.get("faces") or []
    else:
        faces = faces_json
    return [face for face in faces if isinstance(face, dict)]


def _mana_costs_from_faces(faces_json: Any) -> list[str]:
    costs = [face.get("mana_cost") for face in _faces_list(faces_json) if face.get("mana_cost")]
    return [cost for cost in costs if cost]


def _mana_cost_from_faces(faces_json: Any) -> str | None:
    costs = _mana_costs_from_faces(faces_json)
    if not costs:
        return None
    return " // ".join(costs) if len(costs) > 1 else costs[0]


def _normalize_token_name(name: str | None) -> str:
    cleaned = (name or "").strip()
    if not cleaned:
        return "token"
    lowered = cleaned.lower()
    if lowered.endswith(" token"):
        lowered = lowered[:-6].strip()
    return lowered or "token"


def _token_pt_key(token: dict[str, Any]) -> str | None:
    power = token.get("power")
    toughness = token.get("toughness")
    if power is None or toughness is None:
        return None
    power_text = str(power).strip()
    toughness_text = str(toughness).strip()
    if not power_text or not toughness_text:
        return None
    return f"{power_text}/{toughness_text}"


def _tokens_are_generic(tokens: list[dict[str, Any]]) -> bool:
    if not tokens:
        return True
    for token in tokens:
        name = (token.get("name") or "").strip().lower()
        if token.get("id") or (name and name != "token"):
            return False
    return True


def _token_stubs_from_oracle_text(text: str | None) -> list[dict[str, Any]]:
    if not text:
        return []
    lower = text.lower()
    found: list[dict[str, Any]] = []

    def _lookup_token(label: str) -> dict[str, Any] | None:
        if not label or label.lower() == "token":
            return None
        try:
            matches = sc.search_tokens(label, limit=6) or []
        except Exception:
            return None
        label_norm = label.strip().casefold()
        for match in matches:
            if (match.get("name") or "").strip().casefold() == label_norm:
                return match
        return matches[0] if matches else None

    if "token" in lower:
        for key, label in _COMMON_TOKEN_KINDS:
            if f"{key} token" in lower:
                matched = _lookup_token(label)
                found.append(
                    {
                        "id": (matched or {}).get("id"),
                        "name": (matched or {}).get("name") or label,
                        "type_line": (matched or {}).get("type_line") or f"Token - {label}",
                        "images": (matched or {}).get("images") or {"small": None, "normal": None},
                    }
                )
    if not found and re.search(r"\bcreate\b.*\btoken\b", text, flags=re.IGNORECASE | re.DOTALL):
        found.append(
            {
                "id": None,
                "name": "Token",
                "type_line": "Token",
                "images": {"small": None, "normal": None},
            }
        )
    return found


def analyze_folder_rows(folder_id: int) -> FolderDetailRowAnalysis:
    total_rows, total_qty = (
        db.session.query(func.count(Card.id), func.coalesce(func.sum(Card.quantity), 0))
        .filter(Card.folder_id == folder_id)
        .one()
    )

    if not cache_ready():
        ensure_cache_loaded()

    base_types = [token for _label, token in CARD_TYPE_GROUPS]

    def parse_base_types(type_line: str) -> list[str]:
        if not type_line:
            return []
        return [token for token in base_types if token in type_line]

    type_counts = {token: 0 for token in base_types}
    tokens_by_key: dict[tuple[Any, ...], dict[str, Any]] = {}

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
            Card.type_line,
            Card.oracle_text,
            Card.mana_value,
            Card.faces_json,
        )
        .filter(Card.folder_id == folder_id)
        .all()
    )

    bracket_cards: list[dict[str, Any]] = []
    token_cache_by_oracle: dict[str, list[dict[str, Any]]] = {}
    row_print_cache: dict[tuple[str, str, str], dict[str, Any]] = {}

    def _row_print_payload(scode: Any, cn: Any, name: Any) -> dict[str, Any]:
        key = (
            str(scode or "").strip().lower(),
            str(cn or "").strip().lower(),
            str(name or "").strip().lower(),
        )
        if key not in row_print_cache:
            payload: dict[str, Any] = {}
            try:
                found = find_by_set_cn(scode, cn, name)
            except Exception:
                found = None
            if isinstance(found, dict):
                payload = found
            row_print_cache[key] = payload
        return row_print_cache[key]

    for cid, name, scode, cn, oid, _lang, _is_foil, _fid, qty, type_line, oracle_text, mana_value, faces_json in rows:
        qty = int(qty or 0) or 1

        row_print: dict[str, Any] = {}
        if not str(type_line or "").strip() or not str(oracle_text or "").strip() or mana_value is None:
            row_print = _row_print_payload(scode, cn, name)

        tline = str(type_line or "").strip() or type_line_from_print_payload(row_print)
        text = str(oracle_text or "").strip()
        if not text and faces_json:
            face_texts = [face.get("oracle_text") for face in _faces_list(faces_json) if face.get("oracle_text")]
            text = " // ".join(face_texts)
        if not text:
            text = str(row_print.get("oracle_text") or "").strip()
        if not text:
            text = oracle_text_from_faces_json(row_print.get("card_faces"))

        mana_cost = _mana_cost_from_faces(faces_json)
        if not mana_cost:
            row_mana_cost = row_print.get("mana_cost")
            if row_mana_cost:
                mana_cost = str(row_mana_cost)
        row_cmc = row_print.get("cmc")
        if mana_value is None and row_cmc is not None:
            mana_value = row_cmc

        bracket_cards.append(
            {
                "name": name,
                "type_line": tline,
                "oracle_text": text or "",
                "mana_value": mana_value,
                "quantity": qty,
                "mana_cost": mana_cost,
                "produced_mana": None,
                "game_changer": False,
            }
        )

        for token in parse_base_types(tline):
            type_counts[token] += qty

        tokens: list[dict[str, Any]] = []
        if oid:
            cached = token_cache_by_oracle.get(oid)
            if cached is None:
                try:
                    cached = sc.tokens_from_oracle(oid) or []
                except Exception:
                    cached = []
                token_cache_by_oracle[oid] = cached
            tokens = cached
        if _tokens_are_generic(tokens):
            tokens = []
        if not tokens:
            tokens = _token_stubs_from_oracle_text(text)
        if not tokens:
            continue

        for token in tokens:
            token_name = (token.get("name") or "Token").strip()
            token_line = (token.get("type_line") or "") or ""
            is_creature_token = "creature" in token_line.lower()
            if is_creature_token:
                pt_key = _token_pt_key(token)
                if pt_key:
                    key = ("crea_by_pt", _normalize_token_name(token_name), pt_key)
                else:
                    base_id = token.get("id") or f"{token_name.lower()}|{token_line.lower()}"
                    key = ("crea_per_source", cid, base_id)
            else:
                key = ("noncrea_by_name", _normalize_token_name(token_name))

            if key not in tokens_by_key:
                images = token.get("images") or {}
                tokens_by_key[key] = {
                    "id": token.get("id"),
                    "name": token_name,
                    "type_line": token_line or "Token",
                    "small": images.get("small"),
                    "normal": images.get("normal"),
                    "count": 0,
                    "sources": {},
                }
            else:
                images = token.get("images") or {}
                entry = tokens_by_key[key]
                if entry.get("id") is None and token.get("id"):
                    entry["id"] = token.get("id")
                if not entry.get("small") and images.get("small"):
                    entry["small"] = images.get("small")
                if not entry.get("normal") and images.get("normal"):
                    entry["normal"] = images.get("normal")
                if (entry.get("name") or "").lower() == "token" and token_name.lower() != "token":
                    entry["name"] = token_name
                if not entry.get("type_line") or entry.get("type_line") == "Token":
                    if token_line:
                        entry["type_line"] = token_line
            tokens_by_key[key]["count"] += qty
            sources = tokens_by_key[key]["sources"]
            if cid not in sources:
                sources[cid] = {"card_id": cid, "name": name, "qty": 0, "img": None}
            sources[cid]["qty"] += qty

    deck_tokens = []
    for item in tokens_by_key.values():
        source_list = list(item["sources"].values())
        source_list.sort(key=lambda value: (value["name"].lower(), value["card_id"]))
        deck_tokens.append(
            {
                "id": item["id"],
                "name": item["name"],
                "type_line": item["type_line"],
                "small": item["small"],
                "normal": item["normal"],
                "count": item["count"],
                "sources": source_list,
            }
        )
    deck_tokens.sort(key=lambda token: (token["name"].lower(), token.get("type_line") or ""))

    type_breakdown = [(token, type_counts[token]) for token in base_types if type_counts[token] > 0]
    return FolderDetailRowAnalysis(
        total_rows=int(total_rows or 0),
        total_qty=int(total_qty or 0),
        type_breakdown=type_breakdown,
        deck_tokens=deck_tokens,
        bracket_cards=bracket_cards,
    )


__all__ = [
    "CARD_TYPE_GROUPS",
    "FolderDetailRowAnalysis",
    "WUBRG",
    "analyze_folder_rows",
    "artifact_production_colors",
    "oracle_text_from_faces_json",
    "type_group_label",
    "type_line_from_print_payload",
]
