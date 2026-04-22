from core.domains.games.services import game_compat_service, game_service


def test_game_service_delegates_compat_exports():
    assert game_service.GameSession is game_compat_service.GameSession
    assert game_service.parse_positive_int is game_compat_service.parse_positive_int
    assert game_service._session_filters is game_compat_service._session_filters
    assert "GameSession" in dir(game_service)
