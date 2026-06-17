import json

import pytest

from core.domains.cards.services import scryfall_catalog_service as catalog


@pytest.fixture(autouse=True)
def clear_catalog_cache():
    catalog.clear_cached_catalog()
    yield
    catalog.clear_cached_catalog()


def _write_catalog(tmp_path, payload):
    path = tmp_path / "default-cards.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def test_search_prints_and_find_print_by_id_use_local_catalog(tmp_path):
    default_path = _write_catalog(
        tmp_path,
        [
            {
                "id": "bolt-print",
                "oracle_id": "bolt-oracle",
                "name": "Lightning Bolt",
                "set": "lea",
                "collector_number": "161",
            },
            {
                "id": "shock-print",
                "oracle_id": "shock-oracle",
                "name": "Shock",
                "set": "m19",
                "collector_number": "156",
            },
        ],
    )

    prints, total = catalog.search_prints(default_path, name_q="lightning", limit=10, offset=0)

    assert total == 1
    assert prints[0]["id"] == "bolt-print"
    assert catalog.find_print_by_id(default_path, "bolt-print")["name"] == "Lightning Bolt"


def test_search_tokens_and_tokens_from_oracle_resolve_and_dedupe(tmp_path):
    source_print = {
        "id": "source-print",
        "oracle_id": "source-oracle",
        "name": "Treasure Maker",
        "set": "tst",
        "collector_number": "1",
        "oracle_text": "Create a Treasure token.",
        "all_parts": [{"component": "token", "id": "treasure-token", "name": "Treasure"}],
    }
    alt_print = {
        "id": "source-print-2",
        "oracle_id": "source-oracle",
        "name": "Treasure Maker",
        "set": "tst",
        "collector_number": "2",
        "oracle_text": "Create a Treasure token.",
        "all_parts": [{"component": "token", "id": "treasure-token", "name": "Treasure"}],
    }
    default_path = _write_catalog(
        tmp_path,
        [
            source_print,
            alt_print,
            {
                "id": "treasure-token",
                "layout": "token",
                "name": "Treasure",
                "type_line": "Token Artifact — Treasure",
                "set": "ttk",
                "collector_number": "7",
                "lang": "en",
                "image_uris": {"small": "treasure-small", "normal": "treasure-normal"},
            },
        ],
    )

    matches = catalog.search_tokens(default_path, name_q="treasure", limit=10)
    tokens = catalog.tokens_from_oracle(default_path, [source_print, alt_print])

    assert matches[0]["name"] == "Treasure"
    assert matches[0]["images"]["small"] == "treasure-small"
    assert tokens == [
        {
            "id": "treasure-token",
            "name": "Treasure",
            "type_line": "Token Artifact — Treasure",
            "power": None,
            "toughness": None,
            "colors": [],
            "images": {"small": "treasure-small", "normal": "treasure-normal"},
        }
    ]


def test_tokens_from_oracle_collapses_same_token_across_printings(tmp_path):
    """Each printing references its own token print id; the identical token
    (same name/type/P+T) must collapse to a single entry, backfilling the image
    from whichever printing happened to carry one."""
    first_print = {
        "id": "krenko-a",
        "oracle_id": "krenko-oracle",
        "name": "Krenko",
        "set": "aaa",
        "collector_number": "1",
        "all_parts": [{"component": "token", "id": "goblin-a", "name": "Goblin"}],
    }
    second_print = {
        "id": "krenko-b",
        "oracle_id": "krenko-oracle",
        "name": "Krenko",
        "set": "bbb",
        "collector_number": "2",
        "all_parts": [{"component": "token", "id": "goblin-b", "name": "Goblin"}],
    }
    default_path = _write_catalog(
        tmp_path,
        [
            first_print,
            second_print,
            # Only the second printing's token print carries art.
            {
                "id": "goblin-a",
                "layout": "token",
                "name": "Goblin",
                "type_line": "Token Creature — Goblin",
                "power": "1",
                "toughness": "1",
                "colors": ["R"],
            },
            {
                "id": "goblin-b",
                "layout": "token",
                "name": "Goblin",
                "type_line": "Token Creature — Goblin",
                "power": "1",
                "toughness": "1",
                "colors": ["R"],
                "image_uris": {"small": "goblin-small", "normal": "goblin-normal"},
            },
        ],
    )

    tokens = catalog.tokens_from_oracle(default_path, [first_print, second_print])

    assert len(tokens) == 1
    assert tokens[0]["name"] == "Goblin"
    assert tokens[0]["power"] == "1"
    assert tokens[0]["toughness"] == "1"
    assert tokens[0]["colors"] == ["R"]
    assert tokens[0]["images"]["small"] == "goblin-small"


def test_tokens_from_oracle_keeps_distinct_tokens_separate(tmp_path):
    """Same name and stats but a different color (or P/T) is a different token
    and must not be collapsed."""
    source = {
        "id": "maker",
        "oracle_id": "maker-oracle",
        "name": "Spirit Maker",
        "all_parts": [
            {"component": "token", "id": "spirit-white", "name": "Spirit"},
            {"component": "token", "id": "spirit-black", "name": "Spirit"},
        ],
    }
    default_path = _write_catalog(
        tmp_path,
        [
            source,
            {
                "id": "spirit-white",
                "layout": "token",
                "name": "Spirit",
                "type_line": "Token Creature — Spirit",
                "power": "1",
                "toughness": "1",
                "colors": ["W"],
            },
            {
                "id": "spirit-black",
                "layout": "token",
                "name": "Spirit",
                "type_line": "Token Creature — Spirit",
                "power": "1",
                "toughness": "1",
                "colors": ["B"],
            },
        ],
    )

    tokens = catalog.tokens_from_oracle(default_path, [source])

    assert len(tokens) == 2
    assert {tuple(token["colors"]) for token in tokens} == {("W",), ("B",)}


def test_tokens_from_print_returns_generic_token_without_all_parts(tmp_path):
    default_path = _write_catalog(tmp_path, [])

    tokens = catalog.tokens_from_print(
        default_path,
        {
            "id": "fallback-print",
            "oracle_text": "Create a tapped token that's attacking.",
        },
    )

    assert tokens == [
        {
            "id": None,
            "name": "Token",
            "type_line": None,
            "power": None,
            "toughness": None,
            "colors": [],
            "images": {"small": None, "normal": None},
        }
    ]
