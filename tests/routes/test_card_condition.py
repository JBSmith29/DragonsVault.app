"""Integration tests for the card-condition PATCH endpoint."""

from __future__ import annotations

from extensions import db
from models import Card, Folder, FolderRole


def _login(client, email, password):
    response = client.post(
        "/login",
        data={"identifier": email, "password": password},
        follow_redirects=False,
    )
    assert response.status_code in (302, 303), response.data


def _create_owned_card(user):
    folder = Folder(
        name="Condition Test",
        category=Folder.CATEGORY_COLLECTION,
        owner_user_id=user.id,
    )
    folder.role_entries = [FolderRole(role=FolderRole.ROLE_COLLECTION)]
    db.session.add(folder)
    db.session.flush()
    card = Card(
        name="Sol Ring",
        set_code="c20",
        collector_number="278",
        folder_id=folder.id,
        quantity=1,
    )
    db.session.add(card)
    db.session.commit()
    return card


def test_update_card_condition_sets_grade(client, create_user):
    user, password = create_user(email="cond-set@example.com", username="cond_set")
    card = _create_owned_card(user)
    _login(client, user.email, password)

    response = client.post(
        f"/api/card/{card.id}/condition",
        json={"condition": "Near Mint"},
    )
    assert response.status_code == 200, response.data
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["condition"] == "NM"
    assert payload["condition_label"] == "Near Mint"

    fresh = db.session.get(Card, card.id)
    assert fresh.condition == "NM"


def test_update_card_condition_clears_with_null(client, create_user):
    user, password = create_user(email="cond-clear@example.com", username="cond_clear")
    card = _create_owned_card(user)
    card.condition = "LP"
    db.session.commit()
    _login(client, user.email, password)

    response = client.post(
        f"/api/card/{card.id}/condition",
        json={"condition": None},
    )
    assert response.status_code == 200
    assert response.get_json()["condition"] is None
    assert db.session.get(Card, card.id).condition is None


def test_update_card_condition_rejects_invalid_grade(client, create_user):
    user, password = create_user(email="cond-bad@example.com", username="cond_bad")
    card = _create_owned_card(user)
    _login(client, user.email, password)

    response = client.post(
        f"/api/card/{card.id}/condition",
        json={"condition": "perfect"},
    )
    assert response.status_code == 400
    body = response.get_json()
    assert body["success"] is False
    assert "valid_grades" in body


def test_card_detail_page_renders_condition_editor(client, create_user):
    user, password = create_user(email="cond-ui@example.com", username="cond_ui")
    card = _create_owned_card(user)
    _login(client, user.email, password)

    response = client.get(f"/cards/{card.id}")
    assert response.status_code == 200
    body = response.data.decode("utf-8", errors="replace")
    assert "data-card-condition" in body
    assert f"/api/card/{card.id}/condition" in body
