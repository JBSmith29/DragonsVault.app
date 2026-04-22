from flask_login import login_user

from models import Folder, FolderRole, GamePod, GamePodMember, GameRosterDeck, GameRosterPlayer, User, db


def test_build_games_players_page_context_scopes_deck_options_to_current_owner(app, create_user):
    from core.domains.games.services import game_players_payload_service

    owner, _owner_password = create_user(
        email="games-payload-owner@example.com",
        username="games-payload-owner",
        display_name="Owner Player",
    )
    other_user, _other_password = create_user(
        email="games-payload-other@example.com",
        username="games-payload-other",
    )

    with app.app_context():
        own_deck = Folder(
            name="Owner Deck",
            category=Folder.CATEGORY_DECK,
            owner_user_id=owner.id,
        )
        other_deck = Folder(
            name="Other Deck",
            category=Folder.CATEGORY_DECK,
            owner_user_id=other_user.id,
        )
        roster_player = GameRosterPlayer(
            owner_user_id=owner.id,
            display_name="Guest One",
        )
        pod = GamePod(owner_user_id=owner.id, name="Friday Pod")
        db.session.add_all([own_deck, other_deck, roster_player, pod])
        db.session.flush()
        db.session.add_all(
            [
                FolderRole(folder_id=own_deck.id, role=FolderRole.ROLE_DECK),
                FolderRole(folder_id=other_deck.id, role=FolderRole.ROLE_DECK),
                GameRosterDeck(
                    roster_player_id=roster_player.id,
                    owner_user_id=owner.id,
                    folder_id=own_deck.id,
                ),
                GamePodMember(pod_id=pod.id, roster_player_id=roster_player.id),
            ]
        )
        db.session.commit()

        owner = db.session.get(User, owner.id)
        pod = db.session.get(GamePod, pod.id)

        with app.test_request_context("/games/players"):
            login_user(owner)
            context = game_players_payload_service.build_games_players_page_context([pod])

    assert context["current_owner_id"] == owner.id
    assert context["has_roster_players"] is True
    assert len(context["roster_groups"]) == 1
    assert len(context["pods"]) == 1

    roster_group = context["roster_groups"][0]
    assert roster_group["owner_user_id"] == owner.id
    assert [deck["label"] for deck in roster_group["deck_options"]] == ["Owner Deck"]
    assert roster_group["players"][0]["label"] == "Guest One"
    assert roster_group["players"][0]["deck_assignments"][0]["label"] == "Owner Deck"

    pod_payload = context["pods"][0]
    assert pod_payload["name"] == "Friday Pod"
    assert [player["label"] for player in pod_payload["roster_options"]] == ["Guest One"]
    assert [member["label"] for member in pod_payload["members"]] == ["Guest One"]
