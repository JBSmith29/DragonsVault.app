from flask_login import login_user

from models import User, db


def test_parse_collection_browser_request_normalizes_filters(app, create_user):
    from core.domains.cards.services import collection_request_service

    user, _password = create_user(
        email="collection-request@example.com",
        username="collection-request",
    )

    with app.app_context():
        user = db.session.get(User, user.id)
        with app.test_request_context(
            "/cards?folder=12&roles=mana_ramp,card_draw&roles=interaction"
            "&subroles=tempo&subroles=value&type_mode=exact&type=creature&type=wizard"
            "&color=u&color=c&scope=collection&show_friends=1&sort=qty&dir=desc&foil=1&per=999&page=0"
        ):
            login_user(user)
            params = collection_request_service.parse_collection_browser_request()

    assert params.folder_arg == "12"
    assert params.folder_id_int == 12
    assert params.role_list == ["mana_ramp", "card_draw", "interaction"]
    assert params.subrole_list == ["tempo", "value"]
    assert params.selected_types == ["creature", "wizard"]
    assert params.selected_colors == ["u", "c"]
    assert params.collection_flag is True
    assert params.show_friends is True
    assert params.sort == "qty"
    assert params.direction == "desc"
    assert params.reverse is True
    assert params.foil_only is True
    assert params.per == 25
    assert params.page == 1

