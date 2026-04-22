from models import (
    EdhrecCommanderCard,
    EdhrecCommanderCategoryCard,
    EdhrecCommanderTag,
    EdhrecCommanderTagCard,
    EdhrecCommanderTagCategoryCard,
    EdhrecCommanderTypeDistribution,
    EdhrecMetadata,
    EdhrecTagCommander,
    db,
)


def test_persist_monthly_commander_rows_and_finalize(app, db_session):
    from core.domains.decks.services.edhrec import edhrec_ingestion_persistence_service as persistence

    with app.app_context():
        persistence.ensure_schema()
        persistence.persist_monthly_commander_rows(
            "oid-cmdr",
            needs_commander=True,
            synergy_rows=[{"card_oracle_id": "oid-card-1", "synergy_rank": 1, "synergy_score": 0.3}],
            category_rows=[{"category": "Ramp", "card_oracle_id": "oid-card-2", "category_rank": 1}],
            tags=["Blink", "Control"],
            commander_type_rows=[{"card_type": "Creature", "count": 26}],
            tag_card_rows={"Blink": [{"card_oracle_id": "oid-card-3", "synergy_rank": 2}]},
            tag_category_rows={"Blink": [{"category": "Removal", "card_oracle_id": "oid-card-4", "category_rank": 1}]},
            tag_type_rows={"Blink": [{"card_type": "Instant", "count": 7}]},
        )
        persistence.finalize_monthly_ingestion(
            {"old-slug": {"oracle_id": "oid-cmdr", "last_seen": "2026-04-12T00:00:00+00:00"}},
            default_source_version="snapshot-1",
            now_iso_fn=lambda: "2026-04-12T01:00:00+00:00",
        )

        assert EdhrecCommanderCard.query.filter_by(commander_oracle_id="oid-cmdr").count() == 1
        assert EdhrecCommanderCategoryCard.query.filter_by(commander_oracle_id="oid-cmdr").count() == 1
        assert EdhrecCommanderTag.query.filter_by(commander_oracle_id="oid-cmdr").count() == 2
        assert EdhrecCommanderTagCard.query.filter_by(commander_oracle_id="oid-cmdr", tag="Blink").count() == 1
        assert EdhrecCommanderTagCategoryCard.query.filter_by(commander_oracle_id="oid-cmdr", tag="Blink").count() == 1
        assert EdhrecCommanderTypeDistribution.query.filter_by(commander_oracle_id="oid-cmdr", tag="").count() == 1
        assert EdhrecCommanderTypeDistribution.query.filter_by(commander_oracle_id="oid-cmdr", tag="Blink").count() == 1
        assert {
            (row.tag, row.commander_oracle_id)
            for row in EdhrecTagCommander.query.order_by(EdhrecTagCommander.tag.asc()).all()
        } == {
            ("Blink", "oid-cmdr"),
            ("Control", "oid-cmdr"),
        }
        metadata = {row.key: row.value for row in EdhrecMetadata.query.all()}
        assert metadata["last_updated"] == "2026-04-12T01:00:00+00:00"
        assert metadata["source_version"] == "snapshot-1"
        assert "old-slug" in metadata["missing_slugs"]


def test_persist_commander_tag_refresh_clears_stale_category_rows(app, db_session):
    from core.domains.decks.services.edhrec import edhrec_ingestion_persistence_service as persistence

    with app.app_context():
        db.session.add(
            EdhrecCommanderCategoryCard(
                commander_oracle_id="oid-cmdr",
                category="Old Category",
                card_oracle_id="oid-old",
                category_rank=1,
            )
        )
        db.session.add(EdhrecTagCommander(tag="Old", commander_oracle_id="oid-cmdr"))
        db.session.commit()

        persistence.persist_commander_tag_refresh(
            "oid-cmdr",
            synergy_rows=[],
            category_rows=[],
            tags=["Blink"],
            commander_type_rows=[],
            tag_card_rows={},
            tag_category_rows={},
            tag_type_rows={},
            default_source_version="snapshot-2",
            now_iso_fn=lambda: "2026-04-12T02:00:00+00:00",
        )

        assert EdhrecCommanderCategoryCard.query.filter_by(commander_oracle_id="oid-cmdr").count() == 0
        assert {
            (row.tag, row.commander_oracle_id)
            for row in EdhrecTagCommander.query.order_by(EdhrecTagCommander.tag.asc()).all()
        } == {("Blink", "oid-cmdr")}
        metadata = {row.key: row.value for row in EdhrecMetadata.query.all()}
        assert metadata["source_version"] == "snapshot-2"


def test_commander_tag_refresh_ready_requires_tag_specific_rows(app, db_session):
    from core.domains.decks.services.edhrec import edhrec_ingestion_persistence_service as persistence

    with app.app_context():
        db.session.add_all(
            [
                EdhrecCommanderCategoryCard(
                    commander_oracle_id="oid-cmdr",
                    category="Ramp",
                    card_oracle_id="oid-card-1",
                    category_rank=1,
                ),
                EdhrecCommanderTypeDistribution(
                    commander_oracle_id="oid-cmdr",
                    tag="",
                    card_type="Creature",
                    count=20,
                ),
                EdhrecCommanderTagCategoryCard(
                    commander_oracle_id="oid-cmdr",
                    tag="Blink",
                    category="Removal",
                    card_oracle_id="oid-card-2",
                    category_rank=1,
                ),
                EdhrecCommanderTypeDistribution(
                    commander_oracle_id="oid-cmdr",
                    tag="Blink",
                    card_type="Instant",
                    count=8,
                ),
            ]
        )
        db.session.commit()

        assert persistence.commander_tag_refresh_ready("oid-cmdr", ["Blink"]) is True
        assert persistence.commander_tag_refresh_ready("oid-cmdr", ["Blink", "Tokens"]) is False
