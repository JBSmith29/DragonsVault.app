from __future__ import annotations

from services import spellbook_sync


def test_generate_spellbook_combo_dataset_categorises_variants(monkeypatch):
    sample = {
        "early": spellbook_sync.SpellbookVariantRecord(
            variant={
                "id": "early",
                "uses": [
                    {"card": {"name": "Sol Ring"}, "quantity": 1},
                    {"card": {"name": "Basalt Monolith"}, "quantity": 1},
                ],
                "manaValueNeeded": 3,
                "produces": [{"feature": {"name": "Infinite mana"}}],
                "manaNeeded": "{3}",
                "bracketTag": "combo",
                "identity": "c",
                "easyPrerequisites": "",
                "notablePrerequisites": "",
                "description": "Makes a lot of mana",
            },
            results={"Infinite mana"},
            categories={"infinite_mana"},
        ),
        "late": spellbook_sync.SpellbookVariantRecord(
            variant={
                "id": "late",
                "uses": [
                    {"card": {"name": "Dark Ritual"}, "quantity": 1},
                    {"card": {"name": "Exsanguinate"}, "quantity": 1},
                    {"card": {"name": "Cabal Coffers"}, "quantity": 1},
                ],
                "manaValueNeeded": 12,
                "produces": [{"feature": {"name": "Each opponent loses the game"}}],
                "manaNeeded": "{10}{B}{B}",
                "bracketTag": "finisher",
                "identity": "b",
                "easyPrerequisites": "",
                "notablePrerequisites": "",
                "description": "Drain the table",
            },
            results={"Each opponent loses the game"},
            categories={"instant_win"},
        ),
    }

    monkeypatch.setattr(
        spellbook_sync,
        "collect_relevant_spellbook_variants",
        lambda: sample,
    )

    dataset = spellbook_sync.generate_spellbook_combo_dataset(card_count_targets=(2, 3))

    assert dataset["counts"]["total_variants"] == 2
    assert dataset["counts"]["early_game"] == 1
    assert dataset["counts"]["late_game"] == 1
    assert dataset["counts"]["category_infinite_mana"] == 1
    assert dataset["counts"]["category_instant_win"] == 1
    assert dataset["early_game"][0]["category"] == "early"
    assert dataset["late_game"][0]["category"] == "late"
    assert dataset["early_game"][0]["cards"][0]["name"] == "Basalt Monolith"
