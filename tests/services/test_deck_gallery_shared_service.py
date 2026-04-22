from core.domains.decks.services import deck_gallery_shared_service


def test_owner_summary_aggregates_by_owner_key_and_proxy_count():
    decks = [
        {"owner": "Alice", "owner_key": "user:1", "owner_label": "Alice", "qty": 100, "is_proxy": False},
        {"owner": "Alice", "owner_key": "user:1", "owner_label": "Alice", "qty": 80, "is_proxy": True},
        {"owner": "Bob", "owner_key": "user:2", "owner_label": "Bob", "qty": 60, "is_proxy": False},
    ]

    summary = deck_gallery_shared_service.owner_summary(decks)

    assert summary == [
        {
            "key": "user:1",
            "owner": "Alice",
            "label": "Alice",
            "deck_count": 2,
            "card_total": 180,
            "proxy_count": 1,
        },
        {
            "key": "user:2",
            "owner": "Bob",
            "label": "Bob",
            "deck_count": 1,
            "card_total": 60,
            "proxy_count": 0,
        },
    ]


def test_owner_names_returns_sorted_distinct_values():
    decks = [
        {"owner": "Charlie"},
        {"owner": "Alice"},
        {"owner": "Charlie"},
        {"owner": ""},
    ]

    assert deck_gallery_shared_service.owner_names(decks) == ["Alice", "Charlie"]
