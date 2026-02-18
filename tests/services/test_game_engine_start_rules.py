import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
ENGINE_SRC = ROOT_DIR / "backend" / "microservices" / "game-engine" / "src"
if str(ENGINE_SRC) not in sys.path:
    sys.path.insert(0, str(ENGINE_SRC))

from game_engine.engine import apply_action, new_game_state


def _start_game(state: dict, *, player_id: int = 1) -> dict:
    return apply_action(
        state,
        {
            "player_id": player_id,
            "action_type": "start_game",
            "payload": {},
        },
    )


def test_commander_start_requires_minimum_two_players():
    state = new_game_state(format_name="commander", player_ids=[1])

    result = _start_game(state, player_id=1)

    assert result["ok"] is False
    assert result["error"] == "commander_min_players_required"
    assert state["status"] == "waiting"


def test_commander_start_rejects_more_than_four_players():
    state = new_game_state(format_name="commander", player_ids=[1, 2, 3, 4, 5])

    result = _start_game(state, player_id=1)

    assert result["ok"] is False
    assert result["error"] == "commander_max_players_exceeded"
    assert state["status"] == "waiting"


def test_commander_start_allows_two_players():
    state = new_game_state(format_name="commander", player_ids=[1, 2])

    result = _start_game(state, player_id=1)

    assert result["ok"] is True
    assert result["state"]["status"] == "mulligan"
    assert any(event.get("type") == "game_started" for event in result.get("events") or [])


def test_non_commander_start_is_not_limited_to_commander_range():
    state = new_game_state(format_name="standard", player_ids=[1])

    result = _start_game(state, player_id=1)

    assert result["ok"] is True
    assert result["state"]["status"] == "mulligan"
