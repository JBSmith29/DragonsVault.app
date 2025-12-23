"""Rules for deriving deck tags and evergreen keywords from oracle data."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable, Sequence, Set

from services.deck_tags import ALL_DECK_TAGS, VALID_DECK_TAGS, TAG_CATEGORY_MAP, DECK_TAG_GROUPS


# Evergreen keywords (source: draftsim.com/mtg-evergreen/)
EVERGREEN_KEYWORDS: Set[str] = {
    "deathtouch",
    "defender",
    "double strike",
    "enchant",
    "equip",
    "first strike",
    "flash",
    "flying",
    "goad",
    "haste",
    "hexproof",
    "indestructible",
    "lifelink",
    "menace",
    "protection",
    "reach",
    "trample",
    "vigilance",
    "ward",
}

_EVERGREEN_TEXT_HINTS = {
    "protection": ("protection from",),
    "goad": ("goad",),
}

_EVERGREEN_TEXT_PATTERNS = {
    "deathtouch": (r"\bdeathtouch\b",),
    "defender": (r"\bdefender\b",),
    "double strike": (r"\bdouble strike\b",),
    "enchant": (r"\benchant\b",),
    "equip": (r"\bequip\b",),
    "first strike": (r"\bfirst strike\b",),
    "flash": (r"\bflash\b",),
    "flying": (r"\bflying\b",),
    "goad": (r"\bgoad\b", r"\bgoaded\b"),
    "haste": (r"\bhaste\b",),
    "hexproof": (r"\bhexproof\b",),
    "indestructible": (r"\bindestructible\b",),
    "lifelink": (r"\blifelink\b",),
    "menace": (r"\bmenace\b",),
    "protection": (r"\bprotection from\b",),
    "reach": (r"\breach\b",),
    "trample": (r"\btrample\b",),
    "vigilance": (r"\bvigilance\b",),
    "ward": (r"\bward\b",),
}

_EVERGREEN_TEXT_REGEX = {
    keyword: tuple(re.compile(pattern) for pattern in patterns)
    for keyword, patterns in _EVERGREEN_TEXT_PATTERNS.items()
}


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


def derive_evergreen_keywords(*, oracle_text: str | None, keywords: Iterable[str]) -> Set[str]:
    """Return evergreen keywords for an oracle entry."""
    kw_set = _normalize_keywords(keywords)
    evergreen = {kw for kw in kw_set if kw in EVERGREEN_KEYWORDS}
    text = (oracle_text or "").lower()
    for keyword, hints in _EVERGREEN_TEXT_HINTS.items():
        if any(hint in text for hint in hints):
            evergreen.add(keyword)
    for keyword, regexes in _EVERGREEN_TEXT_REGEX.items():
        if keyword in evergreen:
            continue
        for rx in regexes:
            if rx.search(text):
                evergreen.add(keyword)
                break
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
