from models import Card, Folder, FolderRole, db


def _login(client, identifier, password):
    return client.post(
        "/login",
        data={"identifier": identifier, "password": password},
        follow_redirects=True,
    )


def _create_deck(app, *, owner_user_id: int, name: str, cards: list[dict] | None = None) -> tuple[int, int | None]:
    rows = cards or []
    with app.app_context():
        deck = Folder(
            name=name,
            category=Folder.CATEGORY_DECK,
            owner_user_id=owner_user_id,
        )
        db.session.add(deck)
        db.session.flush()
        db.session.add(FolderRole(folder_id=deck.id, role=FolderRole.ROLE_DECK))
        card_id = None
        for row in rows:
            card = Card(
                name=row["name"],
                set_code=row.get("set_code", "TST"),
                collector_number=row.get("collector_number", "1"),
                folder_id=deck.id,
                quantity=int(row.get("quantity", 1)),
                oracle_id=row.get("oracle_id"),
                lang="en",
            )
            db.session.add(card)
            db.session.flush()
            if card_id is None:
                card_id = card.id
        db.session.commit()
        return deck.id, card_id


def test_api_folder_commander_candidates_returns_payload(client, create_user, app, monkeypatch):
    from core.domains.decks.services import commander_assignment_service

    user, password = create_user(email="commander-candidates@example.com", username="commander_candidates")
    deck_id, _ = _create_deck(app, owner_user_id=user.id, name="Candidate Deck")

    monkeypatch.setattr(
        commander_assignment_service,
        "_commander_candidates_for_folder",
        lambda folder_id: [{"name": "Atraxa", "oracle_id": "oid-atraxa"}] if folder_id == deck_id else [],
    )

    _login(client, user.email, password)
    response = client.get(f"/api/folders/{deck_id}/commander-candidates")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["folder"]["id"] == deck_id
    assert payload["candidates"] == [{"name": "Atraxa", "oracle_id": "oid-atraxa"}]


def test_set_folder_commander_updates_folder(client, create_user, app):
    user, password = create_user(email="commander-form@example.com", username="commander_form")
    deck_id, _ = _create_deck(app, owner_user_id=user.id, name="Form Deck")

    _login(client, user.email, password)
    response = client.post(
        f"/folders/{deck_id}/set_commander",
        data={"name": "Atraxa", "oracle_id": "oid-atraxa"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    with app.app_context():
        folder = db.session.get(Folder, deck_id)
        assert folder is not None
        assert folder.commander_name == "Atraxa"
        assert folder.commander_oracle_id == "oid-atraxa"


def test_set_commander_json_resolves_card_lookup(client, create_user, app, monkeypatch):
    from core.domains.decks.services import commander_assignment_service

    user, password = create_user(email="commander-json@example.com", username="commander_json")
    deck_id, card_id = _create_deck(
        app,
        owner_user_id=user.id,
        name="JSON Deck",
        cards=[
            {
                "name": "Mystic Tutor",
                "set_code": "MIR",
                "collector_number": "12",
                "oracle_id": None,
            }
        ],
    )

    monkeypatch.setattr(
        commander_assignment_service,
        "find_by_set_cn",
        lambda set_code, collector_number, name: {"oracle_id": "oid-mystic-tutor"}
        if (str(set_code).upper(), str(collector_number), str(name)) == ("MIR", "12", "Mystic Tutor")
        else None,
    )

    _login(client, user.email, password)
    response = client.post(
        f"/folders/{deck_id}/commander/set",
        json={"card_id": card_id},
        follow_redirects=False,
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["name"] == "Mystic Tutor"
    with app.app_context():
        folder = db.session.get(Folder, deck_id)
        assert folder is not None
        assert folder.commander_name == "Mystic Tutor"
        assert folder.commander_oracle_id == "oid-mystic-tutor"


def test_clear_commander_json_clears_folder(client, create_user, app):
    user, password = create_user(email="commander-clear@example.com", username="commander_clear")
    deck_id, _ = _create_deck(app, owner_user_id=user.id, name="Clear Deck")

    with app.app_context():
        folder = db.session.get(Folder, deck_id)
        folder.commander_name = "Atraxa"
        folder.commander_oracle_id = "oid-atraxa"
        db.session.commit()

    _login(client, user.email, password)
    response = client.post(
        f"/folders/{deck_id}/commander/clear",
        json={},
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert response.get_json() == {"ok": True}
    with app.app_context():
        folder = db.session.get(Folder, deck_id)
        assert folder is not None
        assert folder.commander_name is None
        assert folder.commander_oracle_id is None
