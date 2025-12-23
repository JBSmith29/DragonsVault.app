from __future__ import annotations

import json
import re
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


def _get_field(card, field: str) -> str:
    if hasattr(card, field):
        return getattr(card, field, "") or ""
    if isinstance(card, dict):
        return card.get(field, "") or ""
    return ""

def _get_raw_field(card, field: str):
    if hasattr(card, field):
        return getattr(card, field)
    if isinstance(card, dict):
        return card.get(field)
    return None


_BASIC_TYPE_TAGS = {
    "plains": "Plains",
    "island": "Island",
    "swamp": "Swamp",
    "mountain": "Mountain",
    "forest": "Forest",
}

_SPECIAL_SUBTYPE_TAGS = {
    "gate": "Gate",
    "desert": "Desert",
    "lair": "Lair",
    "locus": "Locus",
}

_MANA_SYMBOL_RE = re.compile(r"\{([WUBRG])\}", re.IGNORECASE)


def _split_faces(value: str) -> List[str]:
    if not value:
        return []
    if "//" in value:
        return [part.strip() for part in value.split("//") if part.strip()]
    return [value.strip()]


def _split_type_line(type_line: str) -> tuple[str, str]:
    if not type_line:
        return "", ""
    if "\u2014" in type_line:
        left, right = type_line.split("\u2014", 1)
    elif " - " in type_line:
        left, right = type_line.split(" - ", 1)
    else:
        return type_line.strip(), ""
    return left.strip(), right.strip()


def _land_face_data(card) -> tuple[List[str], str, bool, int]:
    """
    Return (land_type_lines, land_oracle_text, has_non_land_face, land_face_count).
    Prefer explicit card_faces when present so MDFCs only use the land face text.
    """
    faces = _get_raw_field(card, "card_faces")
    if isinstance(faces, list) and faces:
        land_faces = []
        non_land_faces = 0
        for face in faces:
            if not isinstance(face, dict):
                continue
            face_type = (face.get("type_line") or "").lower()
            if "land" in face_type:
                land_faces.append(face)
            elif face_type:
                non_land_faces += 1
        if land_faces:
            type_lines = [face.get("type_line", "") for face in land_faces if face.get("type_line")]
            texts = [face.get("oracle_text", "") for face in land_faces if face.get("oracle_text")]
            land_text = "\n".join(texts) if texts else _get_field(card, "oracle_text")
            return type_lines, land_text or "", bool(non_land_faces), len(land_faces)

    type_line = _get_field(card, "type_line")
    oracle_text = _get_field(card, "oracle_text")
    faces = _split_faces(type_line)
    land_faces = [face for face in faces if "land" in face.lower()]
    non_land_faces = [face for face in faces if "land" not in face.lower()]
    return land_faces or [type_line], oracle_text or "", bool(non_land_faces), len(land_faces)


def _extract_land_subtypes(type_lines: Iterable[str]) -> Set[str]:
    subtypes: Set[str] = set()
    for line in type_lines:
        left, right = _split_type_line(line)
        if "land" not in left.lower():
            continue
        for token in right.split():
            token = token.strip()
            if token:
                subtypes.add(token)
    return subtypes


def _extract_mana_colors(text: str, produced_mana: Iterable[str] | None) -> Set[str]:
    colors: Set[str] = set()
    if produced_mana:
        for symbol in produced_mana:
            if not symbol:
                continue
            symbol = str(symbol).upper()
            if symbol in "WUBRG":
                colors.add(symbol)
    for symbol in _MANA_SYMBOL_RE.findall(text or ""):
        colors.add(symbol.upper())
    return colors


def _has_any_color(text: str) -> bool:
    lowered = text.lower()
    return "any color" in lowered or "any colour" in lowered


def _detect_enters_tapped(text: str) -> bool:
    lowered = text.lower()
    return "enters the battlefield tapped" in lowered or "enters tapped" in lowered


def _detect_conditional_untapped(text: str, enters_tapped: bool) -> bool:
    if not enters_tapped:
        return False
    lowered = text.lower()
    if "unless" in lowered and "tapped" in lowered:
        return True
    if "if you don't" in lowered and "tapped" in lowered:
        return True
    if "you may reveal" in lowered and "from your hand" in lowered and "tapped" in lowered:
        return True
    return False


def _detect_life_payment(text: str) -> bool:
    lowered = text.lower()
    return bool(
        re.search(r"pay\s+\d+\s+life|pay\s+life|lose\s+\d+\s+life", lowered)
        or "deals 1 damage to you" in lowered
        or "damage to you" in lowered
    )


def _detect_filter_mana(text: str) -> bool:
    lowered = text.lower()
    if "any combination of" in lowered:
        return True
    if "add two mana in any combination" in lowered:
        return True
    if _MANA_SYMBOL_RE.search(lowered) and re.search(r"add\s+\{[wubrg]\}\{[wubrg]\}", lowered):
        return True
    return False


