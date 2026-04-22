from models import DeckTag, Folder, db


def test_collect_edhrec_targets_fills_name_from_oracle(app, create_user, monkeypatch):
    from core.domains.decks.services import edhrec_cache_target_service as target_service

    user, _password = create_user(
        email="edhrec-cache-targets@example.com",
        username="edhrec-cache-targets",
    )

    with app.app_context():
        db.session.add_all(
            [
                Folder(
                    name="Deck A",
                    category=Folder.CATEGORY_DECK,
                    owner_user_id=user.id,
                    commander_name=None,
                    commander_oracle_id="oid-atraxa",
                    deck_tag="Blink",
                ),
                Folder(
                    name="Deck B",
                    category=Folder.CATEGORY_DECK,
                    owner_user_id=user.id,
                    commander_name="Atraxa, Praetors' Voice",
                    commander_oracle_id="oid-atraxa",
                ),
            ]
        )
        db.session.commit()

        monkeypatch.setattr(target_service.sc, "ensure_cache_loaded", lambda: True)
        monkeypatch.setattr(
            target_service.sc,
            "prints_for_oracle",
            lambda oracle_id: [{"name": "Atraxa, Praetors' Voice"}] if oracle_id == "oid-atraxa" else [],
        )

        payload = target_service.collect_edhrec_targets()

    assert payload["deck_total"] == 2
    assert payload["with_commander"] == 2
    assert payload["with_tag"] == 1
    assert payload["commanders"] == [{"oracle_id": "oid-atraxa", "name": "Atraxa, Praetors' Voice"}]
    assert payload["tags"] == ["Blink"]


def test_collect_edhrec_index_targets_dedupes_theme_names(monkeypatch):
    from core.domains.decks.services import edhrec_cache_target_service as target_service

    monkeypatch.setattr(
        target_service,
        "edhrec_index",
        lambda include_commanders, include_themes: {
            "commanders": [{"name": "Atraxa, Praetors' Voice"}],
            "themes": [{"name": "Blink"}, {"name": "blink"}, {"name": "Sacrifice"}],
        },
    )

    payload = target_service.collect_edhrec_index_targets(include_themes=True)

    assert payload["commanders_total"] == 1
    assert payload["tags"] == ["Blink", "Sacrifice"]
    assert payload["tags_total"] == 2


def test_extract_commander_tag_entries_and_upsert_index_tags(app, db_session, monkeypatch):
    from core.domains.decks.services import edhrec_cache_target_service as target_service

    monkeypatch.setattr(
        target_service,
        "resolve_deck_tag_from_slug",
        lambda value: {"blink": "Blink", "artifacts": "Artifacts"}.get(str(value).strip().lower()),
    )

    entries = target_service.extract_commander_tag_entries(
        {
            "theme_options": [
                {"slug": "blink", "label": "Blink"},
                {"slug": "", "label": "Artifacts"},
                {"slug": "blink", "label": "Blink"},
            ]
        }
    )

    with app.app_context():
        inserted = target_service.upsert_index_tags(["Blink", "Artifacts", "Blink"])
        names = [row.name for row in DeckTag.query.order_by(DeckTag.name.asc()).all()]

    assert entries == [
        {"tag": "Blink", "slug": "blink"},
        {"tag": "Artifacts", "slug": "artifacts"},
    ]
    assert inserted == 2
    assert names == ["Artifacts", "Blink"]
