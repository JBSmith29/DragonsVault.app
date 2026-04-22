from models import Card, Folder, FolderRole, db


def test_build_folder_detail_commander_context_builds_media_bracket_links_and_edhrec(
    app,
    create_user,
    monkeypatch,
):
    from core.domains.decks.services import folder_detail_commander_context_service

    user, _password = create_user(email="commander-context@example.com", username="commander_context")

    with app.app_context():
        folder = Folder(
            name="Commander Context",
            category=Folder.CATEGORY_DECK,
            owner_user_id=user.id,
            commander_name="Alpha Commander",
            commander_oracle_id="oid-alpha-commander",
            deck_tag="artifacts",
        )
        db.session.add(folder)
        db.session.flush()
        db.session.add(FolderRole(folder_id=folder.id, role=FolderRole.ROLE_DECK))
        db.session.add_all(
            [
                Card(
                    name="Sol Ring",
                    set_code="TST",
                    collector_number="1",
                    folder_id=folder.id,
                    quantity=1,
                    oracle_id="oid-sol-ring",
                    type_line="Artifact",
                    lang="en",
                ),
                Card(
                    name="Mystic Tutor (Promo)",
                    set_code="TST",
                    collector_number="2",
                    folder_id=folder.id,
                    quantity=1,
                    oracle_id="oid-mystic-tutor",
                    type_line="Instant",
                    lang="en",
                ),
            ]
        )
        db.session.commit()
        folder_id = folder.id

    monkeypatch.setattr(folder_detail_commander_context_service, "compute_bracket_signature", lambda *args, **kwargs: "sig")
    monkeypatch.setattr(folder_detail_commander_context_service, "get_cached_bracket", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        folder_detail_commander_context_service,
        "evaluate_commander_bracket",
        lambda *args, **kwargs: {"summary": "Bracket summary"},
    )
    monkeypatch.setattr(folder_detail_commander_context_service, "store_cached_bracket", lambda *args, **kwargs: None)
    monkeypatch.setattr(folder_detail_commander_context_service, "prints_for_oracle", lambda oracle_id: [])
    monkeypatch.setattr(
        folder_detail_commander_context_service,
        "commander_card_snapshot",
        lambda name, epoch: {
            "name": name,
            "thumb": "thumb.png",
            "hover": "hover.png",
            "set_name": "Snapshot Set",
        },
    )
    monkeypatch.setattr(folder_detail_commander_context_service, "edhrec_cache_ready", lambda: True)
    monkeypatch.setattr(folder_detail_commander_context_service, "resolve_deck_tag_from_slug", lambda value: "Artifacts")
    monkeypatch.setattr(folder_detail_commander_context_service, "unique_oracle_by_name", lambda value: "oid-alpha-commander")
    monkeypatch.setattr(
        folder_detail_commander_context_service,
        "build_recommendation_sections",
        lambda *args, **kwargs: [
            {
                "label": "High Synergy",
                "cards": [
                    {"name": "Sol Ring", "oracle_id": "oid-sol-ring"},
                    {"name": "Other Card", "oracle_id": "oid-other"},
                ],
            }
        ],
    )

    with app.app_context():
        folder = db.session.get(Folder, folder_id)
        deck_cards = Card.query.filter(Card.folder_id == folder_id).order_by(Card.id).all()
        context = folder_detail_commander_context_service.build_folder_detail_commander_context(
            folder,
            deck_cards=deck_cards,
            print_map={},
            bracket_cards=[{"name": "Sol Ring"}],
        )

    assert context["commander_bracket"] == {"summary": "Bracket summary"}
    assert context["commander_media"]["image"] == "thumb.png"
    assert context["commander_media_list"][0]["label"] == "Snapshot Set"
    assert context["bracket_card_links"]["sol ring"] == deck_cards[0].id
    assert context["bracket_card_links"]["mystic tutor"] == deck_cards[1].id
    assert context["edhrec_tag_label"] == "Artifacts"
    assert context["edhrec_commander_ready"] is True
    assert context["edhrec_ready"] is True
    assert context["is_deck_folder"] is True
    assert context["edhrec_sections"][0]["cards"][0]["in_deck"] is True
    assert context["edhrec_sections"][0]["cards"][1]["in_deck"] is False
