"""Detect Magic keyword abilities in oracle text and link them to rules.

The comprehensive rules index in :mod:`rules_cache` is our source of truth
for rule numbers and text. This module supplements it with a curated map of
keyword abilities to the rule numbers that define them, so UI surfaces can
show an inline "what does this do?" link beside any keyword found on a card.

Matches are case-insensitive, operate on word boundaries, and intentionally
skip text inside reminder parentheses so we don't double-count the keyword
inside its own reminder text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from core.shared.utils.rules_cache import lookup_magic_rule


__all__ = [
    "KEYWORD_RULE_INDEX",
    "KeywordMatch",
    "find_keyword_abilities",
    "attach_rule_snippets",
]


#: Map of common Magic keyword abilities to the comprehensive-rules number
#: that introduces them. The list focuses on keywords that need explanation
#: (not "Flying" vs "Reach" which most players know); expanding the map in
#: the future is a safe additive change.
KEYWORD_RULE_INDEX: dict[str, str] = {
    "Absorb": "702.64",
    "Affinity": "702.41",
    "Aftermath": "702.127",
    "Amplify": "702.37",
    "Annihilator": "702.85",
    "Ascend": "702.131",
    "Aura Swap": "702.64",
    "Awaken": "702.113",
    "Banding": "702.22",
    "Battle Cry": "702.91",
    "Blitz": "702.152",
    "Bloodthirst": "702.54",
    "Buyback": "702.26",
    "Cascade": "702.84",
    "Changeling": "702.72",
    "Cipher": "702.99",
    "Commander ninjutsu": "702.49",
    "Companion": "702.139",
    "Compleated": "702.149",
    "Conspire": "702.78",
    "Convoke": "702.50",
    "Corrupted": "702.164",
    "Craft": "702.159",
    "Cycling": "702.29",
    "Dash": "702.109",
    "Daybound": "702.144",
    "Deathtouch": "702.2",
    "Defender": "702.3",
    "Delve": "702.66",
    "Demonstrate": "702.144",
    "Devoid": "702.114",
    "Devour": "702.82",
    "Disturb": "702.147",
    "Doctor's companion": "702.164",
    "Dredge": "702.52",
    "Echo": "702.30",
    "Embalm": "702.128",
    "Emerge": "702.119",
    "Enchant": "702.5",
    "Encore": "702.141",
    "Entwine": "702.41",
    "Epic": "702.49",
    "Equip": "702.6",
    "Escalate": "702.118",
    "Escape": "702.137",
    "Evoke": "702.73",
    "Evolve": "702.100",
    "Exalted": "702.82",
    "Exploit": "702.106",
    "Explore": "701.39",
    "Extort": "702.100",
    "Fabricate": "702.123",
    "Fading": "702.32",
    "Fateful hour": "702.125",
    "Fear": "702.36",
    "First strike": "702.7",
    "Flanking": "702.25",
    "Flash": "702.8",
    "Flashback": "702.33",
    "Flying": "702.9",
    "Food": "701.52",
    "Foretell": "702.143",
    "Fortify": "702.67",
    "Freerunning": "702.163",
    "Frenzy": "702.68",
    "Friends forever": "702.147",
    "Gift": "702.163",
    "Graft": "702.58",
    "Gravestorm": "702.68",
    "Haste": "702.10",
    "Haunt": "702.55",
    "Heroic": "702.86",
    "Hexproof": "702.11",
    "Hidden agenda": "702.105",
    "Hideaway": "702.75",
    "Horsemanship": "702.31",
    "Ingest": "702.116",
    "Improvise": "702.126",
    "Indestructible": "702.12",
    "Infect": "702.90",
    "Inspired": "702.103",
    "Intimidate": "702.13",
    "Investigate": "701.38",
    "Jump-start": "702.134",
    "Kicker": "702.32",
    "Landfall": "702.46",
    "Landwalk": "702.14",
    "Learn": "701.46",
    "Level Up": "702.87",
    "Lifelink": "702.15",
    "Living weapon": "702.91",
    "Madness": "702.35",
    "Melee": "702.121",
    "Menace": "702.110",
    "Mentor": "702.96",
    "Miracle": "702.93",
    "Modular": "702.42",
    "Morph": "702.36",
    "Mutate": "702.136",
    "Myriad": "702.116",
    "Ninjutsu": "702.49",
    "Offering": "702.48",
    "Outlast": "702.107",
    "Overload": "702.95",
    "Partner": "702.124",
    "Persist": "702.78",
    "Phasing": "702.25",
    "Plot": "702.164",
    "Populate": "701.28",
    "Poisonous": "702.69",
    "Proliferate": "701.24",
    "Protection": "702.16",
    "Prowess": "702.108",
    "Prowl": "702.74",
    "Provoke": "702.38",
    "Rampage": "702.23",
    "Ravenous": "702.157",
    "Reach": "702.17",
    "Rebound": "702.88",
    "Recover": "702.57",
    "Reinforce": "702.76",
    "Renown": "702.111",
    "Replicate": "702.59",
    "Retrace": "702.79",
    "Riot": "702.135",
    "Ripple": "702.61",
    "Scavenge": "702.94",
    "Scry": "701.19",
    "Shadow": "702.27",
    "Shroud": "702.18",
    "Skulk": "702.117",
    "Soulbond": "702.92",
    "Soulshift": "702.43",
    "Spectacle": "702.133",
    "Splice": "702.46",
    "Split second": "702.60",
    "Storm": "702.39",
    "Sunburst": "702.43",
    "Surveil": "701.43",
    "Suspend": "702.61",
    "Toxic": "702.164",
    "Training": "702.152",
    "Trample": "702.19",
    "Transmute": "702.49",
    "Treasure": "701.53",
    "Tribute": "702.101",
    "Typecycling": "702.29",
    "Umbra armor": "702.76",
    "Undying": "702.93",
    "Unearth": "702.84",
    "Unleash": "702.97",
    "Vanishing": "702.62",
    "Vigilance": "702.20",
    "Ward": "702.21",
    "Wither": "702.80",
}


#: Pre-compiled combined regex ordered longest-first so "Doctor's companion"
#: matches before "companion".
_KEYWORD_PATTERN = re.compile(
    r"\b(?:%s)\b"
    % "|".join(
        re.escape(keyword)
        for keyword in sorted(KEYWORD_RULE_INDEX.keys(), key=len, reverse=True)
    ),
    re.IGNORECASE,
)
#: Matches reminder-text parentheses so we can remove them before scanning.
_REMINDER_TEXT_PATTERN = re.compile(r"\([^)]*\)")


@dataclass
class KeywordMatch:
    keyword: str
    rule_number: str
    rule_text: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "keyword": self.keyword,
            "rule_number": self.rule_number,
            "rule_text": self.rule_text,
        }


def _canonical_keyword(match_text: str) -> str | None:
    needle = match_text.strip().lower()
    for keyword in KEYWORD_RULE_INDEX:
        if keyword.lower() == needle:
            return keyword
    return None


def find_keyword_abilities(oracle_text: str | None) -> list[KeywordMatch]:
    """Return unique keyword matches found in ``oracle_text``.

    Reminder text in parentheses is stripped before scanning so keywords
    inside reminder text don't trigger spurious matches.
    """
    if not oracle_text:
        return []
    cleaned = _REMINDER_TEXT_PATTERN.sub(" ", oracle_text)
    seen: dict[str, KeywordMatch] = {}
    for match in _KEYWORD_PATTERN.finditer(cleaned):
        canonical = _canonical_keyword(match.group(0))
        if not canonical or canonical in seen:
            continue
        rule_number = KEYWORD_RULE_INDEX[canonical]
        seen[canonical] = KeywordMatch(keyword=canonical, rule_number=rule_number)
    return list(seen.values())


def attach_rule_snippets(matches: Iterable[KeywordMatch]) -> list[KeywordMatch]:
    """Fill in ``rule_text`` for each match using the comprehensive rules."""
    hydrated: list[KeywordMatch] = []
    for match in matches:
        text = lookup_magic_rule(match.rule_number) if match.rule_number else None
        hydrated.append(
            KeywordMatch(
                keyword=match.keyword,
                rule_number=match.rule_number,
                rule_text=text,
            )
        )
    return hydrated
