import pytest

from models import Card, Folder, db


@pytest.fixture
def _clean_db(app):
    with app.app_context():
        db.session.query(Card).delete()
        db.session.query(Folder).delete()
        db.session.commit()
    yield
    with app.app_context():
        db.session.query(Card).delete()
        db.session.query(Folder).delete()
        db.session.commit()


def test_deck_list_uses_commander_placeholder(client, app, monkeypatch, _clean_db):
    from routes import cards as cards_route

    placeholder_path = "/static/img/card-placeholder.svg"

    with app.app_context():
        folder = Folder(
            name="Proxy Deck",
            category=Folder.CATEGORY_DECK,
            owner=None,
            is_proxy=True,
            commander_name="Offline Commander",
            commander_oracle_id=None,
        )
        db.session.add(folder)
        db.session.flush()

        card = Card(
            name="Forest",
            set_code="LTR",
            collector_number="278",
            folder_id=folder.id,
            quantity=1,
            is_proxy=True,
            lang="en",
        )
        db.session.add(card)
        db.session.commit()

    monkeypatch.setattr(cards_route, "evaluate_commander_bracket", lambda *args, **kwargs: {})
    monkeypatch.setattr(cards_route, "prints_for_oracle", lambda *args, **kwargs: ())
    monkeypatch.setattr(cards_route, "_lookup_print_data", lambda *args, **kwargs: {})

    response = client.get("/decks")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert placeholder_path in body


def test_cards_type_filter_uses_cache_fallback(app, monkeypatch):
    from routes import cards as cards_route

    with app.app_context():
        db.session.query(Card).delete()
        db.session.query(Folder).delete()
        folder = Folder(name="My Collection", category=Folder.CATEGORY_COLLECTION)
        db.session.add(folder)
        db.session.flush()
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

        monkeypatch.setattr(cards_route, "ensure_cache_loaded", lambda: True)

        def _fake_prints(oracle_id):
            if oracle_id == "fake-creature":
                return [{"type_line": "Creature — Human Rogue"}]
            return []

        monkeypatch.setattr(cards_route, "prints_for_oracle", _fake_prints)
        monkeypatch.setattr(cards_route, "find_by_set_cn", lambda *args, **kwargs: None)

        filtered = cards_route._apply_cache_type_color_filters(
            Card.query,
            selected_types=["creature"],
            selected_colors=[],
            color_mode="contains",
            type_mode="contains",
        )
        results = filtered.with_entities(Card.name).all()
        assert any(row[0] == "Invisible Stalker" for row in results)
