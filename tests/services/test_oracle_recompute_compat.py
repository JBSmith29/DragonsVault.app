def test_oracle_recompute_re_exports_deck_tag_synergy_api():
    from shared.jobs.background import oracle_deck_tag_synergy_service as synergy_service
    from shared.jobs.background import oracle_recompute

    assert oracle_recompute.ORACLE_DECK_TAG_VERSION == synergy_service.ORACLE_DECK_TAG_VERSION
    assert oracle_recompute.oracle_deck_tag_source_version is synergy_service.oracle_deck_tag_source_version
    assert oracle_recompute.recompute_deck_tag_synergies is synergy_service.recompute_deck_tag_synergies


def test_oracle_recompute_re_exports_role_recompute_api():
    from shared.jobs.background import oracle_recompute

    called = {}

    def fake_recompute_all_roles(*, merge_existing=True):
        called["merge_existing"] = merge_existing
        return {"ok": True}

    oracle_recompute.oracle_role_recompute_service.recompute_all_roles = fake_recompute_all_roles

    assert oracle_recompute.recompute_all_roles(merge_existing=False) == {"ok": True}
    assert called == {"merge_existing": False}
