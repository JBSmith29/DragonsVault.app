from models import Card, Folder, FolderRole, db


def _login(client, identifier, password):
    return client.post(
        "/login",
        data={"identifier": identifier, "password": password},
        follow_redirects=True,
    )


def _create_owned_folder(app, owner_id, *, name, category=Folder.CATEGORY_DECK):
    with app.app_context():
        folder = Folder(
            name=name,
            category=category,
            owner_user_id=owner_id,
        )
        db.session.add(folder)
        db.session.flush()
        db.session.add(FolderRole(folder_id=folder.id, role=category))
        db.session.commit()
        return folder.id


def test_bulk_move_cards_merges_quantity_into_existing_card(client, create_user, app):
    user, password = create_user(email="move-owner@example.com", username="move_owner")
    source_id = _create_owned_folder(app, user.id, name="Source Deck")
    target_id = _create_owned_folder(app, user.id, name="Target Deck")

    with app.app_context():
        source_card = Card(
            name="Sol Ring",
            set_code="CMM",
            collector_number="100",
            folder_id=source_id,
            quantity=3,
            lang="en",
            is_foil=False,
        )
        target_card = Card(
            name="Sol Ring",
            set_code="CMM",
            collector_number="100",
            folder_id=target_id,
            quantity=4,
            lang="en",
            is_foil=False,
        )
        db.session.add_all([source_card, target_card])
        db.session.commit()
        source_card_id = source_card.id
        target_card_id = target_card.id

    _login(client, user.email, password)
    response = client.post(
        "/cards/bulk-move",
        json={"card_ids": [source_card_id], "target_folder_id": target_id, "quantity": 2},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["merged"] == 2
    assert payload["moved"] == 0

    with app.app_context():
        source_card = db.session.get(Card, source_card_id)
        target_card = db.session.get(Card, target_card_id)
        assert source_card is not None
        assert source_card.quantity == 1
        assert target_card is not None
        assert target_card.quantity == 6


def test_bulk_delete_cards_removes_selected_rows(client, create_user, app):
    user, password = create_user(email="delete-owner@example.com", username="delete_owner")
    folder_id = _create_owned_folder(app, user.id, name="Delete Deck")

    with app.app_context():
        card = Card(
            name="Counterspell",
            set_code="7ED",
            collector_number="70",
            folder_id=folder_id,
            quantity=2,
            lang="en",
        )
        survivor = Card(
            name="Island",
            set_code="7ED",
            collector_number="335",
            folder_id=folder_id,
            quantity=5,
            lang="en",
        )
        db.session.add_all([card, survivor])
        db.session.commit()
        card_id = card.id
        survivor_id = survivor.id

    _login(client, user.email, password)
    response = client.post(
        f"/folders/{folder_id}/cards/bulk-delete",
        json={"card_ids": [card_id]},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["deleted"] == 1
    assert payload["deleted_qty"] == 2

    with app.app_context():
        assert db.session.get(Card, card_id) is None
        assert db.session.get(Card, survivor_id) is not None


def test_api_card_printing_options_returns_cached_print_choices(client, create_user, app, monkeypatch):
    from core.domains.cards.services import card_mutation_service

    user, password = create_user(email="printing-owner@example.com", username="printing_owner")
    folder_id = _create_owned_folder(app, user.id, name="Printing Deck")

    with app.app_context():
        card = Card(
            name="Lightning Bolt",
            set_code="M11",
            collector_number="146",
            folder_id=folder_id,
            quantity=1,
            oracle_id="oracle-bolt",
            lang="en",
        )
        db.session.add(card)
        db.session.commit()
        card_id = card.id

    monkeypatch.setattr(card_mutation_service, "cache_ready", lambda: True)
    monkeypatch.setattr(card_mutation_service.sc, "image_for_print", lambda pr: {"normal": (pr.get("image_uris") or {}).get("normal")})
    monkeypatch.setattr(
        card_mutation_service,
        "prints_for_oracle",
        lambda oracle_id: [
            {
                "oracle_id": oracle_id,
                "set": "neo",
                "set_name": "Kamigawa: Neon Dynasty",
                "collector_number": "42",
                "lang": "en",
                "finishes": ["nonfoil", "foil"],
                "promo_types": [],
                "image_uris": {"normal": "https://img.test/neo-42.jpg"},
            }
        ],
    )

    _login(client, user.email, password)
    response = client.get(f"/api/card/{card_id}/printing-options")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["current"] == "M11::146::EN"
    assert payload["current_finish"] == "nonfoil"
    assert payload["options"][0]["value"] == "NEO::42::EN"
    assert payload["options"][0]["image"] == "https://img.test/neo-42.jpg"


def test_api_update_card_printing_merges_into_existing_print_row(client, create_user, app, monkeypatch):
    from core.domains.cards.services import card_mutation_service

    user, password = create_user(email="update-owner@example.com", username="update_owner")
    folder_id = _create_owned_folder(app, user.id, name="Update Deck")

    with app.app_context():
        source = Card(
            name="Arcane Signet",
            set_code="CMM",
            collector_number="999",
            folder_id=folder_id,
            quantity=1,
            oracle_id="oracle-signet",
            lang="en",
            is_foil=False,
        )
        target = Card(
            name="Arcane Signet",
            set_code="NEO",
            collector_number="42",
            folder_id=folder_id,
            quantity=3,
            oracle_id="oracle-signet",
            lang="en",
            is_foil=False,
        )
        db.session.add_all([source, target])
        db.session.commit()
        source_id = source.id
        target_id = target.id

    monkeypatch.setattr(card_mutation_service, "cache_ready", lambda: True)
    monkeypatch.setattr(
        card_mutation_service,
        "prints_for_oracle",
        lambda oracle_id: [
            {
                "name": "Arcane Signet",
                "oracle_id": oracle_id,
                "set": "neo",
                "collector_number": "42",
                "lang": "en",
            }
        ],
    )
    monkeypatch.setattr(
        card_mutation_service,
        "metadata_from_print",
        lambda pr: {
            "type_line": "Artifact",
            "rarity": "common",
            "oracle_text": "Tap: Add one mana of any color.",
            "mana_value": 2,
            "colors": "",
            "color_identity": "",
            "color_identity_mask": 0,
            "layout": "normal",
            "faces_json": None,
        },
    )

    _login(client, user.email, password)
    response = client.post(
        f"/api/card/{source_id}/update-printing",
        json={"printing": "NEO::42::EN", "finish": "nonfoil"},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True

    with app.app_context():
        assert db.session.get(Card, source_id) is None
        target = db.session.get(Card, target_id)
        assert target is not None
        assert target.quantity == 4
