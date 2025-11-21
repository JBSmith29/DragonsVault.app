from models import User


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
        refreshed = User.query.get(user.id)
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
        refreshed = User.query.get(user.id)
        assert refreshed.display_name is None
