"""Deck tag taxonomy used for classifying deck folders."""

from __future__ import annotations

from collections import OrderedDict
import re
from typing import Dict, Iterable, List, Optional, Tuple

from services.edhrec import slugify_theme


DeckTagGroups = OrderedDict[str, List[str]]


DECK_TAG_GROUPS: DeckTagGroups = OrderedDict(
    [
        (
            "Core Archetypes",
            [
                "Aggro",
                "Big Mana",
                "cEDH",
                "Combo",
                "Control",
                "Creatureless",
                "Glass Cannon",
                "Good Stuff",
                "Midrange",
                "Prison",
                "Ramp",
                "Rock",
                "Stax",
                "Tempo",
                "Toolbox",
                "Voltron",
                "Zoo",
            ],
        ),
        (
            "Mechanics and Resources",
            [
                "+1/+1 Counters",
                "-1/-1 Counters",
                "Activated Abilities",
                "Affinity",
                "Anthems",
                "Auras",
                "Artifacts",
                "Blink",
                "Blood",
                "Bounce",
                "Card Draw",
                "Charge Counters",
                "Clones",
                "Clues",
                "Colorless",
                "Combat-Focused",
                "Convoke",
                "Counters",
                "Devotion",
                "Discard",
                "Energy",
                "Enchantress",
                "Equipment",
                "ETB",
                "Exile",
                "Explore",
                "Flashback",
                "Food",
                "Foretell",
                "Graveyard",
                "Historic",
                "Improvise",
                "Kicker",
                "Land Animation",
                "Land Destruction",
                "Landfall",
                "Lands Matter",
                "Lifegain",
                "Lifedrain",
                "Life Exchange",
                "Madness",
                "Modular",
                "Morph",
                "Mutate",
                "Oil Counters",
                "Pillow Fort",
                "Politics",
                "Populate",
                "Power Matters",
                "Proliferate",
                "Prowess",
                "Reanimator",
                "Sacrifice",
                "Self-Discard",
                "Self-Mill",
                "Spell Copy",
                "Spellslinger",
                "Storm",
                "Suspend",
                "Tap / Untap",
                "Tokens",
                "Topdeck",
                "Treasure",
                "Triggered Abilities",
                "Unblockable",
                "Warp",
                "Wheels",
                "X Spells",
            ],
        ),
        (
            "Tribal Themes",
            [
                "Advisors",
                "Angels",
                "Apes",
                "Artificers",
                "Atogs",
                "Astartes",
                "Barbarians",
                "Bats",
                "Bears",
                "Beasts",
                "Birds",
                "Cats",
                "Cephalids",
                "Clerics",
                "Constructs",
                "Crabs",
                "Demons",
                "Devils",
                "Dinosaurs",
                "Dogs",
                "Dragons",
                "Drakes",
                "Druids",
                "Dwarves",
                "Eggs",
                "Elders",
                "Eldrazi",
                "Elementals",
                "Elephants",
                "Elves",
                "Faeries",
                "Foxes",
                "Frogs",
                "Fungi",
                "Giants",
                "Gnomes",
                "Goblins",
                "Golems",
                "Gorgons",
                "Griffins",
                "Halflings",
                "Hippogriffs",
                "Horses",
                "Humans",
                "Hydras",
                "Illusions",
                "Insects",
                "Kaheera Companion",
                "Keruga Companion",
                "Knights",
                "Kor",
                "Lhurgoyfs",
                "Lizards",
                "Lurrus Companion",
                "Merfolk",
                "Minotaurs",
                "Monkeys",
                "Monks",
                "Myr",
                "Necrons",
                "Ninjas",
                "Ogres",
                "Otters",
                "Oozes",
                "Orcs",
                "Phyrexians",
                "Phoenixes",
                "Pirates",
                "Praetors",
                "Rabbits",
                "Raccoons",
                "Rats",
                "Relentless Rats",
                "Rogues",
                "Samurai",
                "Scarecrows",
                "Servos",
                "Shades",
                "Shamans",
                "Sharks",
                "Skeletons",
                "Slivers",
                "Snakes",
                "Soldiers",
                "Specters",
                "Spiders",
                "Sphinxes",
                "Spirits",
                "Squid",
                "Squirrels",
                "Thopters",
                "Treefolk",
                "Turtles",
                "Vampires",
                "Warriors",
                "Werewolves",
                "Whales",
                "Wizards",
                "Wolves",
                "Wurms",
                "Zombies",
            ],
        ),
        (
            "Special Card Synergies",
            [
                "Adventures",
                "Attractions",
                "Battles",
                "Commander Matters",
                "Companions",
                "Crime",
                "Curses",
                "Dungeons",
                "Experience Counters",
                "Legendary Matters",
                "Monarch",
                "Multicolor Matters",
                "Planeswalkers",
                "Sagas",
                "Shrines",
                "Stickers",
                "The Ring",
            ],
        ),
        (
            "Play Patterns and Win Conditions",
            [
                "Ad Nauseam",
                "Aristocrats",
                "Blue Moon",
                "Burn",
                "Chaos",
                "Cheerios",
                "Flash",
                "Group Hug",
                "Group Slug",
                "Hatebears",
                "Infect",
                "Mill",
                "Politics",
                "Primal Surge",
                "Self-Damage",
                "Self-Destruct",
                "Stompy",
                "Turbo Fog",
            ],
        ),
        (
            "Advanced / Experimental Mechanics",
            [
                "Cascade",
                "Connive",
                "Delirium",
                "Descend",
                "Discover",
                "Evoke",
                "Exploit",
                "Heroic",
                "Paradox",
                "Surveil",
                "Unearth",
            ],
        ),
        (
            "Keywords and Combat",
            [
                "Deathtouch",
                "Defenders",
                "Double Strike",
                "Exalted",
                "Extra Combats",
                "First Strike",
                "Flying",
                "Haste",
                "Hexproof",
                "Indestructible",
                "Menace",
                "Ninjutsu",
                "Reach",
                "Shadow",
                "Skulk",
                "Trample",
                "Vigilance",
            ],
        ),
        (
            "Flavor and Miscellaneous",
            [
                "Aikido",
                "Color Hack",
                "Cute Tribes",
                "Dungeon",
                "Old School",
                "Type Hack",
                "Unnatural",
            ],
        ),
    ]
)


