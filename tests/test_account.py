from extensions import db
from models import User
from core.shared.utils.time import utcnow


def _login(client, identifier, password):
    return client.post(
        "/login",
        data={"identifier": identifier, "password": password},
        follow_redirects=True,
    )


def test_account_center_for_regular_user(client, create_user):
    user, password = create_user(email="settings@example.com")
    _login(client, user.email, password)

    resp = client.get("/account/center")
    assert resp.status_code == 200
    assert b"Settings" in resp.data
    assert b"Admin tools" not in resp.data


def test_account_center_for_admin_shows_admin_tools(client, create_user):
    user, password = create_user(email="admin-settings@example.com", username="setting_admin", is_admin=True)
    _login(client, user.email, password)

    resp = client.get("/account/center")
    assert resp.status_code == 200
    assert b"Admin Center" in resp.data
    assert b"Admin tools" in resp.data
    assert b"Open admin dashboard" in resp.data


def test_user_can_update_password(client, create_user):
    user, original_password = create_user(email="member@example.com")
    login_resp = _login(client, user.email, original_password)
    assert login_resp.status_code == 200

    new_password = "n3w-password!"
    resp = client.post(
        "/account/api-token",
        data={
            "action": "update_password",
            "current_password": original_password,
            "new_password": new_password,
            "confirm_password": new_password,
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Password updated successfully." in resp.data

    client.get("/logout", follow_redirects=True)

    reauth = _login(client, user.email, new_password)
    assert reauth.status_code == 200
    assert b"Invalid email/username or password." not in reauth.data


def test_update_password_requires_correct_current_password(client, create_user):
    user, original_password = create_user(email="member2@example.com")
    _login(client, user.email, original_password)

    resp = client.post(
        "/account/api-token",
        data={
            "action": "update_password",
            "current_password": "wrong-password",
            "new_password": "another-new",
            "confirm_password": "another-new",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Current password is incorrect." in resp.data

    client.get("/logout", follow_redirects=True)

    reauth = _login(client, user.email, original_password)
    assert reauth.status_code == 200


def test_update_password_enforces_minimum_length(client, create_user):
    user, original_password = create_user(email="member3@example.com")
    _login(client, user.email, original_password)

    resp = client.post(
        "/account/api-token",
        data={
            "action": "update_password",
            "current_password": original_password,
            "new_password": "short",
            "confirm_password": "short",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"New password must be at least" in resp.data


def test_user_can_update_display_name(client, create_user):
    user, password = create_user(email="profile@example.com", username="profileuser", display_name=None)
    _login(client, user.email, password)

    resp = client.post(
        "/account/center",
        data={
            "action": "update_display_name",
            "display_name": "Commander Guru",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Display name updated." in resp.data

    with client.application.app_context():
        refreshed = db.session.get(User, user.id)
        assert refreshed.display_name == "Commander Guru"


def test_display_name_rejects_overly_long_value(client, create_user):
    user, password = create_user(email="profile2@example.com", username="profileuser2")
    _login(client, user.email, password)
    too_long = "x" * 200

    resp = client.post(
        "/account/center",
        data={
            "action": "update_display_name",
            "display_name": too_long,
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Display name must be" in resp.data

    with client.application.app_context():
        refreshed = db.session.get(User, user.id)
        assert refreshed.display_name is None


def test_archived_user_cannot_login(client, create_user):
    user, password = create_user(email="archived-login@example.com", username="archivedlogin")
    persisted = db.session.get(User, user.id)
    assert persisted is not None
    persisted.archived_at = utcnow()
    db.session.commit()

    resp = client.post(
        "/login",
        data={"identifier": user.email, "password": password},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Invalid email/username or password." in resp.data


def test_archived_user_bearer_token_stops_working(client, create_user):
    user, _password = create_user(email="archived-token@example.com", username="archivedtoken")
    persisted = db.session.get(User, user.id)
    assert persisted is not None
    token = persisted.issue_api_token()
    db.session.commit()

    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    active_resp = client.get("/api/me", headers=headers)
    assert active_resp.status_code == 200

    persisted = db.session.get(User, user.id)
    assert persisted is not None
    persisted.archived_at = utcnow()
    db.session.commit()
    assert User.verify_api_token(token) is None

    archived_resp = client.get("/api/me", headers=headers, follow_redirects=False)
    assert archived_resp.status_code == 401
    assert archived_resp.get_json() == {"error": "authentication_required"}
    assert archived_resp.headers.get("WWW-Authenticate") == "Bearer"


def test_api_requires_authentication_returns_401_json(client):
    resp = client.get("/api/me", headers={"Accept": "application/json"}, follow_redirects=False)
    assert resp.status_code == 401
    assert resp.get_json() == {"error": "authentication_required"}
    assert resp.headers.get("WWW-Authenticate") == "Bearer"
