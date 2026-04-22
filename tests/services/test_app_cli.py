from __future__ import annotations

import json


def test_cache_stats_cli_runs_and_returns_json(app):
    runner = app.test_cli_runner()

    result = runner.invoke(args=["cache-stats", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert "prints" in payload
    assert "rulings" in payload


def test_users_cli_group_is_registered(app):
    commands = app.cli.list_commands(app)

    assert "users" in commands
    assert "seed-roles" in commands
