"""Route + service tests for the self-contained Game Vault feature."""

from core.domains.game_vault.services import vault_service
from core.domains.game_vault.services.importers.base import ImportedDeck


def _login(client, identifier, password):
    return client.post(
        "/login",
        data={"identifier": identifier, "password": password},
        follow_redirects=True,
    )


def _fake_deck(**over):
    base = dict(
        source="archidekt",
        source_id="42",
        url="https://archidekt.com/decks/42",
        name="Atraxa Superfriends",
        commanders=["Atraxa, Praetors' Voice"],
        color_identity="WUBG",
        format="commander",
        bracket=3,
        cards=[{"name": "Sol Ring", "quantity": 1}],
    )
    base.update(over)
    return ImportedDeck(**base)


def _patch_import(monkeypatch, deck):
    monkeypatch.setattr(vault_service, "import_from_url", lambda url: deck)
    monkeypatch.setattr(vault_service, "fetch_deck", lambda source, ref: deck)
    monkeypatch.setattr(
        vault_service.scryfall_lookup, "lookup_commander", lambda name: (None, None)
    )


# --------------------------------------------------------------------------- #
# Page
# --------------------------------------------------------------------------- #
def test_page_requires_login(client):
    # The global auth guard blocks anonymous access (redirect for HTML, 401 for
    # JSON-preferring clients). Either way, it must not return the page.
    resp = client.get("/game-vault/", follow_redirects=False)
    assert resp.status_code in (301, 302, 401)
    assert b"game-vault.js" not in resp.data

    api = client.get("/game-vault/api/state", follow_redirects=False)
    assert api.status_code in (301, 302, 401)


def test_page_loads_when_authenticated(client, create_user):
    user, password = create_user(email="gv-page@example.com", username="gvpage")
    _login(client, user.email, password)
    resp = client.get("/game-vault/")
    assert resp.status_code == 200
    assert b"Game" in resp.data and b"game-vault.js" in resp.data


# --------------------------------------------------------------------------- #
# Players
# --------------------------------------------------------------------------- #
def test_player_crud(client, create_user):
    user, password = create_user(email="gv-players@example.com", username="gvplayers")
    _login(client, user.email, password)

    r = client.post("/game-vault/api/players", json={"name": "Alex", "note": "aggro"})
    assert r.status_code == 201
    pid = r.get_json()["player"]["id"]

    # duplicate rejected
    r = client.post("/game-vault/api/players", json={"name": "Alex"})
    assert r.status_code == 400

    r = client.get("/game-vault/api/players")
    assert r.status_code == 200
    assert any(p["name"] == "Alex" for p in r.get_json()["players"])

    r = client.patch(f"/game-vault/api/players/{pid}", json={"name": "Alexis"})
    assert r.status_code == 200
    assert r.get_json()["player"]["name"] == "Alexis"

    r = client.delete(f"/game-vault/api/players/{pid}")
    assert r.status_code == 200
    assert client.get("/game-vault/api/players").get_json()["players"] == []


def test_players_are_scoped_per_owner(client, create_user):
    u1, p1 = create_user(email="gv-a@example.com", username="gva")
    _login(client, u1.email, p1)
    client.post("/game-vault/api/players", json={"name": "OnlyMine"})
    client.get("/logout")

    u2, p2 = create_user(email="gv-b@example.com", username="gvb")
    _login(client, u2.email, p2)
    players = client.get("/game-vault/api/players").get_json()["players"]
    assert players == []


