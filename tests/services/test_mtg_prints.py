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


def test_resolve_created_tokens_prefers_oracle_all_parts(monkeypatch):
    named = [{"id": "goblin-print", "name": "Goblin", "type_line": "Token Creature — Goblin"}]
    monkeypatch.setattr(mtg_prints.sc, "tokens_from_oracle", lambda oracle_id: named)

    tokens = mtg_prints.resolve_created_tokens("krenko-oracle", "Create a 1/1 red Goblin creature token.")

    assert tokens == named


def test_resolve_created_tokens_falls_back_to_text_when_only_generic(monkeypatch):
    # all_parts data resolves to a placeholder-only stub (no id, generic name)…
    generic = [{"id": None, "name": "Token", "type_line": "Token"}]
    monkeypatch.setattr(mtg_prints.sc, "tokens_from_oracle", lambda oracle_id: generic)

    tokens = mtg_prints.resolve_created_tokens("treasure-oracle", "Create a Treasure token.")

    # …so the text heuristic supplies the named Treasure stub instead.
    assert [token["name"] for token in tokens] == ["Treasure"]


def test_resolve_created_tokens_handles_missing_oracle_id(monkeypatch):
    def _boom(oracle_id):  # pragma: no cover - must not be called
        raise AssertionError("tokens_from_oracle should not run without an oracle id")

    monkeypatch.setattr(mtg_prints.sc, "tokens_from_oracle", _boom)

    tokens = mtg_prints.resolve_created_tokens(None, "Create a Food token.")

    assert [token["name"] for token in tokens] == ["Food"]
