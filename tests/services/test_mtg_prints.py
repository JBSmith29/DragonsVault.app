from shared import mtg_prints


def test_effective_color_identity_adds_artifact_production_colors():
    letters = mtg_prints._effective_color_identity(
        "Artifact Creature",
        "{T}: Add {U} or {R}.",
        ["W"],
    )

    assert letters == ["W", "U", "R"]


def test_small_thumb_for_print_uses_face_image_when_top_level_missing():
    payload = {
        "card_faces": [
            {"image_uris": {"small": "front-small"}},
            {"image_uris": {"small": "back-small"}},
        ]
    }

    assert mtg_prints._small_thumb_for_print(payload) == "front-small"
