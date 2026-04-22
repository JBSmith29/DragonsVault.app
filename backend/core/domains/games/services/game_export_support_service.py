"""Shared game export helpers."""

from __future__ import annotations


def game_csv_headers_wide(*, include_game_id: bool = True) -> list[str]:
    headers = [
        "played_at",
        "notes",
        "win_via_combo",
        "winner_seat",
        "seat_count",
    ]
    if include_game_id:
        headers.insert(0, "game_id")
    for seat_number in range(1, 5):
        headers.extend(
            [
                f"seat_{seat_number}_player_name",
                f"seat_{seat_number}_player_user_id",
                f"seat_{seat_number}_deck_name",
                f"seat_{seat_number}_deck_folder_id",
                f"seat_{seat_number}_commander_name",
                f"seat_{seat_number}_commander_oracle_id",
                f"seat_{seat_number}_bracket_level",
                f"seat_{seat_number}_bracket_label",
                f"seat_{seat_number}_bracket_score",
                f"seat_{seat_number}_power_score",
                f"seat_{seat_number}_turn_order",
            ]
        )
    return headers


__all__ = ["game_csv_headers_wide"]
