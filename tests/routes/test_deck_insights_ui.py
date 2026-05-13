"""End-to-end smoke tests for the new UI wiring.

Covers:
    * the folder detail page includes the deck-insights panel when the folder
      is a deck (but not for collection folders)
    * the dashboard page embeds the collection value card
    * the proxy-PDF route returns a PDF payload
    * a selection of the new JSON endpoints respond with 200

These are deliberately shallow: the service-level tests already cover the
computations. The goal here is to catch routing / template regressions.
"""

from __future__ import annotations

import pytest

from extensions import db
from models import Folder, FolderRole, User
from tests.factories import create_card, create_folder


def _create_user(**overrides) -> tuple[User, str]:
    email = overrides.pop("email", "insights@example.com")
    password = overrides.pop("password", "password123!")
    user = User(email=email, username=overrides.pop("username", email.split("@")[0]))
    user.set_password(password)
    for key, value in overrides.items():
        setattr(user, key, value)
    db.session.add(user)
    db.session.commit()
    return user, password


def _login(client, email, password):
    response = client.post(
        "/login",
        data={"identifier": email, "password": password, "remember": "on"},
        follow_redirects=False,
    )
    assert response.status_code in (302, 303), response.data


def test_folder_detail_page_includes_deck_insights_panel(client, create_user):
    user, password = create_user(email="insights-owner@example.com")
    folder = Folder(
        name="Test Deck",
        category=Folder.CATEGORY_DECK,
        owner_user_id=user.id,
    )
    folder.role_entries = [FolderRole(role=FolderRole.ROLE_DECK)]
    db.session.add(folder)
    db.session.commit()
    card = create_card(folder=folder, name="Sol Ring", set_code="c20", collector_number="278")
    db.session.commit()

    _login(client, user.email, password)
    response = client.get(f"/folders/{folder.id}")
    assert response.status_code == 200, response.data[:500]
    body = response.data.decode("utf-8", errors="replace")

    # Panel container is present.
    assert "data-deck-insights" in body
    # Compare button is rendered.
    assert "data-insights-compare-open" in body
    # Proxy PDF download link is rendered.
    assert f"/folders/{folder.id}/proxy.pdf" in body


def test_collection_folder_does_not_show_deck_insights(client, create_user):
    user, password = create_user(email="collection-owner@example.com")
    folder = Folder(
        name="My Binder",
        category=Folder.CATEGORY_COLLECTION,
        owner_user_id=user.id,
    )
    folder.role_entries = [FolderRole(role=FolderRole.ROLE_COLLECTION)]
    db.session.add(folder)
    db.session.commit()
    create_card(folder=folder, name="Forest", set_code="znr", collector_number="269")
    db.session.commit()

    _login(client, user.email, password)
    response = client.get(f"/folders/{folder.id}")
    assert response.status_code == 200
    assert b"data-deck-insights" not in response.data


def test_dashboard_embeds_collection_value_card(client, create_user):
    user, password = create_user(email="dashboard-owner@example.com")
    _login(client, user.email, password)
    response = client.get("/dashboard")
    assert response.status_code == 200
    body = response.data.decode("utf-8", errors="replace")
    assert "data-collection-value" in body
    # Endpoints should be present in the markup.
    assert "/api/collection/value/snapshots" in body
    assert "/api/collection/value/history" in body


def test_proxy_pdf_returns_pdf_bytes(client, create_user):
    user, password = create_user(email="pdf-owner@example.com")
    folder = Folder(
        name="Proxy Pile",
        category=Folder.CATEGORY_DECK,
        owner_user_id=user.id,
    )
    folder.role_entries = [FolderRole(role=FolderRole.ROLE_DECK)]
    db.session.add(folder)
    db.session.commit()
    create_card(folder=folder, name="Lightning Bolt", set_code="m11", collector_number="146", quantity=4)
    db.session.commit()

    _login(client, user.email, password)
    response = client.get(f"/folders/{folder.id}/proxy.pdf")
    assert response.status_code == 200
    assert response.mimetype == "application/pdf"
    assert response.data.startswith(b"%PDF-1.4")


def test_legality_api_returns_report(client, create_user):
    user, password = create_user(email="legality-owner@example.com")
    folder = Folder(
        name="Commander Deck",
        category=Folder.CATEGORY_DECK,
        owner_user_id=user.id,
    )
    folder.role_entries = [FolderRole(role=FolderRole.ROLE_DECK)]
    db.session.add(folder)
    db.session.commit()

    _login(client, user.email, password)
    response = client.get(f"/api/folders/{folder.id}/legality?format=commander")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["data"]["format"]["key"] == "commander"
