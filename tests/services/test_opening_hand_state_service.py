from core.domains.decks.services import opening_hand_state_service as state_service


def test_opening_hand_state_roundtrip_requires_matching_user():
    token = state_service.encode_state(
        {
            "deck": [{"name": "Card A", "uid": "a-0"}],
            "index": 1,
            "deck_name": "Deck",
            "user_id": 7,
        },
        secret_key="secret",
    )

    decoded = state_service.decode_state(
        token,
        secret_key="secret",
        current_user_id=7,
    )
    assert decoded == {
        "deck": [{"name": "Card A", "uid": "a-0"}],
        "index": 1,
        "deck_name": "Deck",
        "user_id": 7,
    }
    assert state_service.decode_state(token, secret_key="secret", current_user_id=8) is None
    assert state_service.decode_state(token + "tamper", secret_key="secret", current_user_id=7) is None


def test_expanded_deck_entries_assigns_stable_uids():
    expanded = state_service.expanded_deck_entries(
        [
            {"name": "Arcane Signet", "oracle_id": "oid-1", "qty": 2},
            {"name": "Island", "card_id": 9, "qty": 1},
        ]
    )

    assert [entry["uid"] for entry in expanded] == ["oid-1-0", "oid-1-1", "9-2"]
    assert [entry["name"] for entry in expanded] == ["Arcane Signet", "Arcane Signet", "Island"]
