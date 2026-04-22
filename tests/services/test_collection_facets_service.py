from models import Card, Folder, FolderRole, db


def test_collection_rarity_options_includes_lookup_fallback_values(app, create_user, monkeypatch, db_session):
    from core.domains.cards.services import collection_facets_service

    user, _password = create_user(email="collection-facets@example.com", username="collection-facets")

    with app.app_context():
        folder = Folder(
            name="Facet Binder",
            category=Folder.CATEGORY_COLLECTION,
            owner_user_id=user.id,
        )
        db.session.add(folder)
        db.session.flush()
        db.session.add(FolderRole(folder_id=folder.id, role=FolderRole.ROLE_COLLECTION))
        db.session.add(
            Card(
                name="Mystery Card",
                set_code="TST",
                collector_number="7",
                folder_id=folder.id,
                quantity=1,
                oracle_id="oid-mystery-card",
                rarity=None,
            )
        )
        db.session.commit()

    monkeypatch.setattr(collection_facets_service, "_cache_fetch", lambda _key, _ttl, builder: builder())
    monkeypatch.setattr(collection_facets_service, "_lookup_print_data", lambda *args, **kwargs: {"rarity": "serialized"})

    with app.app_context():
        options = collection_facets_service.collection_rarity_options()

    assert any(option["value"] == "serialized" and option["label"] == "Serialized" for option in options)

