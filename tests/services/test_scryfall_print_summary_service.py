from __future__ import annotations

from core.domains.cards.services import scryfall_print_summary_service as summary


def test_display_name_for_print_dedupes_repeated_face_names():
    print_obj = {
        "name": "Brutal Cathar // Brutal Cathar",
        "card_faces": [
            {"name": "Brutal Cathar"},
            {"name": "Brutal Cathar"},
        ],
    }

    assert summary.display_name_for_print(print_obj) == "Brutal Cathar"


def test_type_label_and_image_for_print_use_face_fallbacks():
    print_obj = {
        "set": "mid",
        "collector_number": "7",
        "card_faces": [
            {
                "name": "Galvanic Giant",
                "type_line": "Artifact Creature — Construct",
                "image_uris": {"small": "front-small", "normal": "front-normal"},
            },
            {
                "name": "Overload Spark",
                "type_line": "Instant",
            },
        ],
    }

    type_label = summary.type_label_for_print(print_obj)
    image_payload = summary.image_for_print(
        print_obj,
        image_uris_fn=lambda card: {
            "small": ((card.get("card_faces") or [{}])[0].get("image_uris") or {}).get("small"),
            "normal": ((card.get("card_faces") or [{}])[0].get("image_uris") or {}).get("normal"),
            "large": None,
        },
    )

    assert type_label == "Artifact Creature — Construct // Instant"
    assert image_payload["small"] == "front-small"
    assert image_payload["label"] == "MID #7"


def test_resolve_print_bundle_builds_standard_summary_payload():
    print_obj = {
        "set": "dgm",
        "collector_number": "99",
        "card_faces": [
            {"name": "Wear", "type_line": "Instant"},
            {"name": "Tear", "type_line": "Instant"},
        ],
    }

    bundle = summary.resolve_print_bundle(
        "dgm",
        "99",
        name_hint="Wear",
        find_by_set_cn_fn=lambda set_code, collector_number, name_hint=None: print_obj,
        image_uris_fn=lambda _card: {"small": "wear-small", "normal": "wear-normal", "large": None},
    )

    assert bundle == {
        "print": print_obj,
        "display_name": "Wear // Tear",
        "type_label": "Instant",
        "image": {
            "small": "wear-small",
            "normal": "wear-normal",
            "large": None,
            "label": "DGM #99",
        },
    }
