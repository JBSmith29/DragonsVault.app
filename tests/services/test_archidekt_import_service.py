from core.domains.games.services import archidekt_import_service as imp
from core.domains.games.services import archidekt_service
from core.domains.decks.services.proxy_decks import ResolvedCard
from models import Card, Folder


_DECK = {
    "id": 555,
    "name": "Azula Aggro",
    "commanders": ["Fire Lord Azula"],
    "commander_name": "Fire Lord Azula",
    "bracket": 2,
    "cards": [{"name": "Sol Ring", "quantity": 1}, {"name": "Command Tower", "quantity": 1}],
    "url": "https://archidekt.com/decks/555",
}


def _patch(monkeypatch, deck=None):
    monkeypatch.setattr(archidekt_service, "fetch_deck", lambda deck_id: deck or _DECK)
    # Avoid the Scryfall cache: return two resolved cards and skip metadata.
    monkeypatch.setattr(
        imp,
        "resolve_proxy_cards",
        lambda lines: (
            [
                ResolvedCard(name="Sol Ring", quantity=1, oracle_id="oid-sol", set_code="C21", collector_number="1", lang="en"),
                ResolvedCard(name="Command Tower", quantity=1, oracle_id="oid-ct", set_code="C21", collector_number="2", lang="en"),
            ],
            [],
        ),
    )
    monkeypatch.setattr(imp, "_cache_ready", lambda: False)
    monkeypatch.setattr(imp, "unique_oracle_by_name", lambda name: None)


def test_import_creates_folder_with_commander_and_bracket(app, create_user, monkeypatch):
    user, _ = create_user(email="ad1@example.com", username="ad1")
    _patch(monkeypatch)

    with app.app_context():
        summary = imp.import_archidekt_deck("555", owner_user_id=user.id)
        assert summary["refreshed"] is False
        assert summary["bracket"] == 2
        assert summary["card_count"] == 2

        folder = Folder.query.get(summary["folder_id"])
        assert folder.owner_user_id == user.id
        assert folder.archidekt_deck_id == "555"
        assert folder.archidekt_bracket == 2
        assert folder.commander_name == "Fire Lord Azula"
        assert folder.is_proxy is True
        assert Card.query.filter_by(folder_id=folder.id).count() == 2


def test_reimport_refreshes_same_folder(app, create_user, monkeypatch):
    user, _ = create_user(email="ad2@example.com", username="ad2")
    _patch(monkeypatch)

    with app.app_context():
        first = imp.import_archidekt_deck("555", owner_user_id=user.id)

        # Second import of the same Archidekt deck reuses the folder + replaces cards.
        updated = dict(_DECK, bracket=4, cards=[{"name": "Sol Ring", "quantity": 1}])
        monkeypatch.setattr(archidekt_service, "fetch_deck", lambda deck_id: updated)
        second = imp.import_archidekt_deck("555", owner_user_id=user.id)

        assert second["refreshed"] is True
        assert second["folder_id"] == first["folder_id"]
        assert second["bracket"] == 4
        assert Folder.query.filter_by(owner_user_id=user.id, archidekt_deck_id="555").count() == 1
        assert Card.query.filter_by(folder_id=second["folder_id"]).count() == 2  # mocked resolver returns 2
