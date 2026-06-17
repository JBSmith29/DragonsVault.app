from models import Card, Folder, FolderRole, FolderShare, db


def _login(client, identifier, password):
    return client.post(
        "/login",
        data={"identifier": identifier, "password": password},
        follow_redirects=True,
    )


def _create_deck(app, owner, *, name="Test Deck", shared_user=None, card_count=7):
    with app.app_context():
        deck = Folder(
            name=name,
            category=Folder.CATEGORY_DECK,
            owner_user_id=owner.id,
        )
        db.session.add(deck)
        db.session.flush()
        db.session.add(FolderRole(folder_id=deck.id, role=FolderRole.ROLE_DECK))

        for idx in range(card_count):
            card = Card(
                name=f"Card {idx + 1}",
                set_code="TST",
                collector_number=str(idx + 1),
                folder_id=deck.id,
                quantity=1,
                lang="en",
            )
            db.session.add(card)

        if shared_user is not None:
            db.session.add(FolderShare(folder_id=deck.id, shared_user_id=shared_user.id))

        db.session.commit()
        return deck.id


def test_opening_hand_deck_options_scoped(client, create_user, app):
    owner, _ = create_user(email="owner_opening@example.com", username="owner_opening")
    viewer, viewer_password = create_user(email="viewer_opening@example.com", username="viewer_opening")
    _create_deck(app, owner, name="Owner Only Deck")
    _create_deck(app, owner, name="Shared Deck", shared_user=viewer)

    _login(client, viewer.email, viewer_password)
    resp = client.get("/opening-hand")
    assert resp.status_code == 200
    assert b"Owner Only Deck" not in resp.data
    assert b"Shared Deck" in resp.data


def test_opening_hand_shuffle_blocks_unshared_deck(client, create_user, app):
    owner, _ = create_user(email="owner_shuffle@example.com", username="owner_shuffle")
    viewer, viewer_password = create_user(email="viewer_shuffle@example.com", username="viewer_shuffle")
    deck_id = _create_deck(app, owner, name="Private Deck")

    _login(client, viewer.email, viewer_password)
    resp = client.post("/opening-hand/shuffle", json={"deck_id": str(deck_id)})
    assert resp.status_code in (403, 404)


def test_opening_hand_state_tamper_and_cross_user(client, create_user, app):
    owner, owner_password = create_user(email="owner_state@example.com", username="owner_state")
    viewer, viewer_password = create_user(email="viewer_state@example.com", username="viewer_state")
    deck_id = _create_deck(app, owner, name="State Deck")

    _login(client, owner.email, owner_password)
    resp = client.post("/opening-hand/shuffle", json={"deck_id": str(deck_id)})
    assert resp.status_code == 200
    payload = resp.get_json() or {}
    state_token = payload.get("state")
    assert state_token

    # Corrupt the first character of the signature segment (after the final
    # ".") so the HMAC never matches. Flipping the *last* token character was
    # flaky: base64url's trailing "don't-care" bits sometimes decode unchanged.
    head, _, signature = state_token.rpartition(".")
    tampered_sig = ("A" if signature[0] != "A" else "B") + signature[1:]
    tampered = f"{head}.{tampered_sig}"
    bad_resp = client.post("/opening-hand/draw", json={"state": tampered})
    assert bad_resp.status_code == 400
    bad_payload = bad_resp.get_json() or {}
    assert bad_payload.get("ok") is False

    client.get("/logout", follow_redirects=True)
    _login(client, viewer.email, viewer_password)
    cross_resp = client.post("/opening-hand/draw", json={"state": state_token})
    assert cross_resp.status_code == 400
    cross_payload = cross_resp.get_json() or {}
    assert cross_payload.get("ok") is False


def test_opening_hand_requires_login(client):
    resp = client.get("/opening-hand")
    assert resp.status_code in (301, 302, 401)
    if resp.status_code in (301, 302):
        location = resp.headers.get("Location") or ""
        assert "/login" in location