def _detect_manland(text: str) -> bool:
    lowered = text.lower()
    return "becomes a" in lowered and "creature" in lowered and "until end of turn" in lowered


def _basic_types_in_text(text: str) -> Set[str]:
    lowered = text.lower()
    return {name for name in _BASIC_TYPE_TAGS if name in lowered}


def _detect_fetch_category(text: str, has_life_payment: bool, enters_tapped: bool) -> str | None:
    lowered = text.lower()
    if "search your library" not in lowered:
        return None
    if "land card" not in lowered and not _basic_types_in_text(lowered):
        return None
    if "sacrifice" not in lowered:
        return None
    basic_count = len(_basic_types_in_text(lowered))
    if re.search(r"\{1\}.*\{t\}.*sacrifice", lowered) and basic_count >= 3:
        return "Panorama Fetch"
    if has_life_payment:
        return "Fetch Land"
    if enters_tapped and basic_count >= 2:
        return "Slow Fetch"
    return "Sac-to-Search Land"


def _normalize_tag_list(items: Iterable[str]) -> List[str]:
    seen: Set[str] = set()
    ordered: List[str] = []
    for item in items:
        if not item:
            continue
        cleaned = str(item).strip()
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(cleaned)
    return ordered


def classify_land(card) -> dict | None:
    """
    Classify a land into a single primary category and a list of secondary tags.
    Tags focus on mechanics/subtypes, not the primary category.
    """
    if not is_land_card(card):
        return None

    name = _get_field(card, "name")
    type_lines, land_text, has_non_land_face, land_face_count = _land_face_data(card)
    type_line = "\n".join([tl for tl in type_lines if tl])
    oracle_text = land_text or ""
    lowered = oracle_text.lower()

    subtypes = _extract_land_subtypes(type_lines)
    basic_subtypes = {t for t in subtypes if t.lower() in _BASIC_TYPE_TAGS}

    supertype_line = " ".join(type_lines).lower()
    is_basic = "basic" in supertype_line
    is_snow = "snow" in supertype_line

    produced_mana = _get_raw_field(card, "produced_mana")
    colors_produced = _extract_mana_colors(oracle_text, produced_mana if isinstance(produced_mana, Iterable) else None)
    produces_any_color = _has_any_color(oracle_text)

    enters_tapped = _detect_enters_tapped(oracle_text)
    conditional_untapped = _detect_conditional_untapped(oracle_text, enters_tapped)
    has_cycling = "cycling" in lowered
    has_life_payment = _detect_life_payment(oracle_text)
    has_sacrifice = "sacrifice" in lowered
    has_filter_mana = _detect_filter_mana(oracle_text)
    is_manland = _detect_manland(oracle_text)

    is_pathway = land_face_count >= 2 and not has_non_land_face
    is_spell_mdfc = land_face_count >= 1 and has_non_land_face

    fetch_category = _detect_fetch_category(oracle_text, has_life_payment, enters_tapped)

    basic_type_count = len(basic_subtypes)
    color_count = len(colors_produced)
    is_tri_color = basic_type_count >= 3 or (color_count >= 3 and not produces_any_color)
    is_dual_color = basic_type_count == 2 or (color_count == 2 and not produces_any_color)

    is_domain_land = "domain" in lowered or "basic land types among lands you control" in lowered
    is_scry_land = "scry 1" in lowered
    is_gain_land = "gain 1 life" in lowered
    is_shock_land = "pay 2 life" in lowered and enters_tapped
    is_battle_land = "two or more basic lands" in lowered and enters_tapped
    is_fast_land = "two or fewer other lands" in lowered and enters_tapped
    is_slow_land = "two or more other lands" in lowered and enters_tapped
    is_check_land = bool(
        re.search(
            r"enters the battlefield tapped unless you control.*(plains|island|swamp|mountain|forest).*or.*(plains|island|swamp|mountain|forest)",
            lowered,
        )
    )
    is_reveal_land = "reveal" in lowered and "from your hand" in lowered and "tapped" in lowered
    is_pain_land = ("deals 1 damage to you" in lowered or "you lose 1 life" in lowered) and "add" in lowered
    is_filter_land = has_filter_mana and is_dual_color
    is_filter_tri = has_filter_mana and is_tri_color
    is_cycling_dual = has_cycling and is_dual_color

    is_command_tower = "commander" in lowered and "any color" in lowered
    is_vivid = "vivid" in lowered or ("charge counter" in lowered and "any color" in lowered)
    is_city_of_brass = produces_any_color and (has_life_payment or "damage to you" in lowered)
    is_conditional_any = produces_any_color and (
        "only to cast" in lowered
        or "that a land you control could produce" in lowered
        or "of a color among" in lowered
        or "an opponent controls" in lowered
    )
    is_rainbow = produces_any_color and not (is_command_tower or is_city_of_brass or is_vivid or is_conditional_any)

    tags: List[str] = []
    for subtype in sorted(subtypes):
        lower = subtype.lower()
        if lower in _BASIC_TYPE_TAGS:
            tags.append(_BASIC_TYPE_TAGS[lower])
        elif lower in _SPECIAL_SUBTYPE_TAGS:
            tags.append(_SPECIAL_SUBTYPE_TAGS[lower])
    if is_snow:
        tags.append("Snow")
    if has_cycling:
        tags.append("Cycling")
    if has_life_payment:
        tags.append("Life Payment")
    if enters_tapped:
        tags.append("Enters Tapped")
    if conditional_untapped:
        tags.append("Conditional Untapped")
    if has_sacrifice:
        tags.append("Sacrifice")
    if has_filter_mana:
        tags.append("Filter Mana")

    primary = None
    if is_basic:
        primary = "Snow Basic" if is_snow else "Basic Land"
    elif is_spell_mdfc:
        primary = "Spell Land (MDFC)"
    elif fetch_category:
        primary = fetch_category
    elif is_manland:
        primary = "Manland"
    elif is_pathway:
        primary = "Pathway"
    elif is_tri_color:
        if is_domain_land:
            primary = "Domain Land"
        elif is_filter_tri:
            primary = "Filter Tri-Land"
        elif has_sacrifice and "search your library" not in lowered:
            primary = "Sac Tri-Land"
        else:
            primary = "Tap Tri-Land"
    elif is_dual_color:
        if is_shock_land:
            primary = "Shock Land"
        elif is_battle_land:
            primary = "Battle Land"
        elif is_fast_land:
            primary = "Fast Land"
        elif is_pain_land:
            primary = "Pain Land"
        elif is_check_land:
            primary = "Check Land"
        elif is_reveal_land:
            primary = "Reveal Land"
        elif is_slow_land:
            primary = "Slow Land"
        elif is_filter_land:
            primary = "Filter Land"
        elif is_scry_land:
            primary = "Scry Land"
        elif is_gain_land:
            primary = "Gain Land"
        elif is_cycling_dual:
            primary = "Cycling Dual"
        else:
            primary = "Tap Dual" if enters_tapped else "True Dual"
    elif is_command_tower:
        primary = "Command Tower Style"
    elif is_city_of_brass:
        primary = "City of Brass Style"
    elif is_vivid:
        primary = "Vivid Land"
    elif is_conditional_any:
        primary = "Conditional Any-Color"
    elif is_rainbow:
        primary = "Rainbow Land"
    else:
        if "graveyard" in lowered:
            primary = "Graveyard Utility"
        elif "create" in lowered and "token" in lowered:
            primary = "Token / Creature Utility"
        elif "draw" in lowered or "look at" in lowered or "add {c}{c}" in lowered:
            primary = "Card Advantage Utility"
        elif (
            "destroy target" in lowered
            or "exile target" in lowered
            or "tap target" in lowered
            or "creatures can't" in lowered
            or "players can't" in lowered
        ):
            primary = "Hate / Control Utility"
        else:
            primary = "Card Advantage Utility"

    return {
        "name": name or "",
        "primary_land_category": primary,
        "tags": _normalize_tag_list(tags),
    }


