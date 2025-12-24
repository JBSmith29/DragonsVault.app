"""Rules for deriving deck tags and evergreen tags from oracle data."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import json
from pathlib import Path
import re
from typing import Iterable, Pattern, Sequence, Set

from services.deck_tags import ALL_DECK_TAGS, VALID_DECK_TAGS, TAG_CATEGORY_MAP, DECK_TAG_GROUPS


_EVERGREEN_TAGS_PATH = Path(__file__).resolve().parents[1] / "evergreen" / "evergreen_tags_v1.json"
_EVERGREEN_ENGINE_PATH = Path(__file__).resolve().parents[1] / "evergreen" / "evergreen_detection_engine_v1.json"
_COMMENT_BLOCK_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_REMINDER_TEXT_RE = re.compile(r"\([^()]*\)")
_PARENS_RE = re.compile(r"[()]")
_WHITESPACE_RE = re.compile(r"\s+")
_SIMPLE_TOKEN_RE = re.compile(r"^[a-z0-9]+$")


@dataclass(frozen=True)
class EvergreenRule:
    tag: str
    regexes: tuple[Pattern[str], ...]
    requires: tuple[Pattern[str], ...]
    optional: tuple[Pattern[str], ...]
    color_hint: tuple[str, ...]


@dataclass(frozen=True)
class EvergreenExclusion:
    if_contains: tuple[str, ...]
    exclude_tags: tuple[str, ...]


@dataclass(frozen=True)
class EvergreenConfig:
    normalization: dict
    regex_mode: str
    phrase_match: str
    exclusions: tuple[EvergreenExclusion, ...]
    rules: tuple[EvergreenRule, ...]
    store_source: str


def _load_json_file(path: Path, *, allow_comments: bool = False) -> dict:
    try:
        raw = path.read_text(encoding="utf-8")
    except Exception:
        return {}
    if allow_comments:
        raw = _COMMENT_BLOCK_RE.sub("", raw)
    try:
        return json.loads(raw)
    except Exception:
        return {}


def _strip_reminder_text(text: str) -> str:
    out = text
    while True:
        cleaned = _REMINDER_TEXT_RE.sub(" ", out)
        if cleaned == out:
            return cleaned
        out = cleaned


def _normalize_text(text: str, normalization: dict) -> str:
    if not text:
        return ""
    out = str(text)
    if normalization.get("strip_reminder_text"):
        out = _strip_reminder_text(out)
    elif normalization.get("strip_parentheses"):
        out = _PARENS_RE.sub(" ", out)
    lowercase = normalization.get("lowercase", True)
    if lowercase:
        out = out.lower()
    for symbol, replacement in (normalization.get("symbol_map") or {}).items():
        if not symbol:
            continue
        key = symbol.lower() if lowercase else symbol
        out = out.replace(key, f" {replacement} ")
    out = _WHITESPACE_RE.sub(" ", out).strip()
    return out


def _compile_phrase(token: str) -> Pattern[str] | None:
    token = (token or "").strip().lower()
    if not token:
        return None
    if _SIMPLE_TOKEN_RE.match(token):
        return re.compile(r"\b" + re.escape(token) + r"\b")
    return re.compile(re.escape(token))


def _parse_regex_flags(flags: Iterable[str]) -> int:
    flag_map = {
        "IGNORECASE": re.IGNORECASE,
        "MULTILINE": re.MULTILINE,
        "DOTALL": re.DOTALL,
    }
    value = 0
    for name in flags or []:
        key = str(name).upper()
        value |= flag_map.get(key, 0)
    return value


def _compile_regex_list(values: Iterable[str], flags: int) -> tuple[Pattern[str], ...]:
    compiled = []
    for value in values or []:
        if not value:
            continue
        try:
            compiled.append(re.compile(value, flags))
        except re.error:
            continue
    return tuple(compiled)


@lru_cache(maxsize=1)
def _load_evergreen_config() -> EvergreenConfig:
    tags_data = _load_json_file(_EVERGREEN_TAGS_PATH, allow_comments=True)
    engine_data = _load_json_file(_EVERGREEN_ENGINE_PATH)

    normalization = tags_data.get("normalization") or engine_data.get("normalization") or {}
    engine = tags_data.get("engine") or {}
    detection_rules = engine_data.get("detection_rules") or {}
    regex_config = engine.get("regex") or detection_rules.get("regex") or {}
    phrase_config = engine.get("phrases") or detection_rules.get("phrases") or {}
    output_config = engine.get("output") or engine_data.get("output") or {}

    regex_mode = (regex_config.get("mode") or "search").lower()
    phrase_match = (phrase_config.get("match") or "all").lower()
    regex_flags = _parse_regex_flags(regex_config.get("flags") or [])
    store_source = output_config.get("store_source") or "derived"

    exclusions = []
    for entry in engine_data.get("exclusions") or []:
        if not isinstance(entry, dict):
            continue
        if_contains = tuple(
            str(token).lower()
            for token in (entry.get("if_contains") or [])
            if isinstance(token, str) and token.strip()
        )
        exclude_tags = tuple(
            str(tag)
            for tag in (entry.get("exclude_tags") or [])
            if isinstance(tag, str) and tag.strip()
        )
        if if_contains and exclude_tags:
            exclusions.append(EvergreenExclusion(if_contains=if_contains, exclude_tags=exclude_tags))

    rules = []
    for entry in tags_data.get("tags") or []:
        if not isinstance(entry, dict):
            continue
        tag = (entry.get("tag") or "").strip()
        if not tag:
            continue
        detect = entry.get("detect") or {}
        regex_values = detect.get("regex")
        if isinstance(regex_values, str):
            regex_values = [regex_values]
        elif not isinstance(regex_values, (list, tuple)):
            regex_values = []
        regexes = _compile_regex_list(
            [val for val in regex_values if isinstance(val, str)],
            regex_flags,
        )
        requires = tuple(
            token for token in (_compile_phrase(v) for v in (detect.get("requires") or [])) if token
        )
        optional = tuple(
            token for token in (_compile_phrase(v) for v in (detect.get("optional") or [])) if token
        )
        color_hint = tuple(
            str(c).upper() for c in (detect.get("color_hint") or []) if isinstance(c, str) and c.strip()
        )
        rules.append(EvergreenRule(tag=tag, regexes=regexes, requires=requires, optional=optional, color_hint=color_hint))

    return EvergreenConfig(
        normalization=normalization,
        regex_mode=regex_mode,
        phrase_match=phrase_match,
        exclusions=tuple(exclusions),
        rules=tuple(rules),
        store_source=store_source,
    )


_IRREGULAR_PLURALS = {
    "elves": "elf",
    "wolves": "wolf",
    "dwarves": "dwarf",
    "faeries": "faerie",
    "phyrexians": "phyrexian",
    "werewolves": "werewolf",
    "humans": "human",
    "sphinxes": "sphinx",
    "kraken": "kraken",
}


def _singularize(word: str) -> str:
    lowered = word.lower()
    if lowered in _IRREGULAR_PLURALS:
        return _IRREGULAR_PLURALS[lowered]
    if lowered.endswith("ies") and len(lowered) > 3:
        return lowered[:-3] + "y"
    if lowered.endswith("ves") and len(lowered) > 3:
        return lowered[:-3] + "f"
    if lowered.endswith("es") and len(lowered) > 3:
        return lowered[:-2]
    if lowered.endswith("s") and len(lowered) > 2:
        return lowered[:-1]
    return lowered


def _build_tribal_lookup() -> dict[str, str]:
    tribe_tags = DECK_TAG_GROUPS.get("Tribal Themes", [])
    lookup: dict[str, str] = {}
    for tag in tribe_tags:
        key = tag.lower()
        lookup.setdefault(key, tag)
        parts = key.split()
        if parts:
            singular = " ".join(parts[:-1] + [_singularize(parts[-1])])
            lookup.setdefault(singular, tag)
    return lookup


TRIBAL_LOOKUP = _build_tribal_lookup()


@dataclass(frozen=True)
class TagRule:
    tag: str
    keywords: tuple[str, ...] = ()
    text: tuple[str, ...] = ()
    type_line: tuple[str, ...] = ()
    roles: tuple[str, ...] = ()


TAG_RULES: Sequence[TagRule] = (
    TagRule(tag="Artifacts", type_line=("artifact",)),
    TagRule(tag="Auras", type_line=("aura",)),
    TagRule(tag="Equipment", type_line=("equipment",)),
    TagRule(tag="Planeswalkers", type_line=("planeswalker",)),
    TagRule(tag="Sagas", type_line=("saga",)),
    TagRule(tag="Shrines", type_line=("shrine",)),
    TagRule(tag="Battles", type_line=("battle",)),
    TagRule(tag="Curses", type_line=("curse",)),
    TagRule(tag="Legendary Matters", type_line=("legendary",)),
    TagRule(tag="Clues", keywords=("investigate",), text=("clue token",)),
    TagRule(tag="Food", text=("food token",)),
    TagRule(tag="Blood", text=("blood token",)),
    TagRule(tag="Treasure", text=("treasure token", "create a treasure")),
    TagRule(tag="Tokens", text=("create a token", "token creature")),
    TagRule(tag="Lifegain", text=("gain life", "lifelink"), roles=("lifegain",)),
    TagRule(tag="Lifedrain", text=("opponent loses life", "each opponent loses life", "each player loses life")),
    TagRule(tag="Life Exchange", text=("exchange life", "swap life")),
    TagRule(tag="Discard", text=("discard",), roles=("discard",)),
    TagRule(tag="Self-Discard", text=("discard a card", "discard your hand")),
    TagRule(tag="Graveyard", text=("graveyard",), roles=("recursion",)),
    TagRule(tag="Reanimator", text=("return target creature card from your graveyard to the battlefield", "reanimate")),
    TagRule(tag="Sacrifice", text=("sacrifice",), roles=("sacrifice outlet",)),
    TagRule(tag="Card Draw", text=("draw a card", "draw two cards", "draw cards"), roles=("draw",)),
    TagRule(tag="Ramp", roles=("ramp",)),
    TagRule(tag="Stax", roles=("stax",)),
    TagRule(tag="Protection", text=("hexproof", "indestructible", "ward", "protection from"), roles=("protection",)),
    TagRule(tag="Combat-Focused", roles=("combat",)),
    TagRule(tag="Wheels", text=("each player discards", "discard their hand", "then draws")),
)


ROLE_TO_TAG = {
    "ramp": "Ramp",
    "draw": "Card Draw",
    "lifegain": "Lifegain",
    "discard": "Discard",
    "tokens": "Tokens",
    "stax": "Stax",
    "sacrifice outlet": "Sacrifice",
    "recursion": "Graveyard",
    "protection": "Protection",
    "combat": "Combat-Focused",
    "counterspells": "Control",
    "removal": "Control",
}


def _normalize_keywords(values: Iterable[str]) -> Set[str]:
    normalized: Set[str] = set()
    for kw in values or []:
        if not isinstance(kw, str):
            continue
        norm = kw.strip().lower()
        if norm:
            normalized.add(norm)
    return normalized


def evergreen_source() -> str:
    return _load_evergreen_config().store_source


def _matches_required(patterns: Sequence[Pattern[str]], text: str, mode: str) -> bool:
    if not patterns or not text:
        return False
    if mode == "any":
        return any(rx.search(text) for rx in patterns)
    return all(rx.search(text) for rx in patterns)


def _matches_regex(regexes: Sequence[Pattern[str]], targets: Sequence[str], mode: str) -> bool:
    if not regexes or not targets:
        return False
    for rx in regexes:
        for target in targets:
            if not target:
                continue
            if mode == "match":
                if rx.match(target):
                    return True
            else:
                if rx.search(target):
                    return True
    return False


def _format_tag(tag: str) -> str:
    return (tag or "").replace("_", " ").replace("-", " ").strip().lower()


def derive_evergreen_keywords(
    *,
    oracle_text: str | None,
    keywords: Iterable[str],
    typals: Iterable[str] | None = None,
    type_line: str | None = None,
    name: str | None = None,
    colors: Iterable[str] | None = None,
) -> Set[str]:
    """Return evergreen tags for an oracle entry."""
    config = _load_evergreen_config()
    normalization = config.normalization

    name_text = _normalize_text(name or "", normalization)
    type_text = _normalize_text(type_line or "", normalization)
    oracle_text_norm = _normalize_text(oracle_text or "", normalization)
    keyword_texts = [
        _normalize_text(kw, normalization) for kw in keywords or [] if isinstance(kw, str)
    ]
    keyword_texts = [kw for kw in keyword_texts if kw]

    combined = " ".join(part for part in (name_text, type_text, oracle_text_norm, " ".join(keyword_texts)) if part)
    targets = [t for t in (name_text, type_text, oracle_text_norm) if t] + keyword_texts

    keyword_set: Set[str] = set()
    for kw in keyword_texts:
        keyword_set.add(kw)
        keyword_set.add(kw.replace(" ", "_"))

    color_set = {str(color).upper() for color in (colors or []) if color}

    evergreen: Set[str] = set()
    for rule in config.rules:
        formatted_tag = _format_tag(rule.tag)
        if rule.color_hint and not (color_set & set(rule.color_hint)):
            continue
        if rule.tag in keyword_set or formatted_tag in keyword_set:
            evergreen.add(formatted_tag)
            continue
        if rule.regexes and _matches_regex(rule.regexes, targets, config.regex_mode):
            evergreen.add(formatted_tag)
            continue
        if rule.requires and _matches_required(rule.requires, combined, config.phrase_match):
            evergreen.add(formatted_tag)
            continue
        if rule.optional and _matches_required(rule.optional, combined, "any"):
            evergreen.add(formatted_tag)

    if config.exclusions and combined:
        combined_lower = combined.lower()
        for exclusion in config.exclusions:
            if any(token in combined_lower for token in exclusion.if_contains):
                for tag in exclusion.exclude_tags:
                    evergreen.discard(_format_tag(tag))

    if typals:
        evergreen.update(_normalize_keywords(typals))
    return evergreen


def derive_deck_tags(
    *,
    oracle_text: str | None,
    type_line: str | None,
    keywords: Iterable[str],
    typals: Iterable[str],
    roles: Iterable[str],
) -> Set[str]:
    """Return deck tags derived from oracle fields."""
    kw_set = _normalize_keywords(keywords)
    role_set = _normalize_keywords(roles)
    text = (oracle_text or "").lower()
    type_line_lower = (type_line or "").lower()

    tags: Set[str] = set()

    # Direct keyword matches (e.g., "Landfall", "Prowess").
    for tag in ALL_DECK_TAGS:
        if tag.lower() in kw_set:
            tags.add(tag)

    # Tribal tags from typal detection.
    for typal in typals or []:
        if not isinstance(typal, str):
            continue
        match = TRIBAL_LOOKUP.get(typal.strip().lower())
        if match:
            tags.add(match)

    # Explicit rules.
    for rule in TAG_RULES:
        if rule.keywords and kw_set.intersection(rule.keywords):
            tags.add(rule.tag)
            continue
        if rule.roles and role_set.intersection(rule.roles):
            tags.add(rule.tag)
            continue
        if rule.type_line and any(token in type_line_lower for token in rule.type_line):
            tags.add(rule.tag)
            continue
        if rule.text and any(token in text for token in rule.text):
            tags.add(rule.tag)

    # Role-driven tags.
    for role in role_set:
        mapped = ROLE_TO_TAG.get(role)
        if mapped:
            tags.add(mapped)

    # Ensure only canonical tags survive.
    tags = {tag for tag in tags if tag in VALID_DECK_TAGS}
    return tags


def deck_tag_category(tag: str) -> str | None:
    """Return the category for a deck tag when known."""
    return TAG_CATEGORY_MAP.get(tag)


def ensure_fallback_tag(deck_tags: Set[str], evergreen: Set[str], *, fallback_tag: str = "Good Stuff") -> Set[str]:
    """Ensure at least one deck tag or evergreen keyword is present."""
    if deck_tags or evergreen:
        return set(deck_tags)
    if fallback_tag in VALID_DECK_TAGS:
        return {fallback_tag}
    return set(deck_tags)