def test_opening_hand_hideaway(client, create_user, app):
    user, password = create_user(email="hideaway@example.com", username="hideaway")
    deck_id = _create_deck(app, user, name="Hideaway Deck", card_count=10)

    _login(client, user.email, password)
    resp = client.post("/opening-hand/shuffle", json={"deck_id": str(deck_id)})
    assert resp.status_code == 200
    payload = resp.get_json() or {}
    state_token = payload.get("state")
    remaining = payload.get("remaining")
    assert state_token
    assert remaining is not None

    peek = client.post("/opening-hand/peek", json={"state": state_token, "count": 3})
    assert peek.status_code == 200
    peek_payload = peek.get_json() or {}
    assert peek_payload.get("ok") is True
    cards = peek_payload.get("cards") or []
    assert len(cards) > 0
    pick_uid = cards[0].get("uid")
    assert pick_uid

    hideaway = client.post(
        "/opening-hand/hideaway",
        json={"state": state_token, "count": 3, "pick_uid": pick_uid},
    )
    assert hideaway.status_code == 200
    hideaway_payload = hideaway.get_json() or {}
    assert hideaway_payload.get("ok") is True
    assert hideaway_payload.get("hidden")
    assert hideaway_payload.get("remaining") == remaining - 1


def test_opening_hand_shuffle_uses_resolved_type_line_for_zone_hints(client, create_user, app, monkeypatch):
    from core.domains.decks.services import opening_hand_deck_source_service

    user, password = create_user(email="zones@example.com", username="zones")
    deck_id = _create_deck(app, user, name="Zone Deck")

    def _fake_lookup_print_data(set_code, collector_number, name, oracle_id):
        cn = str(collector_number or "")
        if cn == "1":
            return {
                "type_line": "Basic Land - Island",
                "oracle_text": "{T}: Add {U}.",
            }
        if cn == "2":
            return {
                "type_line": "Creature - Human Wizard",
                "oracle_text": "",
            }
        return {
            "type_line": "Artifact",
            "oracle_text": "",
        }

    monkeypatch.setattr(opening_hand_deck_source_service, "_lookup_print_data", _fake_lookup_print_data)

    _login(client, user.email, password)
    resp = client.post("/opening-hand/shuffle", json={"deck_id": str(deck_id)})
    assert resp.status_code == 200
    payload = resp.get_json() or {}
    assert payload.get("ok") is True

    hand = payload.get("hand") or []
    by_name = {card.get("name"): card for card in hand}

    island_card = by_name.get("Card 1")
    creature_card = by_name.get("Card 2")
    assert island_card is not None
    assert creature_card is not None
    assert island_card.get("zone_hint") == "lands"
    assert island_card.get("is_land") is True
    assert creature_card.get("zone_hint") == "creatures"
    assert creature_card.get("is_creature") is True


def test_opening_hand_mulligan_bottoms_selected_cards(client, create_user, app):
    user, password = create_user(email="mulligan@example.com", username="mulligan")
    deck_id = _create_deck(app, user, name="Mulligan Deck")

    _login(client, user.email, password)
    shuffle_resp = client.post("/opening-hand/shuffle", json={"deck_id": str(deck_id)})
    assert shuffle_resp.status_code == 200
    shuffle_payload = shuffle_resp.get_json() or {}
    state_token = shuffle_payload.get("state")
    opening_hand = shuffle_payload.get("hand") or []
    remaining_before = int(shuffle_payload.get("remaining") or 0)
    assert state_token
    assert len(opening_hand) == 7

    bottom_uid = opening_hand[0].get("uid")
    assert bottom_uid

    mulligan_resp = client.post(
        "/opening-hand/mulligan",
        json={"state": state_token, "count": 1, "bottom_uids": [bottom_uid]},
    )
    assert mulligan_resp.status_code == 200
    mulligan_payload = mulligan_resp.get_json() or {}
    assert mulligan_payload.get("ok") is True
    assert mulligan_payload.get("bottomed") == 1
    assert mulligan_payload.get("hand_size") == 6
    assert int(mulligan_payload.get("remaining") or 0) == remaining_before + 1
    kept_uids = {card.get("uid") for card in (mulligan_payload.get("hand") or [])}
    assert bottom_uid not in kept_uids


