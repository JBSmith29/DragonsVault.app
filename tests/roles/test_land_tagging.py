from roles.role_engine import classify_land, get_land_tags_for_card, get_roles_for_card, get_subroles_for_card


def test_shock_land_classification_and_tags():
    card = {
        "name": "Stomping Ground",
        "type_line": "Land - Mountain Forest",
        "oracle_text": (
            "({T}: Add {R} or {G}.)\n"
            "As Stomping Ground enters the battlefield, you may pay 2 life. "
            "If you don't, it enters the battlefield tapped."
        ),
    }
    result = classify_land(card)
    assert result["primary_land_category"] == "Shock Land"
    assert {"Mountain", "Forest", "Life Payment", "Enters Tapped", "Conditional Untapped"} <= set(result["tags"])
    tags = get_land_tags_for_card(card)
    assert "Shock Land" in tags
    assert "Life Payment" in tags


def test_basic_and_snow_basic_land_classification():
    basic = {
        "name": "Forest",
        "type_line": "Basic Land - Forest",
        "oracle_text": "({T}: Add {G}.)",
    }
    snow = {
        "name": "Snow-Covered Island",
        "type_line": "Basic Snow Land - Island",
        "oracle_text": "({T}: Add {U}.)",
    }
    assert classify_land(basic)["primary_land_category"] == "Basic Land"
    assert "Forest" in classify_land(basic)["tags"]
    assert classify_land(snow)["primary_land_category"] == "Snow Basic"
    assert {"Island", "Snow"} <= set(classify_land(snow)["tags"])


def test_fetch_land_classification():
    card = {
        "name": "Misty Rainforest",
        "type_line": "Land",
        "oracle_text": (
            "{T}, Pay 1 life, Sacrifice Misty Rainforest: Search your library for a Forest or "
            "Island card, put it onto the battlefield, then shuffle."
        ),
    }
    result = classify_land(card)
    assert result["primary_land_category"] == "Fetch Land"
    assert {"Life Payment", "Sacrifice"} <= set(result["tags"])


def test_spell_land_mdfc_uses_land_face():
    card = {
        "name": "Sejiri Shelter // Sejiri Glacier",
        "type_line": "Instant // Land",
        "oracle_text": "Sejiri Shelter text\n\n//\n\nSejiri Glacier text",
        "card_faces": [
            {"type_line": "Instant", "oracle_text": "Instant text"},
            {"type_line": "Land - Plains", "oracle_text": "{T}: Add {W}."},
        ],
    }
    result = classify_land(card)
    assert result["primary_land_category"] == "Spell Land (MDFC)"
    assert "Plains" in result["tags"]


def test_land_roles_and_subroles_override():
    card = {
        "name": "Hallowed Fountain",
        "type_line": "Land - Plains Island",
        "oracle_text": (
            "({T}: Add {W} or {U}.)\n"
            "As Hallowed Fountain enters the battlefield, you may pay 2 life. "
            "If you don't, it enters the battlefield tapped."
        ),
    }
    assert get_roles_for_card(card) == ["land"]
    subroles = get_subroles_for_card(card)
    assert "land:shock land" in subroles