def is_land_card(card) -> bool:
    type_line = _get_field(card, "type_line").lower()
    return "land" in type_line


def get_land_tags_for_card(card) -> List[str]:
    classification = classify_land(card)
    if not classification:
        return []
    tags = [classification["primary_land_category"]] + list(classification.get("tags") or [])
    return _normalize_tag_list(tags)


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
    if "create" in text and "token" in text:
        roles.add("tokens")
    if "add {" in text or "mana pool" in text or "untap target land" in text:
        roles.add("ramp")
    if "search your library" in text:
        roles.add("tutor")
    if "return target" in text and "graveyard" in text:
        roles.add("recursion")
    if "draw" in text and "card" in text:
        roles.add("draw")
    if (
        "destroy target" in text
        or "exile target" in text
        or "sacrifice target" in text
        or "fight" in text
        or ("deals damage equal to its power" in text and "target creature" in text)
    ):
        roles.add("removal")
    if "indestructible" in text or "hexproof" in text or "ward" in text or "prevent all damage" in text or "phase out" in text:
        roles.add("protection")
    if "exile target" in text and "return" in text:
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
    # Simple fallback: if nothing matched, default to utility.
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
    if is_land_card(card):
        return ["land"]
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
        "recursion": "recursion",
        "stax": "stax",
        "utility": "utility",
    }
    return parent_map


def get_subroles_for_card(card) -> List[str]:
    if is_land_card(card):
        land_tags = get_land_tags_for_card(card)
        return _normalize_set([f"land:{tag}" for tag in land_tags])
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
