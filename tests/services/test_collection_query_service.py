from flask_login import login_user

from models import Card, Folder, FolderRole, User, db


def test_build_collection_browser_context_uses_cache_fallback_for_type_filter(app, create_user, monkeypatch):
    from core.domains.cards.services import collection_query_service, collection_request_service

    user, _password = create_user(
        email="collection-query@example.com",
        username="collection-query",
    )

    with app.app_context():
        folder = Folder(
            name="My Collection",
            category=Folder.CATEGORY_COLLECTION,
            owner_user_id=user.id,
        )
        db.session.add(folder)
        db.session.flush()
        db.session.add(FolderRole(folder_id=folder.id, role=FolderRole.ROLE_COLLECTION))
        creature = Card(
            name="Invisible Stalker",
            set_code="ISD",
            collector_number="63",
            folder_id=folder.id,
            quantity=1,
            oracle_id="fake-creature",
            type_line=None,
        )
        db.session.add(creature)
        db.session.commit()
        creature_id = creature.id
        user = db.session.get(User, user.id)

    monkeypatch.setattr(collection_query_service.sc, "cache_ready", lambda: True)
    monkeypatch.setattr(
        collection_query_service,
        "_bulk_print_lookup",
        lambda cards, **kwargs: {creature_id: {"type_line": "Creature - Human Rogue"}},
    )
    monkeypatch.setattr(
        collection_query_service,
        "build_collection_card_list_items",
        lambda cards, **kwargs: cards,
    )

    with app.app_context():
        with app.test_request_context("/cards?type=creature"):
            login_user(user)
            params = collection_request_service.parse_collection_browser_request()
            context = collection_query_service.build_collection_browser_context(params)

    assert len(context["cards"]) == 1
    assert context["cards"][0].name == "Invisible Stalker"
