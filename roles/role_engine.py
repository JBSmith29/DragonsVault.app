from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, List, Sequence, Set


BASE_DIR = Path(__file__).resolve().parent
ROLE_RULES_PATH = BASE_DIR / "role_rules.json"
SUBROLE_RULES_PATH = BASE_DIR / "subrole_rules.json"


def _load_json(path: Path) -> dict:
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


ROLE_RULES = _load_json(ROLE_RULES_PATH)
SUBROLE_RULES = _load_json(SUBROLE_RULES_PATH)

PRIMARY_ROLE_PRIORITY: List[str] = [
    "removal",
    "ramp",
    "draw",
    "tutor",
    "recursion",
    "tokens",
    "protection",
    "counterspells",
    "lifegain",
    "combat",
    "sacrifice outlet",
    "stax",
    "utility",
]


def _collect_text(card) -> str:
    fields = []
    for attr in ("name", "type_line", "oracle_text", "rules_text", "text"):
        if hasattr(card, attr):
            val = getattr(card, attr, "") or ""
        elif isinstance(card, dict):
            val = card.get(attr, "") or ""
        else:
            val = ""
        if val:
            fields.append(str(val))
    return "\n".join(fields).lower()


def _match_keywords(text: str, keywords: Sequence[str]) -> bool:
    lowered = text
    for kw in keywords:
        if kw.lower() in lowered:
            return True
    return False


def _layer1_roles(text: str) -> Set[str]:
    roles: Set[str] = set()
    for role_key, data in ROLE_RULES.items():
        kws = data.get("keywords") or []
        if _match_keywords(text, kws):
            roles.add(role_key.lower())
    return roles


def _layer2_context(text: str) -> Set[str]:
    roles: Set[str] = set()
    if "counter target spell" in text or ("counter target" in text and "spell" in text):
        roles.add("counterspells")
    if "create a token" in text or "create a " in text and " token" in text:
        roles.add("tokens")
    if "add {" in text or "mana pool" in text or "untap target land" in text:
        roles.add("ramp")
    if "search your library" in text:
        roles.add("tutor")
    if "return target" in text and "graveyard" in text:
        roles.add("recursion")
    if "draw" in text and "card" in text:
        roles.add("draw")
    if "destroy target" in text or "exile target" in text or "sacrifice target" in text:
        roles.add("removal")
    if "indestructible" in text or "hexproof" in text or "ward" in text or "prevent all damage" in text or "phase out" in text:
        roles.add("protection")
    if "lifelink" in text or "gain life" in text:
        roles.add("lifegain")
    if "sacrifice a creature" in text or "sacrifice another creature" in text or "sacrifice a permanent" in text:
        roles.add("sacrifice outlet")
    if "each player can't" in text or "players can't" in text or "don't untap" in text:
        roles.add("stax")
    if "creatures you control get" in text or "whenever this creature attacks" in text or "extra combat" in text:
        roles.add("combat")
    return roles


def _layer3_fallback(text: str) -> Set[str]:
    # Simple fallback: if nothing matched, default to utility
    if not text.strip():
        return set()
    return {"utility"}


def _normalize_set(items: Iterable[str]) -> List[str]:
    seen: Set[str] = set()
    ordered: List[str] = []
    for item in items:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(key)
    return ordered


def get_roles_for_card(card) -> List[str]:
    text = _collect_text(card)
    roles = set()
    roles.update(_layer1_roles(text))
    roles.update(_layer2_context(text))
    if not roles:
        roles.update(_layer3_fallback(text))
    return _normalize_set(roles)


def _subrole_parent_map() -> dict:
    parent_map = {
        "ramp": "ramp",
        "land types": "ramp",
        "draw": "draw",
        "removal": "removal",
        "protection": "protection",
        "tokens": "tokens",
        "tutor": "tutor",
        "stax": "stax",
        "utility": "utility",
    }
    return parent_map


def get_subroles_for_card(card) -> List[str]:
    text = _collect_text(card)
    subroles: List[str] = []
    parent_map = _subrole_parent_map()
    for category, groups in SUBROLE_RULES.items():
        parent_role = parent_map.get(category.lower(), category.lower())
        if not isinstance(groups, dict):
            continue
        for subrole_key, keywords in groups.items():
            if not isinstance(keywords, list):
                continue
            if _match_keywords(text, keywords):
                subroles.append(f"{parent_role}:{subrole_key}".lower())
    return _normalize_set(subroles)


def get_primary_role(core_roles: Iterable[str]) -> str | None:
    if not core_roles:
        return None
    normalized = [r.lower() for r in core_roles]
    for candidate in PRIMARY_ROLE_PRIORITY:
        if candidate in normalized:
            return candidate
    return normalized[0] if normalized else None
