"""Integration test: the pod management page embeds the Playgroup Stats button."""

from __future__ import annotations

from extensions import db
from models import GamePod


def _login(client, email, password):
    response = client.post(
        "/login",
        data={"identifier": email, "password": password},
        follow_redirects=False,
    )
    assert response.status_code in (302, 303), response.data


def test_players_page_renders_playgroup_stats_button(client, create_user):
    user, password = create_user(email="pod-owner@example.com", username="pod_owner")
    _login(client, user.email, password)

    pod = GamePod(owner_user_id=user.id, name="Friday Night Pod")
    db.session.add(pod)
    db.session.commit()

    response = client.get("/games/players")
    assert response.status_code == 200
    body = response.data.decode("utf-8", errors="replace")

    # Playgroup stats toggle button is present for owned pods
    assert "data-pod-stats-toggle" in body
    assert f'data-pod-id="{pod.id}"' in body
    assert f"/api/games/pods/{pod.id}/playgroup-stats" in body
