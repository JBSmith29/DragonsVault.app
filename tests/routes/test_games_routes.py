from extensions import db
from models import Folder, FolderRole, GamePod, GameRosterDeck, GameRosterPlayer, GameSession


def _login(client, identifier, password):
    return client.post(
        "/login",
        data={"identifier": identifier, "password": password},
        follow_redirects=True,
    )


def _create_game_session(app, owner_user_id, *, notes="Test game"):
    with app.app_context():
        session = GameSession(owner_user_id=owner_user_id, notes=notes)
        db.session.add(session)
        db.session.commit()
        return session.id


def test_games_landing_loads(client, create_user):
    user, password = create_user(email="games@example.com")
    _login(client, user.email, password)

    response = client.get("/games")
    assert response.status_code == 200


def test_games_dashboard_loads(client, create_user):
    user, password = create_user(email="games-dashboard@example.com")
    _login(client, user.email, password)

    response = client.get("/games/dashboard")
    assert response.status_code == 200


def test_games_logs_loads(client, create_user):
    user, password = create_user(email="games-logs@example.com")
    _login(client, user.email, password)

    response = client.get("/games/logs")
    assert response.status_code == 200


def test_games_admin_loads_for_admin(client, create_user):
    user, password = create_user(email="games-admin@example.com", username="gamesadmin", is_admin=True)
    _login(client, user.email, password)

    response = client.get("/games/admin")
    assert response.status_code == 200


def test_games_import_template_downloads(client, create_user):
    user, password = create_user(email="games-template@example.com")
    _login(client, user.email, password)

    response = client.get("/games/import-template")
    assert response.status_code == 200
    assert response.mimetype == "text/csv"


def test_games_metrics_loads(client, create_user):
    user, password = create_user(email="games-metrics@example.com")
    _login(client, user.email, password)

    response = client.get("/games/metrics")
    assert response.status_code == 200


def test_games_metrics_users_loads(client, create_user):
    user, password = create_user(email="games-metrics-users@example.com")
    _login(client, user.email, password)

    response = client.get("/games/metrics/users")
    assert response.status_code == 200


def test_games_metrics_decks_loads(client, create_user):
    user, password = create_user(email="games-metrics-decks@example.com")
    _login(client, user.email, password)

    response = client.get("/games/metrics/decks")
    assert response.status_code == 200


def test_games_players_loads(client, create_user):
    user, password = create_user(email="games-players@example.com")
    _login(client, user.email, password)

    response = client.get("/games/players")
    assert response.status_code == 200


def test_games_players_create_pod(client, create_user, app):
    user, password = create_user(email="games-pod@example.com")
    _login(client, user.email, password)

    response = client.post(
        "/games/players",
        data={"action": "create_pod", "pod_name": "Weekly Pod"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    with app.app_context():
        pod = GamePod.query.filter_by(owner_user_id=user.id, name="Weekly Pod").first()
        assert pod is not None


def test_games_players_add_guest_player(client, create_user, app):
    user, password = create_user(email="games-player-guest@example.com")
    _login(client, user.email, password)

    response = client.post(
        "/games/players",
        data={
            "action": "add_player",
            "player_kind": "guest",
            "display_name": "Guest One",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    with app.app_context():
        player = GameRosterPlayer.query.filter_by(owner_user_id=user.id, display_name="Guest One").first()
        assert player is not None


def test_games_players_assign_manual_deck(client, create_user, app):
    user, password = create_user(email="games-manual-deck@example.com")
    with app.app_context():
        roster_player = GameRosterPlayer(owner_user_id=user.id, display_name="Manual Pilot")
        db.session.add(roster_player)
        db.session.commit()
        roster_player_id = roster_player.id

    _login(client, user.email, password)
    response = client.post(
        "/games/players",
        data={
            "action": "assign_deck",
            "roster_player_id": str(roster_player_id),
            "manual_deck_name": "Borrowed Deck",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    with app.app_context():
        assignment = GameRosterDeck.query.filter_by(roster_player_id=roster_player_id, deck_name="Borrowed Deck").first()
        assert assignment is not None
        assert assignment.folder_id is None


def test_games_players_rejects_foreign_deck_assignment(client, create_user, app):
    user, password = create_user(email="games-assign-owner@example.com", username="games-assign-owner")
    other_user, _other_password = create_user(email="games-assign-other@example.com", username="games-assign-other")

    with app.app_context():
        roster_player = GameRosterPlayer(owner_user_id=user.id, display_name="Assigned Guest")
        foreign_deck = Folder(
            name="Other Deck",
            category=Folder.CATEGORY_DECK,
            owner_user_id=other_user.id,
        )
        db.session.add_all([roster_player, foreign_deck])
        db.session.flush()
        db.session.add(FolderRole(folder_id=foreign_deck.id, role=FolderRole.ROLE_DECK))
        db.session.commit()
        roster_player_id = roster_player.id
        foreign_deck_id = foreign_deck.id

    _login(client, user.email, password)
    response = client.post(
        "/games/players",
        data={
            "action": "assign_deck",
            "roster_player_id": str(roster_player_id),
            "deck_id": str(foreign_deck_id),
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    with app.app_context():
        assignment = GameRosterDeck.query.filter_by(
            roster_player_id=roster_player_id,
            folder_id=foreign_deck_id,
        ).first()
        assert assignment is None


def test_games_public_dashboard_loads(client):
    response = client.get("/gamedashboard")
    assert response.status_code == 200


def test_games_public_dashboard_deck_metrics_loads(client):
    response = client.get("/gamedashboard?metric=decks")
    assert response.status_code == 200


def test_games_public_dashboard_logs_loads(client):
    response = client.get("/gamedashboard?metric=logs")
    assert response.status_code == 200


def test_games_new_form_loads(client, create_user):
    user, password = create_user(email="games-new@example.com")
    _login(client, user.email, password)

    response = client.get("/games/new")
    assert response.status_code == 200


def test_games_detail_loads_for_owner(client, create_user, app):
    user, password = create_user(email="games-detail@example.com")
    game_id = _create_game_session(app, user.id, notes="Owned game")
    _login(client, user.email, password)

    response = client.get(f"/games/{game_id}")
    assert response.status_code == 200


def test_games_edit_form_loads_for_owner(client, create_user, app):
    user, password = create_user(email="games-edit@example.com")
    game_id = _create_game_session(app, user.id, notes="Editable game")
    _login(client, user.email, password)

    response = client.get(f"/games/{game_id}/edit")
    assert response.status_code == 200


def test_games_delete_removes_owned_game(client, create_user, app):
    user, password = create_user(email="games-delete@example.com")
    game_id = _create_game_session(app, user.id, notes="Delete me")
    _login(client, user.email, password)

    response = client.post(f"/games/{game_id}/delete", follow_redirects=True)
    assert response.status_code == 200
    with app.app_context():
        assert db.session.get(GameSession, game_id) is None


def test_games_bulk_delete_removes_owned_games(client, create_user, app):
    user, password = create_user(email="games-bulk-delete@example.com")
    game_id_1 = _create_game_session(app, user.id, notes="Bulk one")
    game_id_2 = _create_game_session(app, user.id, notes="Bulk two")
    _login(client, user.email, password)

    response = client.post(
        "/games/bulk-delete",
        data={"game_ids": f"{game_id_1},{game_id_2}"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    with app.app_context():
        assert db.session.get(GameSession, game_id_1) is None
        assert db.session.get(GameSession, game_id_2) is None
