from __future__ import annotations

from extensions import db
from models.role import Role, SubRole


CORE_ROLES = [
    "Ramp",
    "Card Draw",
    "Removal",
    "Protection",
    "Token Generation",
    "Recursion",
    "Tutor",
    "Lifegain",
    "Counterspell",
    "Discard",
    "Sacrifice Outlet",
    "Combat Support",
    "Stax",
    "Utility",
]

SUBROLES = {
    "Ramp": [
        "Mana Dork",
        "Mana Rock",
        "Land Ramp",
        "Treasure Ramp",
        "Ritual Mana",
        "Cost Reduction",
        "Mana Fixing",
        "Land Engine",
        "Land Tutor",
        "Bounce Land",
        "Fertile Land",
    ],
    "Land": [
        "Fetchland",
        "Shockland",
        "Triome",
        "Dual Land",
        "Fastland",
        "Slowland",
        "Painland",
        "Filterland",
        "Checkland",
        "Manland",
        "Karoo Land",
        "Utility Land",
        "Rainbow Land",
        "Basic Land",
    ],
    "Card Draw": [
        "Draw Engine",
        "Burst Draw",
        "Wheel",
        "Looting",
        "Impulse Draw",
        "Cantrip",
        "Investigate",
    ],
    "Removal": [
        "Spot Removal",
        "Board Wipe",
        "Damage Removal",
        "Fight Removal",
        "Exile Removal",
        "Flexible Removal",
    ],
    "Protection": [
        "Hexproof Grant",
        "Indestructible Grant",
        "Blink Protection",
        "Color Protection",
        "Spell Protection",
    ],
    "Token Generation": [
        "Creature Tokens",
        "Treasure Tokens",
        "Clue Tokens",
        "Food Tokens",
        "Blood Tokens",
        "Token Doubler",
    ],
    "Recursion": [
        "Creature Reanimation",
        "Regrowth",
        "Gravecast",
        "Mass Reanimation",
    ],
    "Tutor": [
        "Universal Tutor",
        "Creature Tutor",
        "Artifact Tutor",
        "Enchantment Tutor",
        "Land Tutor",
        "Spell Tutor",
        "Tribal Tutor",
    ],
    "Sacrifice Outlet": [
        "Free Sac",
        "Costed Sac",
        "Sac Payoff",
        "Death Trigger",
    ],
    "Combat Support": [
        "Haste",
        "Evasion",
        "Go Wide",
        "Go Tall",
        "Extra Combat",
        "Anthem",
    ],
    "Stax": [
        "Mana Tax",
        "Spell Tax",
        "Attack Tax",
        "Enter Tapped",
        "Rule of Law",
        "Ability Lock",
    ],
    "Utility": [
        "Utility Creature",
        "Utility Land",
        "Utility Artifact",
        "Card Filtering",
        "Bounce Tempo",
        "Topdeck Manipulation",
        "Global Enchantment",
    ],
}


def seed_roles_and_subroles() -> None:
    roles_by_name = {}
    for role_name in CORE_ROLES:
        existing = Role.query.filter_by(label=role_name).first()
        if not existing:
            existing = Role(key=role_name.lower().replace(" ", "_"), label=role_name, description=None)
            db.session.add(existing)
        roles_by_name[role_name] = existing

    for parent_name, children in SUBROLES.items():
        parent = roles_by_name.get(parent_name)
        if parent is None:
            parent = Role.query.filter_by(label=parent_name).first()
            if not parent:
                parent = Role(key=parent_name.lower().replace(" ", "_"), label=parent_name, description=None)
                db.session.add(parent)
            roles_by_name[parent_name] = parent
        for child in children:
            existing = SubRole.query.filter_by(role_id=parent.id, label=child).first()
            if existing:
                continue
            sr = SubRole(role_id=parent.id, key=child.lower().replace(" ", "_"), label=child, description=None)
            db.session.add(sr)

    db.session.commit()
