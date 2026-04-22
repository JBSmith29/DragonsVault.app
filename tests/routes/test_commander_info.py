from types import SimpleNamespace


def _login(client, identifier, password):
    return client.post(
        "/login",
        data={"identifier": identifier, "password": password},
        follow_redirects=True,
    )


def test_commander_brackets_page_uses_commander_info_service(client, create_user, monkeypatch):
    from core.domains.decks.services import commander_info_service

    user, password = create_user(email="brackets@example.com", username="brackets_user")

    monkeypatch.setattr(commander_info_service, "cache_ready", lambda: True)
    monkeypatch.setattr(commander_info_service, "cache_epoch", lambda: 7)
    monkeypatch.setattr(commander_info_service, "GAME_CHANGERS", ["Alpha Growth"])
    monkeypatch.setattr(commander_info_service, "_MASS_LAND_FEATURED", ["Land Lock"])
    monkeypatch.setattr(commander_info_service, "_EXTRA_TURN_CHAINERS", ["Time Chain"])
    monkeypatch.setattr(
        commander_info_service,
        "commander_card_snapshot",
        lambda name, epoch: {
            "name": f"{name} ({epoch})",
            "oracle_id": f"oid-{name.lower().replace(' ', '-')}",
            "scryfall_id": None,
            "scryfall_uri": None,
            "set": "TST",
            "set_name": "Test Set",
            "collector_number": "1",
            "hover": None,
            "thumb": None,
        },
    )

    _login(client, user.email, password)
    response = client.get("/commander-brackets")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Commander Bracket Reference" in body
    assert "Alpha Growth (7)" in body
    assert "Land Lock (7)" in body
    assert "Time Chain (7)" in body


def test_commander_spellbook_combos_page_uses_commander_info_service(client, create_user, monkeypatch):
    from core.domains.decks.services import commander_info_service

    user, password = create_user(email="spellbook@example.com", username="spellbook_user")

    early_combo = SimpleNamespace(
        id="combo-early",
        cards=["Sol Ring", "Basalt Monolith"],
        requirements={"sol ring": 2},
        result_categories=["infinite_mana"],
        mana_needed="{2}",
        identity="C",
        results=["Infinite colorless mana"],
        url="https://example.test/early",
        mana_value_needed=2,
        normalized_mana_value=2,
    )
    late_combo = SimpleNamespace(
        id="combo-late",
        cards=["Time Warp", "Archaeomancer"],
        requirements={},
        result_categories=["extra_turns"],
        mana_needed="{5}{U}",
        identity="U",
        results=["Infinite turns"],
        url="https://example.test/late",
        mana_value_needed=6,
        normalized_mana_value=6,
    )

    monkeypatch.setattr(commander_info_service, "SPELLBOOK_EARLY_COMBOS", [early_combo])
    monkeypatch.setattr(commander_info_service, "SPELLBOOK_LATE_COMBOS", [late_combo])
    monkeypatch.setattr(commander_info_service, "render_mana_html", lambda cost, use_local=True: cost)

    _login(client, user.email, password)
    response = client.get("/commander-spellbook-combos")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Commander Spellbook Combos" in body
    assert "Infinite colorless mana" in body
    assert "Infinite turns" in body
    assert "Sol Ring" in body
    assert "Archaeomancer" in body
