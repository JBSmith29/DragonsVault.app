from flask_login import login_user

from models import Card, Folder, FolderRole, User, db


def test_build_decks_overview_context_builds_viewmodels_and_wizard_payload(app, create_user, monkeypatch):
    from core.domains.decks.services import deck_gallery_overview_service, deck_gallery_service

    user, _password = create_user(
        email="deck-overview@example.com",
        username="deck-overview",
        display_name="Deck Owner",
    )

    with app.app_context():
        folder = Folder(
            name="Proxy Deck",
            category=Folder.CATEGORY_DECK,
            owner_user_id=user.id,
            commander_name="Offline Commander",
            is_proxy=True,
        )
        db.session.add(folder)
        db.session.flush()
        db.session.add(FolderRole(folder_id=folder.id, role=FolderRole.ROLE_DECK))
        db.session.add(
            Card(
                name="Forest",
                set_code="LTR",
                collector_number="278",
                folder_id=folder.id,
                quantity=3,
                lang="en",
                type_line="Basic Land - Forest",
                oracle_text="{T}: Add {G}.",
                mana_value=0,
            )
        )
        db.session.commit()

        user = db.session.get(User, user.id)

        monkeypatch.setattr(deck_gallery_service, "cache_fetch", lambda _key, _ttl, fn: fn())
        monkeypatch.setattr(deck_gallery_service, "_ensure_cache_ready", lambda: True)
        monkeypatch.setattr(deck_gallery_service, "evaluate_commander_bracket", lambda *args, **kwargs: {"level": 1, "label": "Casual"})
        monkeypatch.setattr(deck_gallery_service, "get_cached_bracket", lambda *args, **kwargs: None)
        monkeypatch.setattr(deck_gallery_service, "store_cached_bracket", lambda *args, **kwargs: None)
        monkeypatch.setattr(deck_gallery_service, "ensure_symbols_cache", lambda force=False: None)
        monkeypatch.setattr(deck_gallery_service.sc, "cache_ready", lambda: True)
        monkeypatch.setattr(deck_gallery_service, "render_mana_html", lambda mana_str, use_local=False: f"rendered:{mana_str}")
        monkeypatch.setattr(deck_gallery_service, "compute_folder_color_identity", lambda *args, **kwargs: (["G"], "Green"))
        monkeypatch.setattr(
            deck_gallery_service,
            "_commander_thumbnail_payload",
            lambda *args, **kwargs: {
                "name": "Offline Commander",
                "small": "/thumb-small.png",
                "large": "/thumb-large.png",
                "alt": "Offline Commander",
            },
        )
        monkeypatch.setattr(deck_gallery_service, "get_deck_tag_groups", lambda: {"Theme": ["Spells"]})
        monkeypatch.setattr(
            deck_gallery_service,
            "build_deck_metadata_wizard_payload",
            lambda folders, tag_groups: {"folder_count": len(folders), "group_count": len(tag_groups)},
        )
        monkeypatch.setattr(deck_gallery_service, "find_by_set_cn", lambda *args, **kwargs: None)
        monkeypatch.setattr(deck_gallery_service, "url_for", lambda endpoint, **kwargs: f"/decks?page={kwargs.get('page', 1)}")

        with app.test_request_context("/decks"):
            login_user(user)
            context = deck_gallery_overview_service.build_decks_overview_context(hooks=deck_gallery_service)

    assert context["total_decks"] == 1
    assert context["proxy_total"] == 1
    assert context["owned_total"] == 0
    assert context["scope"] == "mine"
    assert context["deck_metadata_wizard"] == {"folder_count": 1, "group_count": 1}
    assert context["page_url_map"] == {1: "/decks?page=1"}
    assert context["owner_names"] == []
    assert len(context["decks"]) == 1

    deck = context["decks"][0]
    assert deck.name == "Proxy Deck"
    assert deck.qty == 3
    assert deck.is_proxy is True
    assert deck.is_owner is True
    assert deck.ci_name == "Green"
    assert deck.ci_letters == "G"
    assert deck.ci_html == "rendered:{G}"
    assert deck.bracket_label == "Casual"
    assert deck.commander is not None
    assert deck.commander.name == "Offline Commander"
    assert deck.commander.small == "/thumb-small.png"

    owner_summary = context["owner_summary"][0]
    assert owner_summary.label == "Deck Owner"
    assert owner_summary.deck_count == 1
    assert owner_summary.proxy_count == 1
