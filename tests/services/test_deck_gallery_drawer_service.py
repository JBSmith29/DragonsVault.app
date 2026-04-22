from models import Card, Folder, FolderRole, db


def test_build_deck_drawer_summary_uses_print_fallback_and_tag_groups(app, create_user, monkeypatch):
    from core.domains.decks.services import deck_gallery_drawer_service, deck_gallery_service

    user, _password = create_user(
        email="deck-drawer@example.com",
        username="deck-drawer",
    )

    with app.app_context():
        folder = Folder(
            name="Drawer Deck",
            category=Folder.CATEGORY_DECK,
            owner_user_id=user.id,
            deck_tag="Spells",
        )
        db.session.add(folder)
        db.session.flush()
        db.session.add(FolderRole(folder_id=folder.id, role=FolderRole.ROLE_DECK))
        db.session.add(
            Card(
                name="Fallback Card",
                set_code="TST",
                collector_number="7",
                folder_id=folder.id,
                quantity=2,
                lang="en",
                type_line=None,
                oracle_text=None,
                mana_value=None,
            )
        )
        db.session.commit()
        folder_id = folder.id

        monkeypatch.setattr(deck_gallery_service, "_ensure_cache_ready", lambda: True)
        monkeypatch.setattr(
            deck_gallery_service,
            "_lookup_print_data",
            lambda *args, **kwargs: {
                "type_line": "Creature - Elf Wizard",
                "oracle_text": "Whenever this attacks, draw a card.",
                "cmc": 3,
                "mana_cost": "{2}{G}",
            },
        )
        monkeypatch.setattr(deck_gallery_service, "deck_mana_pip_dist", lambda *args, **kwargs: [{"color": "G", "count": 2}])
        monkeypatch.setattr(
            deck_gallery_service,
            "deck_land_mana_sources",
            lambda *args, **kwargs: [("G", "icon-g", 1)],
        )
        monkeypatch.setattr(deck_gallery_service, "deck_curve_rows", lambda *args, **kwargs: [{"mv": 3, "count": 2}])
        monkeypatch.setattr(deck_gallery_service, "compute_folder_color_identity", lambda *args, **kwargs: (["G"], "Green"))
        monkeypatch.setattr(deck_gallery_service, "get_deck_tag_groups", lambda: {"Theme": ["Spells"]})
        monkeypatch.setattr(deck_gallery_service, "get_deck_tag_category", lambda value: "Theme" if value == "Spells" else None)
        monkeypatch.setattr(
            deck_gallery_service,
            "evaluate_commander_bracket",
            lambda *args, **kwargs: {"level": 2, "label": "Tuned", "score": 5, "summary_points": ["Fast mana"]},
        )
        monkeypatch.setattr(deck_gallery_service, "get_cached_bracket", lambda *args, **kwargs: None)
        monkeypatch.setattr(deck_gallery_service, "store_cached_bracket", lambda *args, **kwargs: None)

        folder = db.session.get(Folder, folder_id)
        payload = deck_gallery_drawer_service.build_deck_drawer_summary(folder, hooks=deck_gallery_service)

    assert payload["deck"]["name"] == "Drawer Deck"
    assert payload["deck"]["tag_label"] == "Theme: Spells"
    assert payload["deck"]["tag_category"] == "Theme"
    assert payload["type_breakdown"] == [("Creature", 2)]
    assert payload["total_cards"] == 2
    assert payload["deck_colors"] == ["G"]
    assert payload["land_mana_sources"] == [{"color": "G", "icon": "icon-g", "label": "G", "count": 1}]
    assert payload["bracket"]["label"] == "Tuned"
