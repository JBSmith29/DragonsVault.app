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


def _ensure_tables():
    return None


def test_edhrec_query_service_groups_categories_and_tags(app, db_session, monkeypatch):
    from core.domains.decks.services import edhrec_cache_query_service

    with app.app_context():
        db.session.add_all(
            [
                EdhrecCommanderTag(commander_oracle_id="oid-cmdr", tag="Blink"),
                EdhrecCommanderTag(commander_oracle_id="oid-cmdr", tag="Control"),
                EdhrecCommanderCategoryCard(
                    commander_oracle_id="oid-cmdr",
                    category="Ramp",
                    category_rank=1,
                    card_oracle_id="oid-card-1",
                    synergy_rank=1,
                    synergy_score=0.22,
                    inclusion_percent=24.0,
                ),
                EdhrecCommanderTagCategoryCard(
                    commander_oracle_id="oid-cmdr",
                    tag="Blink",
                    category="Removal",
                    category_rank=1,
                    card_oracle_id="oid-card-2",
                    synergy_rank=2,
                    synergy_score=0.14,
                    inclusion_percent=18.0,
                ),
                EdhrecCommanderTagCard(
                    commander_oracle_id="oid-cmdr",
                    tag="Blink",
                    card_oracle_id="oid-card-2",
                    synergy_rank=2,
                    synergy_score=0.14,
                    inclusion_percent=18.0,
                ),
                EdhrecCommanderTypeDistribution(commander_oracle_id="oid-cmdr", tag="", card_type="Creature", count=25),
                EdhrecCommanderTypeDistribution(commander_oracle_id="oid-cmdr", tag="Blink", card_type="Instant", count=9),
                EdhrecTagCommander(tag="Blink", commander_oracle_id="oid-cmdr"),
                EdhrecMetadata(key="snapshot", value="unused"),
            ]
        )
        db.session.commit()

        categories = edhrec_cache_query_service.get_commander_category_groups(
            "oid-cmdr",
            ensure_tables_fn=_ensure_tables,
            limit=10,
        )
        tag_categories = edhrec_cache_query_service.get_commander_category_groups(
            "oid-cmdr",
            tag="Blink",
            ensure_tables_fn=_ensure_tables,
            limit=10,
        )
        tag_groups = edhrec_cache_query_service.get_commander_tag_synergy_groups(
            "oid-cmdr",
            ["Blink"],
            ensure_tables_fn=_ensure_tables,
            limit=10,
        )
        type_rows = edhrec_cache_query_service.get_commander_type_distribution(
            "oid-cmdr",
            ensure_tables_fn=_ensure_tables,
        )
        tag_type_rows = edhrec_cache_query_service.get_commander_type_distribution(
            "oid-cmdr",
            tag="Blink",
            ensure_tables_fn=_ensure_tables,
        )
        tags = edhrec_cache_query_service.get_commander_tags("oid-cmdr", ensure_tables_fn=_ensure_tables)
        commanders = edhrec_cache_query_service.get_tag_commanders("Blink", ensure_tables_fn=_ensure_tables)

    assert tags == ["Blink", "Control"]
    assert commanders == ["oid-cmdr"]
    assert type_rows == [("Creature", 25)]
    assert tag_type_rows == [("Instant", 9)]
    assert categories[0]["label"] == "Ramp"
    assert categories[0]["cards"][0]["oracle_id"] == "oid-card-1"
    assert tag_categories[0]["label"] == "Removal"
    assert tag_groups[0]["label"] == "Blink"
    assert tag_groups[0]["cards"][0]["oracle_id"] == "oid-card-2"


def test_edhrec_query_service_prefers_tag_specific_synergy(app, db_session, monkeypatch):
    from core.domains.decks.services import edhrec_cache_query_service

    with app.app_context():
        db.session.add_all(
            [
                EdhrecCommanderTag(commander_oracle_id="oid-cmdr", tag="Blink"),
                EdhrecCommanderCard(
                    commander_oracle_id="oid-cmdr",
                    card_oracle_id="oid-card-1",
                    synergy_rank=5,
                    synergy_score=0.15,
                    inclusion_percent=10.0,
                ),
                EdhrecCommanderTagCard(
                    commander_oracle_id="oid-cmdr",
                    tag="Blink",
                    card_oracle_id="oid-card-1",
                    synergy_rank=2,
                    synergy_score=0.3,
                    inclusion_percent=18.0,
                ),
                EdhrecCommanderTagCard(
                    commander_oracle_id="oid-cmdr",
                    tag="Blink",
                    card_oracle_id="oid-card-2",
                    synergy_rank=1,
                    synergy_score=0.2,
                    inclusion_percent=25.0,
                ),
            ]
        )
        db.session.commit()

        monkeypatch.setattr(
            edhrec_cache_query_service.sc,
            "prints_for_oracle",
            lambda oracle_id: [{"name": {"oid-card-1": "Card One", "oid-card-2": "Card Two"}[oracle_id]}],
        )

        rows = edhrec_cache_query_service.get_commander_synergy(
            "oid-cmdr",
            tags=["Blink"],
            prefer_tag_specific=True,
            limit=10,
            ensure_tables_fn=_ensure_tables,
        )

    assert [row["oracle_id"] for row in rows] == ["oid-card-1", "oid-card-2"]
    assert rows[0]["name"] == "Card One"
    assert rows[0]["tag_matches"] == ["Blink"]
    assert rows[0]["synergy_percent"] == 30.0
