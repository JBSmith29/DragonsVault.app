from models.role import OracleCoreRoleTag, OracleDeckTag, OracleEvergreenTag


def test_build_deck_tag_synergy_rows_selects_roles_evergreen_and_cards():
    from shared.jobs.background import oracle_deck_tag_synergy_service as service

    deck_tag_rows = [
        OracleDeckTag(tag="Aggro", oracle_id="oid1"),
        OracleDeckTag(tag="Aggro", oracle_id="oid2"),
        OracleDeckTag(tag="Aggro", oracle_id="oid3"),
    ]
    core_role_rows = [
        OracleCoreRoleTag(oracle_id="oid1", role="ramp"),
        OracleCoreRoleTag(oracle_id="oid2", role="ramp"),
        OracleCoreRoleTag(oracle_id="oid3", role="ramp"),
        OracleCoreRoleTag(oracle_id="oid6", role="draw"),
        OracleCoreRoleTag(oracle_id="oid7", role="draw"),
    ]
    evergreen_rows = [
        OracleEvergreenTag(oracle_id="oid1", keyword="flying"),
        OracleEvergreenTag(oracle_id="oid2", keyword="flying"),
        OracleEvergreenTag(oracle_id="oid3", keyword="flying"),
        OracleEvergreenTag(oracle_id="oid4", keyword="flying"),
        OracleEvergreenTag(oracle_id="oid5", keyword="flying"),
    ]

    core_synergy, evergreen_synergy, card_synergy = service.build_deck_tag_synergy_rows(
        deck_tag_rows,
        core_role_rows,
        evergreen_rows,
    )

    assert len(core_synergy) == 1
    assert core_synergy[0].deck_tag == "Aggro"
    assert core_synergy[0].role == "ramp"

    assert len(evergreen_synergy) == 1
    assert evergreen_synergy[0].deck_tag == "Aggro"
    assert evergreen_synergy[0].keyword == "flying"

    assert {row.oracle_id for row in card_synergy} == {"oid1", "oid2", "oid3"}