# --------------------------------------------------------------------------- #
# Deck import (network mocked)
# --------------------------------------------------------------------------- #
def test_import_deck_by_url(client, create_user, monkeypatch):
    user, password = create_user(email="gv-import@example.com", username="gvimport")
    _login(client, user.email, password)
    _patch_import(monkeypatch, _fake_deck())

    pid = client.post("/game-vault/api/players", json={"name": "Sam"}).get_json()["player"]["id"]
    r = client.post(f"/game-vault/api/players/{pid}/decks", json={"url": "https://archidekt.com/decks/42"})
    assert r.status_code == 201
    deck = r.get_json()["deck"]
    assert deck["name"] == "Atraxa Superfriends"
    assert deck["commander_name"] == "Atraxa, Praetors' Voice"
    assert deck["colors"] == ["W", "U", "B", "G"]
    assert deck["bracket"] == 3

    # re-import same source deck updates rather than duplicating
    r = client.post(f"/game-vault/api/players/{pid}/decks", json={"url": "https://archidekt.com/decks/42"})
    assert r.status_code == 201
    players = client.get("/game-vault/api/players").get_json()["players"]
    sam = next(p for p in players if p["name"] == "Sam")
    assert sam["deck_count"] == 1


def test_import_bad_link_returns_400(client, create_user):
    user, password = create_user(email="gv-bad@example.com", username="gvbad")
    _login(client, user.email, password)
    pid = client.post("/game-vault/api/players", json={"name": "Kim"}).get_json()["player"]["id"]
    r = client.post(f"/game-vault/api/players/{pid}/decks", json={"url": "https://example.com/x"})
    assert r.status_code == 400
    assert "error" in r.get_json()


def test_set_deck_bracket_manual(client, create_user, monkeypatch):
    user, password = create_user(email="gv-bracket@example.com", username="gvbracket")
    _login(client, user.email, password)
    _patch_import(monkeypatch, _fake_deck(bracket=3))
    pid = client.post("/game-vault/api/players", json={"name": "Mo"}).get_json()["player"]["id"]
    did = client.post(f"/game-vault/api/players/{pid}/decks",
                      json={"url": "https://archidekt.com/decks/42"}).get_json()["deck"]["id"]

    # Hand-set the bracket to 5.
    r = client.patch(f"/game-vault/api/decks/{did}", json={"bracket": 5})
    assert r.status_code == 200
    deck = r.get_json()["deck"]
    assert deck["bracket"] == 5 and deck["bracket_manual"] is True and deck["bracket_is_estimated"] is False

    # A re-sync must NOT overwrite the manual bracket (import says 3).
    r = client.post(f"/game-vault/api/decks/{did}/sync")
    assert r.get_json()["deck"]["bracket"] == 5

    # Out-of-range rejected.
    assert client.patch(f"/game-vault/api/decks/{did}", json={"bracket": 9}).status_code == 400

    # Clearing reverts to source (import bracket 3, no longer manual).
    r = client.patch(f"/game-vault/api/decks/{did}", json={"bracket": None})
    assert r.status_code == 200
    deck = r.get_json()["deck"]
    assert deck["bracket_manual"] is False and deck["bracket"] == 3


def test_delete_deck(client, create_user, monkeypatch):
    user, password = create_user(email="gv-deldeck@example.com", username="gvdeldeck")
    _login(client, user.email, password)
    _patch_import(monkeypatch, _fake_deck())
    pid = client.post("/game-vault/api/players", json={"name": "Lee"}).get_json()["player"]["id"]
    did = client.post(f"/game-vault/api/players/{pid}/decks", json={"url": "https://archidekt.com/decks/42"}).get_json()["deck"]["id"]
    assert client.delete(f"/game-vault/api/decks/{did}").status_code == 200


