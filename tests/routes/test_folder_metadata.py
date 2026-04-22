from models import Card, DeckTagMap, Folder, FolderRole, db
from core.domains.decks.services.deck_tags import set_folder_deck_tag


def _login(client, identifier, password):
    return client.post(
        "/login",
        data={"identifier": identifier, "password": password},
        follow_redirects=True,
    )


def _create_folder(
    app,
    *,
    owner_user_id: int,
    name: str,
    category: str = Folder.CATEGORY_DECK,
    cards: list[dict] | None = None,
    commander_name: str | None = None,
    commander_oracle_id: str | None = None,
) -> int:
    rows = cards or []
    primary_role = FolderRole.ROLE_COLLECTION if category == Folder.CATEGORY_COLLECTION else FolderRole.ROLE_DECK
    with app.app_context():
        folder = Folder(
            name=name,
            category=category,
            owner_user_id=owner_user_id,
            commander_name=commander_name,
            commander_oracle_id=commander_oracle_id,
        )
        db.session.add(folder)
        db.session.flush()
        db.session.add(FolderRole(folder_id=folder.id, role=primary_role))
        for row in rows:
            db.session.add(
                Card(
                    name=row["name"],
                    set_code=row.get("set_code", "TST"),
                    collector_number=row.get("collector_number", "1"),
                    folder_id=folder.id,
                    quantity=int(row.get("quantity", 1)),
                    oracle_id=row.get("oracle_id"),
                    lang=row.get("lang", "en"),
                    is_foil=bool(row.get("is_foil", False)),
                )
            )
        db.session.commit()
        return folder.id


def test_set_folder_tag_json_updates_folder_tag(client, create_user, app):
    user, password = create_user(email="folder-tag@example.com", username="folder_tag")
    folder_id = _create_folder(app, owner_user_id=user.id, name="Tag Deck")

    _login(client, user.email, password)
    response = client.post(
        f"/folders/{folder_id}/tag/set",
        json={"tag": "Spellslinger"},
    )

    assert response.status_code == 200
    assert response.get_json() == {"ok": True, "tag": "Spellslinger", "category": "User Tags"}
    with app.app_context():
        folder = db.session.get(Folder, folder_id)
        assert folder is not None
        assert folder.deck_tag == "Spellslinger"
        tag_map = DeckTagMap.query.filter_by(folder_id=folder_id).one()
        assert tag_map.locked is True
        assert tag_map.source == "user"


def test_clear_folder_tag_json_removes_existing_tag(client, create_user, app):
    user, password = create_user(email="folder-tag-clear@example.com", username="folder_tag_clear")
    folder_id = _create_folder(app, owner_user_id=user.id, name="Clear Tag Deck")

    with app.app_context():
        folder = db.session.get(Folder, folder_id)
        assert folder is not None
        set_folder_deck_tag(folder, "Tokens", source="user", locked=True)
        db.session.commit()

    _login(client, user.email, password)
    response = client.post(
        f"/folders/{folder_id}/tag/clear",
        json={},
    )

    assert response.status_code == 200
    assert response.get_json() == {"ok": True}
    with app.app_context():
        folder = db.session.get(Folder, folder_id)
        assert folder is not None
        assert folder.deck_tag is None
        assert DeckTagMap.query.filter_by(folder_id=folder_id).count() == 0


def test_set_folder_owner_and_proxy_json_update_folder(client, create_user, app):
    user, password = create_user(email="folder-owner@example.com", username="folder_owner")
    folder_id = _create_folder(app, owner_user_id=user.id, name="Owner Deck")

    _login(client, user.email, password)

    owner_response = client.post(
        f"/folders/{folder_id}/owner/set",
        json={"owner": "Paper Owner"},
    )
    proxy_response = client.post(
        f"/folders/{folder_id}/proxy/set",
        json={"is_proxy": True},
    )

    assert owner_response.status_code == 200
    assert owner_response.get_json() == {"ok": True, "owner": "Paper Owner"}
    assert proxy_response.status_code == 200
    assert proxy_response.get_json() == {"ok": True, "is_proxy": True}
    with app.app_context():
        folder = db.session.get(Folder, folder_id)
        assert folder is not None
        assert folder.owner == "Paper Owner"
        assert folder.is_proxy is True


def test_rename_proxy_deck_dedupes_name(client, create_user, app):
    user, password = create_user(email="folder-rename@example.com", username="folder_rename")
    folder_id = _create_folder(app, owner_user_id=user.id, name="Old Name")
    _create_folder(app, owner_user_id=user.id, name="Target Name")

    _login(client, user.email, password)
    response = client.post(
        f"/folders/{folder_id}/rename",
        data={"new_name": "Target Name"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    with app.app_context():
        folder = db.session.get(Folder, folder_id)
        assert folder is not None
        assert folder.name == "Target Name (2)"


def test_refresh_folder_edhrec_starts_background_thread(client, create_user, app, monkeypatch):
    from core.domains.decks.services import folder_metadata_service

    user, password = create_user(email="folder-refresh@example.com", username="folder_refresh")
    folder_id = _create_folder(
        app,
        owner_user_id=user.id,
        name="Refresh Deck",
        commander_name="Atraxa",
    )

    started: dict[str, object] = {}

    class FakeThread:
        def __init__(self, target=None, name=None, daemon=None):
            started["target"] = target
            started["name"] = name
            started["daemon"] = daemon

        def start(self):
            started["started"] = True

    monkeypatch.setattr(folder_metadata_service.sc, "ensure_cache_loaded", lambda: True)
    monkeypatch.setattr(folder_metadata_service.sc, "unique_oracle_by_name", lambda name: "oid-atraxa")
    monkeypatch.setattr(folder_metadata_service.threading, "Thread", FakeThread)

    _login(client, user.email, password)
    response = client.post(
        f"/folders/{folder_id}/edhrec/refresh",
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert started["started"] is True
    assert started["daemon"] is True
    assert started["name"] == f"folder-edhrec-{folder_id}"
    assert callable(started["target"])


def test_folder_cards_json_and_counts_return_sorted_payload(client, create_user, app):
    user, password = create_user(email="folder-cards@example.com", username="folder_cards")
    folder_id = _create_folder(
        app,
        owner_user_id=user.id,
        name="Payload Deck",
        cards=[
            {
                "name": "Zulu Card",
                "set_code": "ZZZ",
                "collector_number": "10",
                "quantity": 2,
                "oracle_id": "oid-zulu",
                "is_foil": True,
            },
            {
                "name": "Alpha Card",
                "set_code": "BBB",
                "collector_number": "9",
                "quantity": 1,
                "oracle_id": "oid-alpha-b",
            },
            {
                "name": "Alpha Card",
                "set_code": "AAA",
                "collector_number": "1",
                "quantity": 3,
                "oracle_id": "oid-alpha-a",
            },
        ],
    )

    _login(client, user.email, password)
    cards_response = client.get(f"/folders/{folder_id}/cards.json")
    counts_response = client.get(f"/api/folder/{folder_id}/counts")

    assert cards_response.status_code == 200
    assert [card["name"] for card in cards_response.get_json()] == [
        "Alpha Card",
        "Alpha Card",
        "Zulu Card",
    ]
    assert [card["set_code"] for card in cards_response.get_json()] == ["aaa", "bbb", "zzz"]
    assert cards_response.get_json()[0]["collector_number"] == "1"
    assert cards_response.get_json()[2]["is_foil"] is True

    assert counts_response.status_code == 200
    assert counts_response.get_json() == {"ok": True, "unique": 3, "total": 6}
