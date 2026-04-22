def test_build_commander_rows_maps_payload_sections(monkeypatch):
    from core.domains.decks.services.edhrec import edhrec_ingestion_fetch_service as fetch_service

    monkeypatch.setattr(fetch_service.edhrec_payload_service, "extract_cardviews", lambda payload: [{"name": "Card A"}])
    monkeypatch.setattr(
        fetch_service.edhrec_payload_service,
        "map_synergy_cards",
        lambda views, lookup_oracle_id_fn, max_synergy_cards: [
            {"card_oracle_id": lookup_oracle_id_fn(views[0]["name"]), "synergy_rank": 1}
        ],
    )
    monkeypatch.setattr(fetch_service.edhrec_payload_service, "extract_cardlists", lambda payload: [{"header": "Ramp"}])
    monkeypatch.setattr(
        fetch_service.edhrec_payload_service,
        "map_category_cards",
        lambda cardlists, lookup_oracle_id_fn, max_synergy_cards: [
            {"category": cardlists[0]["header"], "card_oracle_id": lookup_oracle_id_fn("Card B")}
        ],
    )
    monkeypatch.setattr(fetch_service.edhrec_payload_service, "normalize_tag_candidates", lambda raw: ["Blink", "Artifacts"])
    monkeypatch.setattr(fetch_service.edhrec_payload_service, "upsert_edhrec_tags", lambda tags: [tag.upper() for tag in tags])
    monkeypatch.setattr(
        fetch_service.edhrec_payload_service,
        "extract_type_distribution_from_sources",
        lambda payload, raw: [{"card_type": "Creature", "count": 27}],
    )

    rows = fetch_service.build_commander_rows(
        {"payload": True},
        {"raw": True},
        lookup_oracle_id_fn=lambda name: {"Card A": "oid-a", "Card B": "oid-b"}[name],
        max_synergy_cards=None,
    )

    assert rows == {
        "synergy_rows": [{"card_oracle_id": "oid-a", "synergy_rank": 1}],
        "category_rows": [{"category": "Ramp", "card_oracle_id": "oid-b"}],
        "tags": ["BLINK", "ARTIFACTS"],
        "commander_type_rows": [{"card_type": "Creature", "count": 27}],
    }


def test_fetch_tag_rows_collects_successful_tags_and_skips_not_found(monkeypatch):
    from core.domains.decks.services.edhrec import edhrec_ingestion_fetch_service as fetch_service

    def fake_fetch(_session, url):
        if url.endswith("/blink"):
            return {"tag": "Blink"}, {"raw": "blink"}, None
        return None, None, "Commander page not found."

    monkeypatch.setattr(fetch_service.edhrec_payload_service, "extract_cardviews", lambda payload: [{"name": payload["tag"]}])
    monkeypatch.setattr(
        fetch_service.edhrec_payload_service,
        "map_synergy_cards",
        lambda views, lookup_oracle_id_fn, max_synergy_cards: [
            {"card_oracle_id": lookup_oracle_id_fn(views[0]["name"]), "synergy_rank": 1}
        ],
    )
    monkeypatch.setattr(fetch_service.edhrec_payload_service, "extract_cardlists", lambda payload: [{"header": payload["tag"]}])
    monkeypatch.setattr(
        fetch_service.edhrec_payload_service,
        "map_category_cards",
        lambda cardlists, lookup_oracle_id_fn, max_synergy_cards: [
            {"category": cardlists[0]["header"], "card_oracle_id": "oid-category"}
        ],
    )
    monkeypatch.setattr(
        fetch_service.edhrec_payload_service,
        "extract_type_distribution_from_sources",
        lambda payload, raw: [{"card_type": "Artifact", "count": 4}],
    )

    rows = fetch_service.fetch_tag_rows(
        session=None,
        target_name="Atraxa, Praetors' Voice",
        tag_names=["Blink", "Missing"],
        slug_base="atraxa-praetors-voice",
        last_request_at=0.0,
        interval_seconds=0.0,
        lookup_oracle_id_fn=lambda name: {"Blink": "oid-blink"}[name],
        max_synergy_cards=None,
        fetch_commander_json_fn=fake_fetch,
    )

    assert rows["tag_cards_added"] == 1
    assert rows["tag_card_rows"] == {"Blink": [{"card_oracle_id": "oid-blink", "synergy_rank": 1}]}
    assert rows["tag_category_rows"] == {"Blink": [{"category": "Blink", "card_oracle_id": "oid-category"}]}
    assert rows["tag_type_rows"] == {"Blink": [{"card_type": "Artifact", "count": 4}]}


def test_fetch_primary_commander_payload_records_missing_slugs():
    from core.domains.decks.services.edhrec import edhrec_ingestion_fetch_service as fetch_service

    missing_slugs = {}

    def fake_fetch(_session, url):
        if url.endswith("/old-slug"):
            return None, None, "Commander page not found."
        return {"payload": True}, {"raw": True}, None

    payload = fetch_service.fetch_primary_commander_payload(
        None,
        target_name="Atraxa, Praetors' Voice",
        target_oracle_id="oid-atraxa",
        candidates_to_try=["old-slug", "atraxa-praetors-voice"],
        last_request_at=0.0,
        interval_seconds=0.0,
        missing_slugs=missing_slugs,
        now_iso_fn=lambda: "2026-04-12T00:00:00+00:00",
        fetch_commander_json_fn=fake_fetch,
    )

    assert payload["slug_used"] == "atraxa-praetors-voice"
    assert payload["payload"] == {"payload": True}
    assert missing_slugs == {
        "old-slug": {
            "name": "Atraxa, Praetors' Voice",
            "oracle_id": "oid-atraxa",
            "last_seen": "2026-04-12T00:00:00+00:00",
        }
    }


def test_fetch_commander_bundle_builds_rows_from_primary_payload(monkeypatch):
    from core.domains.decks.services.edhrec import edhrec_ingestion_fetch_service as fetch_service

    monkeypatch.setattr(
        fetch_service,
        "fetch_primary_commander_payload",
        lambda *args, **kwargs: {
            "payload": {"payload": True},
            "raw_json": {"raw": True},
            "slug_used": "atraxa",
            "fetch_error": None,
            "last_request_at": 1.5,
        },
    )
    monkeypatch.setattr(
        fetch_service,
        "build_commander_rows",
        lambda payload, raw_json, lookup_oracle_id_fn, max_synergy_cards: {
            "synergy_rows": [{"card_oracle_id": lookup_oracle_id_fn("Card A")}],
            "category_rows": [],
            "tags": ["Blink"],
            "commander_type_rows": [],
        },
    )

    bundle = fetch_service.fetch_commander_bundle(
        None,
        target_name="Atraxa, Praetors' Voice",
        target_oracle_id="oid-atraxa",
        candidates_to_try=["atraxa"],
        last_request_at=0.0,
        interval_seconds=0.0,
        lookup_oracle_id_fn=lambda name: {"Card A": "oid-card-a"}[name],
        max_synergy_cards=None,
    )

    assert bundle["slug_used"] == "atraxa"
    assert bundle["commander_rows"] == {
        "synergy_rows": [{"card_oracle_id": "oid-card-a"}],
        "category_rows": [],
        "tags": ["Blink"],
        "commander_type_rows": [],
    }