# --------------------------------------------------------------------------- #
# Games + stats
# --------------------------------------------------------------------------- #
def test_log_game_and_stats(client, create_user):
    user, password = create_user(email="gv-game@example.com", username="gvgame")
    _login(client, user.email, password)
    a = client.post("/game-vault/api/players", json={"name": "Ann"}).get_json()["player"]["id"]
    b = client.post("/game-vault/api/players", json={"name": "Bob"}).get_json()["player"]["id"]

    # No "format" sent — it should default to commander. Infinite-win flagged.
    r = client.post("/game-vault/api/games", json={
        "played_at": "2026-07-01",
        "turns": 9,
        "win_condition": "combo",
        "infinite_win": True,
        "participants": [
            {"player_id": a, "is_winner": True, "turn_order": 1},
            {"player_id": b, "is_winner": False, "turn_order": 2},
        ],
    })
    assert r.status_code == 201, r.get_json()
    game = r.get_json()["game"]
    assert game["winner_name"] == "Ann"
    assert game["format"] == "commander"
    assert game["infinite_win"] is True
    assert len(game["participants"]) == 2

    stats = client.get("/game-vault/api/stats").get_json()["stats"]
    assert stats["total_games"] == 1
    assert stats["infinite_wins"] == 1
    ann = next(p for p in stats["players"] if p["label"] == "Ann")
    assert ann["win_rate"] == 100.0
    bob = next(p for p in stats["players"] if p["label"] == "Bob")
    assert bob["win_rate"] == 0.0

    # Turn-order metrics: seat 1 (Ann) won, seat 2 (Bob) did not.
    turn = {t["seat"]: t for t in stats["turn_order"]}
    assert turn[1]["win_rate"] == 100.0
    assert turn[2]["win_rate"] == 0.0
    assert stats["win_conditions"][0]["label"] == "combo"


def test_game_requires_two_players(client, create_user):
    user, password = create_user(email="gv-one@example.com", username="gvone")
    _login(client, user.email, password)
    a = client.post("/game-vault/api/players", json={"name": "Solo"}).get_json()["player"]["id"]
    r = client.post("/game-vault/api/games", json={
        "played_at": "2026-07-01",
        "participants": [{"player_id": a, "is_winner": True}],
    })
    assert r.status_code == 400


def test_two_winners_rejected(client, create_user):
    user, password = create_user(email="gv-two-win@example.com", username="gvtwowin")
    _login(client, user.email, password)
    a = client.post("/game-vault/api/players", json={"name": "P1"}).get_json()["player"]["id"]
    b = client.post("/game-vault/api/players", json={"name": "P2"}).get_json()["player"]["id"]
    r = client.post("/game-vault/api/games", json={
        "played_at": "2026-07-01",
        "participants": [
            {"player_id": a, "is_winner": True},
            {"player_id": b, "is_winner": True},
        ],
    })
    assert r.status_code == 400


def test_edit_game(client, create_user):
    user, password = create_user(email="gv-edit@example.com", username="gvedit")
    _login(client, user.email, password)
    a = client.post("/game-vault/api/players", json={"name": "Ann"}).get_json()["player"]["id"]
    b = client.post("/game-vault/api/players", json={"name": "Bob"}).get_json()["player"]["id"]
    c = client.post("/game-vault/api/players", json={"name": "Cy"}).get_json()["player"]["id"]

    gid = client.post("/game-vault/api/games", json={
        "played_at": "2026-07-01", "turns": 5,
        "participants": [
            {"player_id": a, "is_winner": True, "turn_order": 1, "deck_name": "Old Deck"},
            {"player_id": b, "is_winner": False, "turn_order": 2},
        ],
    }).get_json()["game"]["id"]

    # Edit: change winner to Bob, add a third seat, bump turns, keep Ann's snapshot.
    r = client.patch(f"/game-vault/api/games/{gid}", json={
        "played_at": "2026-07-02", "turns": 8, "infinite_win": True,
        "participants": [
            {"player_id": a, "is_winner": False, "turn_order": 1, "deck_name": "Old Deck"},
            {"player_id": b, "is_winner": True, "turn_order": 2},
            {"player_id": c, "is_winner": False, "turn_order": 3},
        ],
    })
    assert r.status_code == 200, r.get_json()
    game = r.get_json()["game"]
    assert game["winner_name"] == "Bob"
    assert game["turns"] == 8 and game["infinite_win"] is True
    assert len(game["participants"]) == 3
    ann = next(p for p in game["participants"] if p["player_name"] == "Ann")
    assert ann["deck_name"] == "Old Deck"  # snapshot preserved through edit


