"""Tests for deck comparison."""

from __future__ import annotations

import pytest

from core.domains.decks.services.deck_compare_service import compare_folders
from extensions import db
from tests.factories import create_card, create_folder


def _seed_card(folder, *, name, mana_value=None, type_line="Creature", quantity=1,
               oracle_id=None, color_identity=""):
    card = create_card(
        folder=folder,
        name=name,
        set_code="tst",
        collector_number=str(hash(name) % 1000),
        quantity=quantity,
        oracle_id=oracle_id or f"oracle-{name.lower()}",
    )
    card.mana_value = mana_value
    card.type_line = type_line
    card.color_identity = color_identity
    db.session.flush()
    return card


def test_compare_folders_detects_shared_and_unique(app, db_session):
    with app.app_context():
        left = create_folder(name="Left Deck")
        right = create_folder(name="Right Deck")

        _seed_card(left, name="Sol Ring", type_line="Artifact", mana_value=1)
        _seed_card(right, name="Sol Ring", type_line="Artifact", mana_value=1)
        _seed_card(left, name="Lightning Bolt", type_line="Instant", mana_value=1)
        _seed_card(right, name="Counterspell", type_line="Instant", mana_value=2)

        db.session.commit()
        report = compare_folders(left, right)

    shared_names = [row["name"] for row in report.shared]
    only_left_names = [row["name"] for row in report.only_left]
    only_right_names = [row["name"] for row in report.only_right]
    assert shared_names == ["Sol Ring"]
    assert only_left_names == ["Lightning Bolt"]
    assert only_right_names == ["Counterspell"]
    assert report.summary["shared"] == 1
    assert report.summary["left_total"] == 2
    assert report.summary["right_total"] == 2


def test_compare_folders_curve_and_type_diffs(app, db_session):
    with app.app_context():
        left = create_folder(name="Aggro")
        right = create_folder(name="Control")

        _seed_card(left, name="Goblin Guide", type_line="Creature", mana_value=1, quantity=4)
        _seed_card(right, name="Force of Will", type_line="Instant", mana_value=5, quantity=4)
        _seed_card(right, name="Island", type_line="Basic Land — Island", quantity=10)
        _seed_card(left, name="Mountain", type_line="Basic Land — Mountain", quantity=10)

        db.session.commit()
        report = compare_folders(left, right)

    curve = report.curve_diff
    # Left has four 1-drop creatures, right has none at CMC 1 (non-land only)
    assert curve["1"]["left"] == 4
    assert curve["1"]["right"] == 0
    # Right has 5-drops, left has none
    assert curve["5"]["right"] == 4

    # Land counts are in the type_diff, not the curve.
    assert report.type_diff["Land"]["left"] == 10
    assert report.type_diff["Land"]["right"] == 10
    assert report.type_diff["Creature"]["left"] == 4
    assert report.type_diff["Instant"]["right"] == 4


def test_compare_folders_rejects_self_comparison(app, db_session):
    with app.app_context():
        folder = create_folder(name="Solo")
        db.session.commit()
        with pytest.raises(ValueError):
            compare_folders(folder, folder)


def test_compare_folders_uses_oracle_id_for_matching(app, db_session):
    with app.app_context():
        left = create_folder(name="Left")
        right = create_folder(name="Right")

        # Same oracle id, slightly different displayed name (reprint with new
        # capitalization). Should still match as shared.
        _seed_card(left, name="Sol Ring", oracle_id="oracle-sol")
        _seed_card(right, name="SOL RING", oracle_id="oracle-sol")

        db.session.commit()
        report = compare_folders(left, right)

    assert len(report.shared) == 1
    assert report.shared[0]["oracle_id"] == "oracle-sol"
