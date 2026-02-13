from models import Card, Folder, FolderRole, FolderShare, db


def _login(client, identifier, password):
    return client.post(
        "/login",
        data={"identifier": identifier, "password": password},
        follow_redirects=True,
    )


def _create_deck(app, owner, *, name="Test Deck", shared_user=None):
    with app.app_context():
        deck = Folder(
            name=name,
            category=Folder.CATEGORY_DECK,
            owner_user_id=owner.id,
        )
        db.session.add(deck)
        db.session.flush()
        db.session.add(FolderRole(folder_id=deck.id, role=FolderRole.ROLE_DECK))

        for idx in range(7):
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
    deck_id = _create_deck(app, owner, name="Owner Only Deck")

    _login(client, viewer.email, viewer_password)
    resp = client.get("/opening-hand")
    assert resp.status_code == 200
    assert b"Owner Only Deck" not in resp.data

    with app.app_context():
        db.session.add(FolderShare(folder_id=deck_id, shared_user_id=viewer.id))
        db.session.commit()

    resp2 = client.get("/opening-hand")
    assert resp2.status_code == 200
    assert b"Owner Only Deck" in resp2.data


def test_opening_hand_shuffle_blocks_unshared_deck(client, create_user, app):
    owner, _ = create_user(email="owner_shuffle@example.com", username="owner_shuffle")
    viewer, viewer_password = create_user(email="viewer_shuffle@example.com", username="viewer_shuffle")
    deck_id = _create_deck(app, owner, name="Private Deck")

    _login(client, viewer.email, viewer_password)
    resp = client.post("/opening-hand/shuffle", json={"deck_id": str(deck_id)})
    assert resp.status_code == 403


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

    tampered = state_token[:-1] + ("A" if state_token[-1] != "A" else "B")
    bad_resp = client.post("/opening-hand/draw", json={"state": tampered})
    assert bad_resp.status_code == 400
    bad_payload = bad_resp.get_json() or {}
    assert bad_payload.get("ok") is False

    other_client = app.test_client()
    _login(other_client, viewer.email, viewer_password)
    cross_resp = other_client.post("/opening-hand/draw", json={"state": state_token})
    assert cross_resp.status_code == 400
    cross_payload = cross_resp.get_json() or {}
    assert cross_payload.get("ok") is False


def test_opening_hand_hideaway(client, create_user, app):
    user, password = create_user(email="hideaway@example.com", username="hideaway")
    deck_id = _create_deck(app, user, name="Hideaway Deck")

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
