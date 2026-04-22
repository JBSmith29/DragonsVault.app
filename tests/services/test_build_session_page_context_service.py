from types import SimpleNamespace


def test_build_session_page_context_uses_legacy_helper_seams(monkeypatch):
    from core.domains.decks.services import build_session_page_context_service as context_service
    from core.domains.decks.services import build_session_service as legacy

    session = SimpleNamespace(
        id=7,
        commander_oracle_id="oid-commander",
        commander_name="Commander",
        build_name="Test Build",
        tags_json=["Tokens"],
        cards=[],
    )

    monkeypatch.setattr(legacy, "_normalized_tags", lambda tags: ["Tokens"])
    monkeypatch.setattr(legacy, "_oracle_payload", lambda oracle_id, fallback=None: {"oracle_id": oracle_id, "name": fallback})
    monkeypatch.setattr(legacy, "_session_cards", lambda entries: [{"oracle_id": "oid-card"}])
    monkeypatch.setattr(legacy, "_build_oracle_ids", lambda entries: {"oid-card"})
    monkeypatch.setattr(legacy, "_group_session_cards_by_type", lambda cards: [{"label": "Creatures", "cards": cards}])
    monkeypatch.setattr(
        legacy,
        "_deck_metrics",
        lambda entries: {
            "role_needs": {"Ramp"},
            "mana_pip_dist": [("G", "/g.svg", 2)],
            "land_mana_sources": [("G", "/g.svg", 4)],
            "phase": "exploration",
        },
    )
    monkeypatch.setattr(legacy, "_type_breakdown_for_entries", lambda entries: [("Creature", 3)])
    monkeypatch.setattr(legacy, "_distribution_breakdown_for_entries", lambda entries: [("Creature", 3)])
    monkeypatch.setattr(legacy, "_edhrec_type_breakdown", lambda commander_oracle_id, tags: [("Creature", 10)])
    monkeypatch.setattr(legacy, "_build_session_bracket_context", lambda session_obj, entries: {"label": "Focused"})
    monkeypatch.setattr(legacy, "get_deck_tag_groups", lambda: {"Themes": ["Tokens"]})
    monkeypatch.setattr(
        legacy,
        "build_recommendation_sections",
        lambda *args, **kwargs: [{"cards": [{"oracle_id": "oid-rec", "name": "Rec"}]}],
    )
    monkeypatch.setattr(legacy, "_collection_oracle_ids", lambda user_id: {"oid-rec"})
    monkeypatch.setattr(legacy, "_collection_name_keys", lambda user_id: {"rec"})
    monkeypatch.setattr(legacy, "_mark_collection_cards", lambda sections, owned_oracles, owned_name_keys=None: sections[0]["cards"][0].update({"in_collection": True}))
    monkeypatch.setattr(legacy, "_recommendation_oracle_ids", lambda sections: {"oid-rec"})
    monkeypatch.setattr(
        legacy,
        "_collection_recommendation_sections",
        lambda *args, **kwargs: [{"cards": [{"oracle_id": "oid-own"}]}],
    )
    monkeypatch.setattr(legacy, "_mark_build_cards", lambda sections, build_oracles: [card.update({"in_build": True}) for section in sections for card in (section.get("cards") or [])])
    monkeypatch.setattr(legacy, "_edhrec_estimate_seconds", lambda tags: 6)

    context = context_service.build_session_page_context(
        session,
        user_id=42,
        sort_mode="synergy",
        build_view="gallery",
        rec_source="bad-value",
        edhrec_job_id="job-1",
    )

    assert context["build_session"] is session
    assert context["rec_source"] == "edhrec"
    assert context["build_view"] == "gallery"
    assert context["edhrec_job_id"] == "job-1"
    assert context["phase"] == "exploration"
    assert context["recommendations"][0]["cards"][0]["in_collection"] is True
    assert context["recommendations"][0]["cards"][0]["in_build"] is True
    assert context["collection_sections"][0]["cards"][0]["in_build"] is True
    assert context["build_bracket"] == {"label": "Focused"}
    assert context["tag_groups"] == {"Themes": ["Tokens"]}


def test_build_session_drawer_summary_uses_legacy_helper_seams(monkeypatch):
    from core.domains.decks.services import build_session_page_context_service as context_service
    from core.domains.decks.services import build_session_service as legacy

    entries = [SimpleNamespace(card_oracle_id="oid-alpha", quantity=2)]
    session = SimpleNamespace(
        id=11,
        commander_oracle_id="oid-commander",
        commander_name="Commander",
        build_name="My Build",
        tags_json=["Blink"],
        cards=entries,
    )

    monkeypatch.setattr(legacy, "_normalized_tags", lambda tags: ["Blink"])
    monkeypatch.setattr(legacy, "get_deck_tag_category", lambda tag: "Theme")
    monkeypatch.setattr(legacy, "_commander_drawer_payload", lambda oracle_id, fallback_name: {"name": fallback_name, "image": "/c.png"})
    monkeypatch.setattr(
        legacy,
        "_deck_metrics",
        lambda rows: {
            "mana_pip_dist": [("U", "/u.svg", 3)],
            "land_mana_sources": [("W", "/w.svg", 2)],
            "missing_cmc": 1,
            "total_cards": 2,
        },
    )
    monkeypatch.setattr(legacy, "_type_breakdown_for_entries", lambda rows: [("Creature", 0), ("Instant", 2)])
    monkeypatch.setattr(legacy, "_curve_rows_for_entries", lambda rows: [{"label": "2", "count": 2, "pct": 100.0}])
    monkeypatch.setattr(legacy, "_color_identity_set", lambda oracle_id: {"U", "W"})
    monkeypatch.setattr(legacy, "_build_session_bracket_context", lambda session_obj, rows: {"score": 3})

    summary = context_service.build_session_drawer_summary(session)

    assert summary["deck"] == {
        "id": 11,
        "name": "My Build",
        "tag": "Blink",
        "tag_label": "Theme: Blink",
    }
    assert summary["commander"] == {"name": "Commander", "image": "/c.png"}
    assert summary["bracket"] == {"score": 3}
    assert summary["type_breakdown"] == [("Instant", 2)]
    assert summary["mana_pip_dist"] == [{"color": "U", "icon": "/u.svg", "count": 3}]
    assert summary["land_mana_sources"] == [{"color": "W", "icon": "/w.svg", "label": "W", "count": 2}]
    assert summary["curve_rows"] == [{"label": "2", "count": 2, "pct": 100.0}]
    assert summary["missing_cmc"] == 1
    assert summary["total_cards"] == 2
    assert summary["deck_colors"] == ["U", "W"]
