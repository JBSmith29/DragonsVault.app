from __future__ import annotations

from services import edhrec_client as edhrec


def test_slugify_commander_handles_split_and_accents():
    slug = edhrec.slugify_commander("\u00c9trata, the Silencer // Backup")
    assert slug == "etrata-the-silencer-backup"


def test_commander_cardviews_filters_categories():
    payload = {
        "slug": "atraxa-praetors-voice",
        "name": "Atraxa, Praetors' Voice",
        "kind": "commander",
        "cardlists": [
            {
                "header": "Top Cards",
                "cardviews": [
                    {"name": "Sol Ring", "slug": "sol-ring", "rank": 1, "synergy": 35.5, "inclusion": 96.0},
                    {"name": "Farseek", "slug": "farseek", "rank": 2, "synergy": 12.0, "inclusion": 78.0},
                ],
            },
            {
                "header": "Signature Cards",
                "tag": "signature",
                "cardviews": [
                    {"name": "Deepglow Skate", "slug": "deepglow-skate", "rank": 1, "synergy": 54.0, "inclusion": 52.0},
                ],
            },
        ],
    }

    views = edhrec.commander_cardviews(payload, categories=["Top Cards"])
    assert len(views) == 2
    assert views[0].name == "Sol Ring"
    assert views[0].category == "Top Cards"
    assert views[1].rank == 2


def test_merge_cardviews_prefers_higher_synergy():
    first = edhrec.CardView(
        name="Sol Ring",
        slug="sol-ring",
        category="Top Cards",
        rank=1,
        source_kind="commander",
        synergy=30.0,
        inclusion=95.0,
        num_decks=None,
        potential_decks=None,
        url=None,
        label=None,
        trend_zscore=None,
        source="atraxa",
        source_label="Atraxa",
    )
    better_inclusion = edhrec.CardView(
        name="Sol Ring",
        slug="sol-ring",
        category="Signature",
        rank=1,
        source_kind="theme",
        synergy=30.0,
        inclusion=99.0,
        num_decks=None,
        potential_decks=None,
        url=None,
        label=None,
        trend_zscore=None,
        source="proliferate",
        source_label="Proliferate",
    )
    better_synergy = edhrec.CardView(
        name="Sol Ring",
        slug="sol-ring",
        category="Staples",
        rank=1,
        source_kind="theme",
        synergy=60.0,
        inclusion=50.0,
        num_decks=None,
        potential_decks=None,
        url=None,
        label=None,
        trend_zscore=None,
        source="staples",
        source_label="Staples",
    )

    merged = edhrec.merge_cardviews([first], [better_inclusion], [better_synergy])
    assert merged["sol-ring"].synergy == 60.0
    assert merged["sol-ring"].source_label == "Staples"