def test_opening_hand_mulligan_rejects_invalid_selection(client, create_user, app):
    user, password = create_user(email="mulligan_bad@example.com", username="mulligan_bad")
    deck_id = _create_deck(app, user, name="Mulligan Invalid Deck")

    _login(client, user.email, password)
    shuffle_resp = client.post("/opening-hand/shuffle", json={"deck_id": str(deck_id)})
    assert shuffle_resp.status_code == 200
    payload = shuffle_resp.get_json() or {}
    state_token = payload.get("state")
    assert state_token

    bad_resp = client.post(
        "/opening-hand/mulligan",
        json={"state": state_token, "count": 2, "bottom_uids": ["missing-uid"]},
    )
    assert bad_resp.status_code == 400
    bad_payload = bad_resp.get_json() or {}
    assert bad_payload.get("ok") is False


def test_opening_hand_play_page_includes_automation_ui(client, create_user, app):
    user, password = create_user(email="automation@example.com", username="automation")
    deck_id = _create_deck(app, user, name="Automation Deck")

    _login(client, user.email, password)
    resp = client.post(
        "/opening-hand/play",
        data={"deck_id": str(deck_id)},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # Auto-tap assistance + mana HUD are wired in, but there is no full-turn
    # "Auto Play" — the automation assists decisions, it never plays for you.
    assert 'id="autoTapToggle"' in body
    assert 'id="manaHud"' in body
    assert "opening-hand-automation.js" in body
    assert 'id="autoPlayBtn"' not in body

    # Status HUD (deck name, turn, hand, cards remaining) sits in a panel above
    # the command zone, ahead of the board command section in the markup.
    assert 'id="boardStatusPanel"' in body
    panel_idx = body.index('id="boardStatusPanel"')
    command_idx = body.index('data-board-zone="command"')
    assert panel_idx < command_idx
    for moved_id in ("handDeckName", "turnCounter", "handSizeBadge", "handRemaining"):
        assert body.index(f'id="{moved_id}"') < command_idx

    # The life tracker host is centred in the action bar, between the
    # "Select New Deck" button and the "Scry / Surveil" button.
    assert 'id="actionLifeHost"' in body
    assert body.index('id="newDeckBtn"') < body.index('id="actionLifeHost"') < body.index('id="scryBtn"')


def test_opening_hand_token_search_sets_zone_hints(client, create_user, monkeypatch):
    from core.domains.decks.services import opening_hand_service

    user, password = create_user(email="token_search@example.com", username="token_search")
    _login(client, user.email, password)

    monkeypatch.setattr(opening_hand_service, "_ensure_cache_ready", lambda: True)

    def _fake_search_tokens(query, limit=36):
        return [
            {
                "id": "token-creature",
                "name": "Soldier",
                "type_line": "Token Creature - Soldier",
                "images": {"normal": "https://example.com/soldier.jpg"},
            },
            {
                "id": "token-land",
                "name": "Treasure Cove",
                "type_line": "Token Land",
                "images": {"normal": "https://example.com/land.jpg"},
            },
        ]

    monkeypatch.setattr(opening_hand_service.sc, "search_tokens", _fake_search_tokens)

    resp = client.get("/opening-hand/tokens/search?q=soldier")
    assert resp.status_code == 200
    payload = resp.get_json() or {}
    assert payload.get("ok") is True
    tokens = payload.get("tokens") or []
    by_id = {token.get("id"): token for token in tokens}
    assert by_id["token-creature"]["zone_hint"] == "creatures"
    assert by_id["token-land"]["zone_hint"] == "lands"
