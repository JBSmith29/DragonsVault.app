from core.domains.decks.services import opening_hand_gameplay_service as gameplay_service


def test_mulligan_state_bottoms_selected_cards():
    state = {
        "deck": [{"uid": f"card-{idx}", "name": f"Card {idx}"} for idx in range(7)],
        "index": 7,
        "deck_name": "Deck",
        "user_id": 1,
    }

    result = gameplay_service.mulligan_state(
        state,
        raw_bottom_uids=["card-0"],
        count=1,
        hand_size=7,
    )

    assert result["bottomed"] == 1
    assert result["hand_size"] == 6
    assert [card["uid"] for card in result["hand_cards"]] == [
        "card-1",
        "card-2",
        "card-3",
        "card-4",
        "card-5",
        "card-6",
    ]
    assert result["state"]["deck"][-1]["uid"] == "card-0"


def test_search_state_lists_and_takes_matching_cards():
    state = {
        "deck": [
            {"uid": "land-1", "name": "Island", "type_line": "Basic Land - Island"},
            {"uid": "spell-1", "name": "Arcane Signet", "type_line": "Artifact"},
            {"uid": "land-2", "name": "Island", "type_line": "Basic Land - Island"},
        ],
        "index": 0,
        "deck_name": "Deck",
        "user_id": 1,
    }

    listed = gameplay_service.search_state(
        state,
        action="list",
        criteria={"kind": "basic_land"},
    )
    assert listed == {
        "matches": [
            {
                "name": "Island",
                "count": 2,
                "card": {"uid": "land-1", "name": "Island", "type_line": "Basic Land - Island"},
            }
        ],
        "remaining": 3,
    }

    taken = gameplay_service.search_state(
        state,
        action="take",
        criteria={"kind": "basic_land"},
        pick_uid="land-2",
    )
    assert taken["card"]["uid"] == "land-2"
    assert taken["remaining"] == 2
    # The tutored card is removed; remaining library order is now randomized, so
    # assert membership rather than a fixed order.
    assert {card["uid"] for card in taken["state"]["deck"]} == {"land-1", "spell-1"}


def test_search_state_take_shuffles_the_library(monkeypatch):
    """Tutoring a card must shuffle the remaining library (a regression guard
    against shuffling a throwaway slice copy)."""
    state = {
        "deck": [
            {"uid": "fetch", "name": "Forest", "type_line": "Basic Land - Forest"},
            *[{"uid": f"top-{idx}", "name": "Spell", "type_line": "Sorcery"} for idx in range(6)],
        ],
        "index": 0,
        "deck_name": "Deck",
        "user_id": 1,
    }
    # Deterministic "shuffle" that reverses, so the effect is observable.
    monkeypatch.setattr(gameplay_service.random, "shuffle", lambda lst: lst.reverse())

    taken = gameplay_service.search_state(
        state,
        action="take",
        criteria={"kind": "basic_land"},
        pick_uid="fetch",
    )

    # After removing the fetched land, the six remaining spells are reversed.
    assert [card["uid"] for card in taken["state"]["deck"]] == [
        "top-5",
        "top-4",
        "top-3",
        "top-2",
        "top-1",
        "top-0",
    ]


def test_reorder_state_moves_surveilled_cards_to_graveyard():
    state = {
        "deck": [
            {"uid": "card-1", "name": "One"},
            {"uid": "card-2", "name": "Two"},
            {"uid": "card-3", "name": "Three"},
        ],
        "index": 0,
        "deck_name": "Deck",
        "user_id": 1,
    }

    result = gameplay_service.reorder_state(
        state,
        action="surveil",
        count=2,
        keep_order=[1],
        bottom_order=[],
        graveyard_order=[0],
        choices=[],
    )

    assert [card["uid"] for card in result["state"]["deck"]] == ["card-2", "card-3"]
    assert [card["uid"] for card in result["graveyard_cards"]] == ["card-1"]
    assert result["bottom_cards"] == []