def all_deck_tags() -> Tuple[str, ...]:
    return tuple(tag for tag in _iterate_tags(DECK_TAG_GROUPS.values()))


def _iterate_tags(groups: Iterable[Iterable[str]]) -> Iterable[str]:
    for group in groups:
        for tag in group:
            yield tag


ALL_DECK_TAGS: Tuple[str, ...] = all_deck_tags()
VALID_DECK_TAGS = frozenset(ALL_DECK_TAGS)
TAG_CATEGORY_MAP: dict[str, str] = {}
for _category, _tags in DECK_TAG_GROUPS.items():
    for _tag in _tags:
        TAG_CATEGORY_MAP.setdefault(_tag, _category)
del _category, _tags, _tag


SLUG_TO_DECK_TAG: Dict[str, str] = {}
for _tag in ALL_DECK_TAGS:
    _slug = slugify_theme(_tag)
    if _slug:
        SLUG_TO_DECK_TAG.setdefault(_slug, _tag)
del _tag, _slug


def resolve_deck_tag_from_slug(slug: str) -> Optional[str]:
    """
    Map an EDHREC theme slug back to one of our canonical deck tags.

    EDHREC occasionally appends population markers (e.g., ``lifegain2-6k``);
    stripping the numeric suffix lets us align with our taxonomy.
    """

    if not slug:
        return None

    lowered = slug.strip().lower()
    if not lowered:
        return None

    match = SLUG_TO_DECK_TAG.get(lowered)
    if match:
        return match

    trimmed = re.sub(r"-?\d.*$", "", lowered)
    if trimmed:
        match = SLUG_TO_DECK_TAG.get(trimmed)
        if match:
            return match

    return None
