from datetime import datetime, timedelta, timezone

from models import EdhrecMetadata, Folder, db


def test_slug_candidates_for_target_dedupes_and_uses_front_face():
    from core.domains.decks.services.edhrec.edhrec_target_service import CommanderTarget, slug_candidates_for_target

    target = CommanderTarget(
        oracle_id="oid-1",
        name="Esika, God of the Tree // The Prismatic Bridge",
        slug_name="Esika, God of the Tree",
        slug_override="esika-god-of-the-tree",
    )

    assert slug_candidates_for_target(target) == [
        "esika-god-of-the-tree",
        "esika-god-of-the-tree-the-prismatic-bridge",
    ]


def test_prune_missing_slugs_discards_stale_rows():
    from core.domains.decks.services.edhrec.edhrec_target_service import prune_missing_slugs

    fresh = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    stale = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()

    missing = {
        "fresh-slug": {"oracle_id": "oid-fresh", "last_seen": fresh},
        "stale-slug": {"oracle_id": "oid-stale", "last_seen": stale},
    }

    assert prune_missing_slugs(missing, 30) == {
        "fresh-slug": {"oracle_id": "oid-fresh", "last_seen": fresh},
    }


def test_load_active_targets_collects_tags_and_fills_oracle_from_name(app, create_user, monkeypatch):
    from core.domains.decks.services.edhrec import edhrec_target_service

    user, _password = create_user(
        email="edhrec-targets@example.com",
        username="edhrec-targets",
    )

    with app.app_context():
        db.session.add_all(
            [
                Folder(
                    name="Deck A",
                    category=Folder.CATEGORY_DECK,
                    owner_user_id=user.id,
                    commander_name="Atraxa, Praetors' Voice",
                    commander_oracle_id=None,
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

        monkeypatch.setattr(edhrec_target_service.sc, "ensure_cache_loaded", lambda: True)
        monkeypatch.setattr(
            edhrec_target_service.sc,
            "unique_oracle_by_name",
            lambda name: "oid-atraxa" if "Atraxa" in name else None,
        )
        monkeypatch.setattr(
            edhrec_target_service.sc,
            "prints_for_oracle",
            lambda oracle_id: [{"name": "Atraxa, Praetors' Voice", "layout": "normal"}] if oracle_id == "oid-atraxa" else [],
        )
        monkeypatch.setattr(edhrec_target_service, "load_edhrec_index_slugs", lambda: {"oid-atraxa": {"slug": "atraxa-praetors-voice"}})

        targets, tag_map = edhrec_target_service.load_active_targets(use_index_slugs=True)

    assert [target.oracle_id for target in targets] == ["oid-atraxa"]
    assert targets[0].slug_override == "atraxa-praetors-voice"
    assert tag_map == {"oid-atraxa": {"Blink"}}


def test_load_missing_slugs_reads_metadata_row(app, db_session):
    from core.domains.decks.services.edhrec import edhrec_target_service

    with app.app_context():
        db.session.merge(
            EdhrecMetadata(
                key="missing_slugs",
                value='{"missing-target":{"oracle_id":"oid-123","last_seen":"2026-01-01T00:00:00+00:00"}}',
            )
        )
        db.session.commit()

        payload = edhrec_target_service.load_missing_slugs()

    assert payload == {
        "missing-target": {"oracle_id": "oid-123", "last_seen": "2026-01-01T00:00:00+00:00"}
    }
