def test_extract_cardviews_prefers_synergy_lists_and_normalizes_inclusion():
    from core.domains.decks.services.edhrec import edhrec_payload_service

    payload = {
        "cardlists": [
            {
                "header": "Top Cards",
                "cardviews": [{"name": "Card A", "rank": 7, "synergy": "0.12", "num_decks": 15, "potential_decks": 60}],
            },
            {
                "header": "High Synergy Cards",
                "cardviews": [{"name": "Card B", "rank": 1, "synergy": "0.44", "inclusion": 0.2}],
            },
        ]
    }

    assert edhrec_payload_service.extract_cardviews(payload) == [
        {"name": "Card B", "rank": 1, "synergy": 0.44, "inclusion": 20.0}
    ]


def test_map_synergy_cards_dedupes_by_oracle_and_ranks_highest_score():
    from core.domains.decks.services.edhrec import edhrec_payload_service

    views = [
        {"name": "Card A", "rank": 5, "synergy": 0.2, "inclusion": 10.0},
        {"name": "Card A Alt", "rank": 4, "synergy": 0.3, "inclusion": 12.0},
        {"name": "Card B", "rank": 1, "synergy": 0.1, "inclusion": 30.0},
    ]

    lookup = {
        "Card A": "oid-a",
        "Card A Alt": "oid-a",
        "Card B": "oid-b",
    }

    rows = edhrec_payload_service.map_synergy_cards(
        views,
        lookup_oracle_id_fn=lambda name: lookup.get(name),
        max_synergy_cards=None,
    )

    assert rows == [
        {"card_oracle_id": "oid-a", "synergy_rank": 1, "synergy_score": 0.3, "inclusion_percent": 12.0},
        {"card_oracle_id": "oid-b", "synergy_rank": 2, "synergy_score": 0.1, "inclusion_percent": 30.0},
    ]


def test_extract_type_distribution_uses_fallback_piechart_and_numeric_fields():
    from core.domains.decks.services.edhrec import edhrec_payload_service

    piechart_payload = {
        "panels": {"piechart": {"content": [{"label": "Creatures", "value": 18}, {"label": "Artifacts", "value": 6}]}}
    }
    count_payload = {"creature": 12, "artifact": 4, "battle": 1}

    assert edhrec_payload_service.extract_type_distribution(piechart_payload) == [
        {"card_type": "Creature", "count": 18},
        {"card_type": "Artifact", "count": 6},
    ]
    assert edhrec_payload_service.extract_type_distribution_from_sources({}, count_payload) == [
        {"card_type": "Creature", "count": 12},
        {"card_type": "Artifact", "count": 4},
        {"card_type": "Battle", "count": 1},
    ]


def test_normalize_tag_candidates_walks_nested_next_data(monkeypatch):
    from core.domains.decks.services.edhrec import edhrec_payload_service

    raw = {
        "props": {
            "pageProps": {
                "data": {
                    "tags": [
                        {"slug": "blink", "label": "Blink"},
                        {"slug": "sacrifice", "label": "Sacrifice"},
                        {"slug": "blink", "label": "Blink"},
                    ]
                }
            }
        }
    }

    monkeypatch.setattr(
        edhrec_payload_service,
        "resolve_deck_tag_from_slug",
        lambda value: {"blink": "Blink", "sacrifice": "Sacrifice"}.get(str(value).strip().lower()),
    )
    monkeypatch.setattr(edhrec_payload_service, "normalize_tag_label", lambda value: str(value).strip().title())

    assert edhrec_payload_service.normalize_tag_candidates(raw) == ["Blink", "Sacrifice"]