def test_deck_mapping_by_commander(client, create_user, monkeypatch):
    user, password = create_user(email="gv-map@example.com", username="gvmap")
    _login(client, user.email, password)
    _patch_import(monkeypatch, _fake_deck(name="Atraxa Superfriends"))  # commander Atraxa
    pid = client.post("/game-vault/api/players", json={"name": "Sam"}).get_json()["player"]["id"]
    other = client.post("/game-vault/api/players", json={"name": "Kim"}).get_json()["player"]["id"]
    did = client.post(f"/game-vault/api/players/{pid}/decks",
                      json={"url": "https://archidekt.com/decks/42"}).get_json()["deck"]["id"]

    # Two games where Sam played a commander recorded by name only (no deck_id).
    for _ in range(2):
        client.post("/game-vault/api/games", json={
            "played_at": "2026-07-01",
            "participants": [
                {"player_id": pid, "is_winner": True,
                 "deck_name": "old atraxa list", "commander_name": "Atraxa, Praetors' Voice"},
                {"player_id": other, "is_winner": False},
            ],
        })

    overview = client.get("/game-vault/api/deck-map").get_json()
    sam = next(p for p in overview["players"] if p["name"] == "Sam")
    gc = next(g for g in sam["game_commanders"] if g["commander_name"] == "Atraxa, Praetors' Voice")
    assert gc["count"] == 2
    assert gc["suggested_deck_id"] == did  # same commander → confident suggestion

    r = client.post("/game-vault/api/deck-map", json={
        "mappings": [{"player_id": pid, "commander_name": "Atraxa, Praetors' Voice", "deck_id": did}],
    })
    assert r.status_code == 200
    assert r.get_json()["result"]["seats_updated"] == 2

    games = client.get("/game-vault/api/games").get_json()["games"]
    sam_seats = [p for g in games for p in g["participants"] if p["player_name"] == "Sam"]
    assert sam_seats and all(s["deck_id"] == did for s in sam_seats)
    assert all(s["commander_name"] == "Atraxa, Praetors' Voice" for s in sam_seats)


def test_metrics_endpoint(client, create_user):
    user, password = create_user(email="gv-metrics@example.com", username="gvmetrics")
    _login(client, user.email, password)
    a = client.post("/game-vault/api/players", json={"name": "Ann"}).get_json()["player"]["id"]
    b = client.post("/game-vault/api/players", json={"name": "Bob"}).get_json()["player"]["id"]
    client.post("/game-vault/api/games", json={
        "played_at": "2026-07-01", "win_condition": "combo", "turns": 8,
        "participants": [
            {"player_id": a, "is_winner": True, "turn_order": 1, "commander_name": "Atraxa"},
            {"player_id": b, "is_winner": False, "turn_order": 2, "commander_name": "Krenko"},
        ],
    })
    r = client.get("/game-vault/api/metrics?min_games=1")
    assert r.status_code == 200
    data = r.get_json()
    m = data["metrics"]
    assert m["summary"]["games"] == 1
    assert any(p["label"] == "Ann" and p["win_rate"] == 100.0 for p in m["players"])
    assert {"seat": 1, "label": "1st to play", "games": 1, "wins": 1, "win_rate": 100.0} in m["turn_order"]
    assert any(w["label"] == "combo" for w in m["win_conditions"])
    assert data["options"]["players"]  # filter options present

    # player filter narrows to games including that player
    r2 = client.get(f"/game-vault/api/metrics?player_id={a}")
    assert r2.get_json()["metrics"]["summary"]["games"] == 1


def test_state_endpoint(client, create_user):
    user, password = create_user(email="gv-state@example.com", username="gvstate")
    _login(client, user.email, password)
    data = client.get("/game-vault/api/state").get_json()
    assert set(data.keys()) == {"players", "games", "stats"}
