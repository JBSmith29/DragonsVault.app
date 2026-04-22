def _login_admin(client, create_user):
    user, password = create_user(email="admin@example.com", username="admin", is_admin=True)
    client.post("/login", data={"identifier": user.email, "password": password}, follow_redirects=True)


def test_admin_page_loads(client, create_user):
    _login_admin(client, create_user)
    response = client.get("/admin")
    assert response.status_code == 200

def test_refresh_rulings_post(client, create_user):
    _login_admin(client, create_user)
    response = client.post("/admin", data={"action": "refresh_rulings"})
    assert response.status_code in (200, 302)

def test_clear_cache_post(client, create_user):
    _login_admin(client, create_user)
    response = client.post("/admin", data={"action": "clear_cache"})
    assert response.status_code in (200, 302)


def test_admin_data_operations_page_loads(client, create_user):
    _login_admin(client, create_user)
    response = client.get("/admin/data-operations")
    assert response.status_code == 200


def test_admin_folder_categories_page_loads(client, create_user):
    _login_admin(client, create_user)
    response = client.get("/admin/folder-categories")
    assert response.status_code == 200


def test_manage_folder_preferences_page_loads(client, create_user):
    user, password = create_user(email="folders@example.com", username="folders")
    client.post("/login", data={"identifier": user.email, "password": password}, follow_redirects=True)
    response = client.get("/account/folders")
    assert response.status_code == 200


def test_admin_card_roles_page_loads(client, create_user):
    _login_admin(client, create_user)
    response = client.get("/admin/card-roles")
    assert response.status_code == 200


def test_admin_oracle_tags_page_loads(client, create_user):
    _login_admin(client, create_user)
    response = client.get("/admin/oracle-tags")
    assert response.status_code == 200


def test_admin_oracle_core_roles_page_loads(client, create_user):
    _login_admin(client, create_user)
    response = client.get("/admin/oracle-tags/core-roles")
    assert response.status_code == 200


def test_admin_oracle_deck_tags_page_loads(client, create_user):
    _login_admin(client, create_user)
    response = client.get("/admin/oracle-tags/deck-tags")
    assert response.status_code == 200


def test_admin_game_deck_mapping_empty_state_loads(client, create_user):
    _login_admin(client, create_user)
    response = client.get("/admin/game-deck-mapping")
    assert response.status_code == 200


def test_admin_requests_page_loads(client, create_user):
    _login_admin(client, create_user)
    response = client.get("/admin/requests")
    assert response.status_code == 200


def test_admin_manage_users_page_loads(client, create_user):
    _login_admin(client, create_user)
    response = client.get("/admin/users")
    assert response.status_code == 200


def test_admin_job_status_returns_json(client, create_user):
    _login_admin(client, create_user)
    response = client.get("/admin/job-status?scope=scryfall")
    assert response.status_code == 200
    assert response.is_json
    assert "events" in response.get_json()


def test_legacy_imports_ws_returns_notice(client, create_user):
    _login_admin(client, create_user)
    response = client.get("/ws/imports")
    assert response.status_code == 410
    assert response.is_json
    assert "error" in response.get_json()


def test_create_user_via_admin(client, create_user, app):
    from models import User

    _login_admin(client, create_user)
    resp = client.post(
        "/admin",
        data={
            "action": "create_user",
            "user_username": "new_user",
            "user_email": "new_user@example.com",
            "user_password": "hunter2",
            "user_display_name": "New User",
            "user_is_admin": "1",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        new_user = User.query.filter_by(email="new_user@example.com").first()
        assert new_user is not None
        assert new_user.is_admin
        assert new_user.username == "new_user"
