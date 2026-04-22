from types import SimpleNamespace


def test_normalize_requested_tags_dedupes_and_uses_canonical_rows(monkeypatch):
    from core.domains.decks.services.edhrec import edhrec_tag_refresh_service as service

    monkeypatch.setattr(
        service.edhrec_target_service,
        "normalize_deck_tag",
        lambda value: {"blink": "Blink", "BLINK": "Blink", "tokens": "Tokens"}.get(value),
    )
    monkeypatch.setattr(
        service,
        "ensure_deck_tag",
        lambda name, source="user": SimpleNamespace(name=f"Stored {name}") if name == "Blink" else None,
    )

    assert service.normalize_requested_tags(["blink", "BLINK", "", "tokens"]) == [
        "Stored Blink",
        "Tokens",
    ]


def test_ingest_commander_tag_data_returns_cached_when_ready(monkeypatch):
    from core.domains.decks.services.edhrec import edhrec_tag_refresh_service as service

    monkeypatch.setattr(service.persistence_service, "ensure_schema", lambda: None)
    monkeypatch.setattr(service.sc, "ensure_cache_loaded", lambda: True)
    monkeypatch.setattr(service, "_USE_INDEX_SLUGS", False)
    monkeypatch.setattr(
        service.edhrec_target_service,
        "commander_target_from_oracle",
        lambda *args, **kwargs: SimpleNamespace(oracle_id="oid-cmdr", name="Atraxa, Praetors' Voice"),
    )
    monkeypatch.setattr(service, "normalize_requested_tags", lambda tags: ["Blink"])
    monkeypatch.setattr(service.persistence_service, "commander_tag_refresh_ready", lambda oid, tags: True)

    result = service.ingest_commander_tag_data(
        "oid-cmdr",
        "Atraxa, Praetors' Voice",
        ["Blink"],
        force_refresh=False,
    )

    assert result == {"status": "ok", "message": "EDHREC data already cached."}


def test_ingest_commander_tag_data_persists_refreshed_rows(monkeypatch):
    from core.domains.decks.services.edhrec import edhrec_tag_refresh_service as service

    target = SimpleNamespace(oracle_id="oid-cmdr", name="Atraxa, Praetors' Voice")
    persisted = {}
    observed = {}

    monkeypatch.setattr(service.persistence_service, "ensure_schema", lambda: None)
    monkeypatch.setattr(service.sc, "ensure_cache_loaded", lambda: True)
    monkeypatch.setattr(service.sc, "unique_oracle_by_name", lambda name: f"oracle::{name}")
    monkeypatch.setattr(service, "_USE_INDEX_SLUGS", False)
    monkeypatch.setattr(service, "_DEFAULT_SOURCE_VERSION", "snapshot-5")
    monkeypatch.setattr(
        service.edhrec_target_service,
        "commander_target_from_oracle",
        lambda *args, **kwargs: target,
    )
    monkeypatch.setattr(service, "normalize_requested_tags", lambda tags: ["Blink", "Tokens"])
    monkeypatch.setattr(service.persistence_service, "commander_tag_refresh_ready", lambda oid, tags: False)
    monkeypatch.setattr(service.edhrec_target_service, "slug_candidates_for_target", lambda item: ["atraxa"])
    monkeypatch.setattr(service.fetch_service, "build_edhrec_session", lambda: object())
    monkeypatch.setattr(
        service.fetch_service,
        "fetch_commander_bundle",
        lambda *args, **kwargs: {
            "fetch_error": None,
            "slug_used": "atraxa",
            "payload": {"ok": True},
            "raw_json": {"ok": True},
            "last_request_at": 2.5,
            "commander_rows": {
                "synergy_rows": [{"card_oracle_id": "oracle::Card A"}],
                "category_rows": [{"category": "Ramp", "card_oracle_id": "oracle::Card B"}],
                "tags": ["Blink", "Tokens", "Control"],
                "commander_type_rows": [{"card_type": "Creature", "count": 27}],
            },
        },
    )

    def fake_fetch_tag_rows(*args, **kwargs):
        observed["tag_names"] = list(kwargs["tag_names"])
        observed["slug_base"] = kwargs["slug_base"]
        observed["last_request_at"] = kwargs["last_request_at"]
        return {
            "tag_card_rows": {"Blink": [{"card_oracle_id": "oracle::Card C"}]},
            "tag_category_rows": {"Blink": [{"category": "Removal", "card_oracle_id": "oracle::Card D"}]},
            "tag_type_rows": {"Blink": [{"card_type": "Instant", "count": 6}]},
            "tag_cards_added": 1,
        }

    monkeypatch.setattr(service.fetch_service, "fetch_tag_rows", fake_fetch_tag_rows)

    def fake_persist(commander_oracle_id, **kwargs):
        persisted["commander_oracle_id"] = commander_oracle_id
        persisted.update(kwargs)

    monkeypatch.setattr(service.persistence_service, "persist_commander_tag_refresh", fake_persist)
    monkeypatch.setattr(service.persistence_service, "now_iso", lambda: "2026-04-13T00:00:00+00:00")

    result = service.ingest_commander_tag_data("oid-cmdr", target.name, ["Blink", "Tokens"])

    assert result == {
        "status": "ok",
        "message": "EDHREC data refreshed for Atraxa, Praetors' Voice.",
        "cards_inserted": 1,
        "tags_inserted": 3,
        "tag_cards_inserted": 1,
    }
    assert observed == {
        "tag_names": ["Blink", "Tokens"],
        "slug_base": "atraxa",
        "last_request_at": 2.5,
    }
    assert persisted["commander_oracle_id"] == "oid-cmdr"
    assert persisted["default_source_version"] == "snapshot-5"
    assert persisted["tags"] == ["Blink", "Tokens", "Control"]
