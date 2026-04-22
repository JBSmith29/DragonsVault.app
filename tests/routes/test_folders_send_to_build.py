from models import BuildSession, BuildSessionCard, Card, Folder, FolderRole, db


def _login(client, identifier, password):
    return client.post(
        "/login",
        data={"identifier": identifier, "password": password},
        follow_redirects=True,
    )


def _create_deck(
    app,
    *,
    owner_user_id: int,
    name: str,
    commander_oracle_id: str | None = None,
    commander_name: str | None = None,
    cards: list[dict] | None = None,
) -> int:
    rows = cards or []
    with app.app_context():
        deck = Folder(
            name=name,
            category=Folder.CATEGORY_DECK,
            owner_user_id=owner_user_id,
            commander_oracle_id=commander_oracle_id,
            commander_name=commander_name,
        )
        db.session.add(deck)
        db.session.flush()
        db.session.add(FolderRole(folder_id=deck.id, role=FolderRole.ROLE_DECK))
        for row in rows:
            db.session.add(
                Card(
                    name=row["name"],
                    set_code=row.get("set_code", "TST"),
                    collector_number=row.get("collector_number", "1"),
                    folder_id=deck.id,
                    quantity=int(row.get("quantity", 1)),
                    oracle_id=row.get("oracle_id"),
                    lang="en",
                )
            )
        db.session.commit()
        return deck.id


def _session_card_counts(session_id: int) -> dict[str, int]:
    rows = BuildSessionCard.query.filter_by(session_id=session_id).all()
    return {row.card_oracle_id: int(row.quantity or 0) for row in rows}


def test_send_to_build_copies_deck_cards_and_selected_additions(client, create_user, app):
    user, password = create_user(
        email="sendbuild_owner@example.com",
        username="sendbuild_owner",
    )
    deck_id = _create_deck(
        app,
        owner_user_id=user.id,
        name="Ruby Ramp",
        commander_oracle_id="oid-commander",
        commander_name="Ruby Commander",
        cards=[
            {"name": "Dragon One", "oracle_id": "oid-dragon-1", "quantity": 2},
            {"name": "Dragon Two", "oracle_id": "oid-dragon-2", "quantity": 1},
        ],
    )

    _login(client, user.email, password)
    response = client.post(
        f"/folders/{deck_id}/send-to-build",
        data={"card_oracle_id": ["oid-dragon-2", "oid-extra-1"]},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert "/decks/build/" in (response.headers.get("Location") or "")

    with app.app_context():
        session = BuildSession.query.filter_by(owner_user_id=user.id).one()
        counts = _session_card_counts(session.id)
        assert session.build_name == "Ruby Ramp"
        assert session.commander_oracle_id == "oid-commander"
        assert counts["oid-dragon-1"] == 2
        assert counts["oid-dragon-2"] == 2
        assert counts["oid-extra-1"] == 1
        assert counts["oid-commander"] == 1


def test_send_to_build_resolves_missing_oracle_ids_without_commander(client, create_user, app, monkeypatch):
    from core.domains.decks.services import send_to_build_service

    user, password = create_user(
        email="sendbuild_resolve@example.com",
        username="sendbuild_resolve",
    )
    deck_id = _create_deck(
        app,
        owner_user_id=user.id,
        name="Resolver Deck",
        cards=[
            {
                "name": "Mystic Tutor",
                "set_code": "MIR",
                "collector_number": "12",
                "quantity": 2,
                "oracle_id": None,
            },
            {
                "name": "Island",
                "set_code": "TST",
                "collector_number": "2",
                "quantity": 1,
                "oracle_id": None,
            },
            {
                "name": "Mystery Card",
                "set_code": "UNK",
                "collector_number": "999",
                "quantity": 1,
                "oracle_id": None,
            },
        ],
    )

    monkeypatch.setattr(send_to_build_service.sc, "ensure_cache_loaded", lambda: True)

    def _fake_find_by_set_cn(set_code, collector_number, name):
        if str(set_code).upper() == "MIR" and str(collector_number) == "12" and str(name) == "Mystic Tutor":
            return {"oracle_id": "oid-mystic-tutor"}
        return None

    def _fake_unique_oracle_by_name(name):
        if str(name) == "Island":
            return "oid-island"
        return None

    monkeypatch.setattr(send_to_build_service, "find_by_set_cn", _fake_find_by_set_cn)
    monkeypatch.setattr(send_to_build_service.sc, "unique_oracle_by_name", _fake_unique_oracle_by_name)

    _login(client, user.email, password)
    response = client.post(
        f"/folders/{deck_id}/send-to-build",
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert "/decks/build/" in (response.headers.get("Location") or "")

    with app.app_context():
        session = BuildSession.query.filter_by(owner_user_id=user.id).one()
        counts = _session_card_counts(session.id)
        assert session.commander_oracle_id is None
        assert session.commander_name is None
        assert counts == {
            "oid-mystic-tutor": 2,
            "oid-island": 1,
        }
