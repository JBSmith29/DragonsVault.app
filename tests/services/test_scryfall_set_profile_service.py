from __future__ import annotations

import pytest

from core.domains.cards.services import scryfall_set_profile_service as set_profiles


@pytest.fixture(autouse=True)
def clear_profile_cache():
    set_profiles.clear_cached_set_profiles()
    yield
    set_profiles.clear_cached_set_profiles()


def test_set_profiles_build_curve_and_color_mix_summary():
    cache = [
        {
            "set": "abc",
            "name": "Red Mage",
            "type_line": "Creature — Human Wizard",
            "cmc": 2,
            "color_identity": ["R"],
        },
        {
            "set": "abc",
            "name": "Growth Spiral",
            "type_line": "Instant",
            "cmc": 5,
            "color_identity": ["G", "U"],
        },
        {
            "set": "abc",
            "name": "Mind Stone",
            "type_line": "Artifact",
            "mana_value": 3,
            "colors": [],
        },
        {
            "set": "abc",
            "name": "Forest",
            "type_line": "Basic Land — Forest",
            "cmc": 0,
            "color_identity": ["G"],
        },
        {
            "set": "abc",
            "name": "Saproling",
            "layout": "token",
            "type_line": "Token Creature — Saproling",
            "cmc": 0,
            "color_identity": ["G"],
        },
    ]
    ensure_calls = []

    profiles = set_profiles.set_profiles(
        ["abc", "missing"],
        cache=cache,
        ensure_cache_loaded_fn=lambda: ensure_calls.append("load") or True,
    )

    assert ensure_calls == ["load"]
    assert profiles["abc"] == {
        "avg_mv": 3.33,
        "curve_bucket": "mid",
        "dominant_colors": ["U", "R", "G"],
        "color_presence": ["U", "R", "G"],
        "color_mode": "multi",
        "nonland_spells": 3,
        "mono_cards": 1,
        "multicolor_cards": 1,
        "colorless_cards": 1,
        "color_counts": {"W": 0, "U": 1, "B": 0, "R": 1, "G": 1},
    }
    assert profiles["missing"] == {}


def test_clear_cached_set_profiles_forces_rebuild():
    first = set_profiles.set_profiles(
        ["one"],
        cache=[{"set": "one", "type_line": "Artifact", "cmc": 1}],
        ensure_cache_loaded_fn=lambda: True,
    )
    second = set_profiles.set_profiles(
        ["two"],
        cache=[{"set": "two", "type_line": "Artifact", "cmc": 6}],
        ensure_cache_loaded_fn=lambda: True,
    )

    assert first["one"]["avg_mv"] == 1.0
    assert second["two"] == {}

    set_profiles.clear_cached_set_profiles()

    rebuilt = set_profiles.set_profiles(
        ["two"],
        cache=[{"set": "two", "type_line": "Artifact", "cmc": 6}],
        ensure_cache_loaded_fn=lambda: True,
    )

    assert rebuilt["two"]["avg_mv"] == 6.0


def test_set_image_samples_filters_to_requested_set_and_uses_sampler():
    cache = [
        {
            "set": "neo",
            "name": "Sample One",
            "collector_number": "1",
            "lang": "en",
            "rarity": "rare",
            "image_uris": {"small": "small-1", "normal": "normal-1", "large": "large-1"},
        },
        {
            "set": "neo",
            "name": "Sample Two",
            "collector_number": "2",
            "lang": "en",
            "rarity": "common",
            "image_uris": {"small": "small-2", "normal": "normal-2", "large": None},
        },
        {
            "set": "neo",
            "name": "No Art",
            "collector_number": "3",
            "lang": "en",
            "rarity": "uncommon",
        },
        {
            "set": "bro",
            "name": "Other Set",
            "collector_number": "9",
            "lang": "en",
            "rarity": "mythic",
            "image_uris": {"small": "small-9", "normal": "normal-9", "large": None},
        },
    ]

    samples = set_profiles.set_image_samples(
        "neo",
        cache=cache,
        image_uris_fn=lambda card: {
            "small": (card.get("image_uris") or {}).get("small"),
            "normal": (card.get("image_uris") or {}).get("normal"),
            "large": (card.get("image_uris") or {}).get("large"),
        },
        per_set=1,
        sample_fn=lambda candidates, count: [candidates[-1]],
    )

    assert samples == [
        {
            "small": "small-2",
            "normal": "normal-2",
            "large": None,
            "name": "Sample Two",
            "collector_number": "2",
            "lang": "en",
            "rarity": "common",
        }
    ]
