"""Move deck tag vocabulary to database tables."""

from __future__ import annotations

import datetime as dt
import logging
import re
import unicodedata
from collections import OrderedDict

import sqlalchemy as sa
from alembic import op

revision = "0017_deck_tag_db"
down_revision = "0016_add_build_session_name"
branch_labels = None
depends_on = None

_LOG = logging.getLogger(__name__)

_SYSTEM_TAG_GROUPS = OrderedDict(
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
                "Forced Combat",
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
                "Gods",
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
                "Jellyfish",
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


def _slugify(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    text = text.replace("//", " ")
    text = text.replace("/", " ")
    text = text.replace("&", " and ")
    text = text.replace("@", " at ")
    text = text.replace("+", " plus ")
    text = text.replace("'", "")
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-{2,}", "-", text)
    return text.strip("-")


def _table_columns(inspector: sa.inspect, name: str) -> set[str]:
    return {col["name"] for col in inspector.get_columns(name)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    legacy_table = None

    if "deck_tags" in tables:
        cols = _table_columns(inspector, "deck_tags")
        if "name" not in cols or "slug" not in cols:
            legacy_table = "deck_tags_legacy"
            if legacy_table in tables:
                legacy_table = "deck_tags_legacy_v2"
            op.rename_table("deck_tags", legacy_table)
            tables.add(legacy_table)
            tables.discard("deck_tags")

    if "deck_tags" not in tables:
        op.create_table(
            "deck_tags",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("name", sa.String(length=128), nullable=False),
            sa.Column("slug", sa.String(length=160), nullable=False),
            sa.Column("source", sa.String(length=32), nullable=False),
            sa.Column("edhrec_category", sa.String(length=120), nullable=True),
            sa.Column("created_at", sa.DateTime, nullable=False),
            sa.Column("updated_at", sa.DateTime, nullable=False),
            sa.UniqueConstraint("name", name="uq_deck_tags_name"),
            sa.UniqueConstraint("slug", name="uq_deck_tags_slug"),
        )
        op.create_index("ix_deck_tags_name", "deck_tags", ["name"], unique=False)
        op.create_index("ix_deck_tags_slug", "deck_tags", ["slug"], unique=False)
        op.create_index("ix_deck_tags_source", "deck_tags", ["source"], unique=False)
        op.create_index(
            "ix_deck_tags_edhrec_category",
            "deck_tags",
            ["edhrec_category"],
            unique=False,
        )

    if "deck_tag_map" not in tables:
        op.create_table(
            "deck_tag_map",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("folder_id", sa.Integer, nullable=False),
            sa.Column("deck_tag_id", sa.Integer, nullable=False),
            sa.Column("confidence", sa.Float, nullable=True),
            sa.Column("source", sa.String(length=32), nullable=False),
            sa.Column("locked", sa.Boolean, nullable=False, server_default=sa.false()),
            sa.Column("created_at", sa.DateTime, nullable=False),
            sa.Column("updated_at", sa.DateTime, nullable=False),
            sa.ForeignKeyConstraint(["folder_id"], ["folder.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["deck_tag_id"], ["deck_tags.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("folder_id", "deck_tag_id", name="uq_deck_tag_map_folder_tag"),
        )
        op.create_index("ix_deck_tag_map_folder", "deck_tag_map", ["folder_id"], unique=False)
        op.create_index("ix_deck_tag_map_tag", "deck_tag_map", ["deck_tag_id"], unique=False)
        op.create_index("ix_deck_tag_map_source", "deck_tag_map", ["source"], unique=False)

    conn = bind
    now = dt.datetime.utcnow()
    deck_tags_table = sa.table(
        "deck_tags",
        sa.column("id", sa.Integer),
        sa.column("name", sa.String),
        sa.column("slug", sa.String),
        sa.column("source", sa.String),
        sa.column("edhrec_category", sa.String),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
    )

    existing_names = set()
    for row in conn.execute(sa.text("SELECT name FROM deck_tags")).fetchall():
        existing_names.add(str(row[0]).casefold())

    if legacy_table:
        legacy_rows = conn.execute(
            sa.text(
                f"SELECT tag, source, first_seen, last_seen FROM {legacy_table}"
            )
        ).fetchall()
        inserts = []
        for tag, source, first_seen, last_seen in legacy_rows:
            name = (tag or "").strip()
            if not name:
                continue
            key = name.casefold()
            if key in existing_names:
                continue
            existing_names.add(key)
            slug = _slugify(name)
            created_at = first_seen or now
            updated_at = last_seen or now
            inserts.append(
                {
                    "name": name,
                    "slug": slug,
                    "source": source or "system",
                    "edhrec_category": None,
                    "created_at": created_at,
                    "updated_at": updated_at,
                }
            )
        if inserts:
            conn.execute(deck_tags_table.insert(), inserts)

    inserts = []
    for category, tags in _SYSTEM_TAG_GROUPS.items():
        for tag in tags:
            name = (tag or "").strip()
            if not name:
                continue
            key = name.casefold()
            if key in existing_names:
                continue
            existing_names.add(key)
            inserts.append(
                {
                    "name": name,
                    "slug": _slugify(name),
                    "source": "system",
                    "edhrec_category": category,
                    "created_at": now,
                    "updated_at": now,
                }
            )
    if inserts:
        conn.execute(deck_tags_table.insert(), inserts)

    tag_lookup = {
        row[0].casefold(): row[1]
        for row in conn.execute(sa.text("SELECT name, id FROM deck_tags")).fetchall()
        if row[0]
    }

    folder_rows = conn.execute(
        sa.text(
            "SELECT id, deck_tag FROM folder WHERE deck_tag IS NOT NULL AND deck_tag != ''"
        )
    ).fetchall()
    map_inserts = []
    for folder_id, deck_tag in folder_rows:
        label = (deck_tag or "").strip()
        if not label:
            continue
        tag_id = tag_lookup.get(label.casefold())
        if not tag_id:
            conn.execute(
                deck_tags_table.insert(),
                {
                    "name": label,
                    "slug": _slugify(label),
                    "source": "user",
                    "edhrec_category": "User",
                    "created_at": now,
                    "updated_at": now,
                },
            )
            tag_id = conn.execute(
                sa.text("SELECT id FROM deck_tags WHERE name = :name"),
                {"name": label},
            ).scalar()
            tag_lookup[label.casefold()] = tag_id
        if not tag_id:
            continue
        map_inserts.append(
            {
                "folder_id": folder_id,
                "deck_tag_id": tag_id,
                "confidence": None,
                "source": "user",
                "locked": True,
                "created_at": now,
                "updated_at": now,
            }
        )
    if map_inserts:
        existing_pairs = {
            (row[0], row[1])
            for row in conn.execute(
                sa.text("SELECT folder_id, deck_tag_id FROM deck_tag_map")
            ).fetchall()
        }
        filtered = [
            row for row in map_inserts if (row["folder_id"], row["deck_tag_id"]) not in existing_pairs
        ]
        if not filtered:
            return
        map_table = sa.table(
            "deck_tag_map",
            sa.column("folder_id", sa.Integer),
            sa.column("deck_tag_id", sa.Integer),
            sa.column("confidence", sa.Float),
            sa.column("source", sa.String),
            sa.column("locked", sa.Boolean),
            sa.column("created_at", sa.DateTime),
            sa.column("updated_at", sa.DateTime),
        )
        conn.execute(map_table.insert(), filtered)


def downgrade() -> None:
    _LOG.info("Downgrade skipped for deck tag database tables.")
