from services.oracle_tagging import derive_deck_tags, derive_evergreen_keywords, ensure_fallback_tag


def test_derive_evergreen_keywords_from_keywords():
    keywords = ["Flying", "Ward", "Kicker"]
    out = derive_evergreen_keywords(oracle_text="", keywords=keywords)
    assert "flying" in out
    assert "ward" in out
    assert "kicker" not in out


def test_derive_evergreen_keywords_from_text():
    out = derive_evergreen_keywords(oracle_text="Protection from red", keywords=[])
    assert "protection" in out


def test_derive_deck_tags_keyword_and_typal():
    tags = derive_deck_tags(
        oracle_text="Whenever a land enters the battlefield under your control, ...",
        type_line="Creature — Dragon",
        keywords=["Landfall", "Flying"],
        typals=["dragon"],
        roles=[],
    )
    assert "Landfall" in tags
    assert "Dragons" in tags


def test_derive_deck_tags_roles_and_type_line():
    tags = derive_deck_tags(
        oracle_text="Create a Treasure token. Draw a card.",
        type_line="Legendary Artifact",
        keywords=[],
        typals=[],
        roles=["ramp", "draw"],
    )
    assert "Treasure" in tags
    assert "Card Draw" in tags
    assert "Artifacts" in tags
    assert "Legendary Matters" in tags


def test_derive_deck_tags_fallback():
    tags = derive_deck_tags(
        oracle_text="",
        type_line="",
        keywords=[],
        typals=[],
        roles=[],
    )
    assert tags == set()


def test_ensure_fallback_tag_only_when_needed():
    assert ensure_fallback_tag(set(), set()) == {"Good Stuff"}
    assert ensure_fallback_tag(set(), {"flying"}) == set()
    assert ensure_fallback_tag({"Tokens"}, set()) == {"Tokens"}
