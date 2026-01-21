"""API endpoints for games administration and metrics management."""

from flask import Blueprint, jsonify, request
from flask_login import login_required, current_user
from extensions import db, cache
from core.domains.games.models import GamePod, GameSession
from core.domains.users.models import User
from sqlalchemy import func
from datetime import date

games_api = Blueprint('games_api', __name__, url_prefix='/api/games')


@games_api.route('/metrics/refresh', methods=['POST'])
@login_required
def refresh_metrics():
    """Refresh cached metrics for the current user."""
    try:
        # Clear user-specific cache entries
        cache_keys = [
            f"user_metrics_{current_user.id}",
            f"user_metrics_30_{current_user.id}",
        ]
        
        for key in cache_keys:
            cache.delete(key)
        
        return jsonify({
            'success': True,
            'message': 'Metrics cache refreshed successfully'
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Failed to refresh metrics: {str(e)}'
        }), 500


@games_api.route('/admin/system-stats', methods=['GET'])
@login_required
def get_system_stats():
    """Get system-wide statistics (admin only)."""
    if not current_user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403
    
    try:
        # Get system statistics
        total_games = db.session.query(func.count(GameSession.id)).scalar() or 0
        total_users = db.session.query(func.count(User.id)).scalar() or 0
        
        # Games today
        today = date.today()
        games_today = (
            db.session.query(func.count(GameSession.id))
            .filter(func.date(GameSession.played_at) == today)
            .scalar() or 0
        )
        
        # Total pods
        total_pods = db.session.query(func.count(GamePod.id)).scalar() or 0
        
        # Combo rate
        combo_wins = (
            db.session.query(func.count(GameSession.id))
            .filter(GameSession.win_via_combo.is_(True))
            .scalar() or 0
        )
        combo_rate = round((combo_wins / total_games) * 100, 1) if total_games > 0 else 0
        
        return jsonify({
            'success': True,
            'stats': {
                'total_games': total_games,
                'total_users': total_users,
                'games_today': games_today,
                'total_pods': total_pods,
                'combo_rate': combo_rate,
                'avg_games_per_user': round(total_games / total_users, 1) if total_users > 0 else 0
            }
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Failed to get system stats: {str(e)}'
        }), 500


@games_api.route('/admin/clear-cache', methods=['POST'])
@login_required
def clear_cache():
    """Clear all cached data (admin only)."""
    if not current_user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403
    
    try:
        cache.clear()
        return jsonify({
            'success': True,
            'message': 'All caches cleared successfully'
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Failed to clear cache: {str(e)}'
        }), 500


@games_api.route('/admin/health-check', methods=['GET'])
@login_required
def health_check():
    """Perform system health check (admin only)."""
    if not current_user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403
    
    try:
        # Basic health checks
        checks = {
            'database': False,
            'cache': False,
            'recent_activity': False
        }
        
        # Test database connection
        try:
            db.session.execute('SELECT 1')
            checks['database'] = True
        except Exception:
            pass
        
        # Test cache
        try:
            cache.set('health_check', 'ok', timeout=1)
            checks['cache'] = cache.get('health_check') == 'ok'
            cache.delete('health_check')
        except Exception:
            pass
        
        # Check for recent activity (games in last 24 hours)
        try:
            from datetime import datetime, timedelta
            yesterday = datetime.now() - timedelta(days=1)
            recent_games = (
                db.session.query(func.count(GameSession.id))
                .filter(GameSession.created_at >= yesterday)
                .scalar() or 0
            )
            checks['recent_activity'] = recent_games > 0
        except Exception:
            pass
        
        all_healthy = all(checks.values())
        
        return jsonify({
            'success': True,
            'healthy': all_healthy,
            'checks': checks,
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Health check failed: {str(e)}'
        }), 500


@games_api.route('/quick-pod', methods=['POST'])
@login_required
def quick_pod_create():
    """Create a pod with players in one step."""
    from core.domains.games.services import games_enhanced
    return games_enhanced.api_quick_pod_create()


@games_api.route('/auto-assign-decks', methods=['POST'])
@login_required
def auto_assign_decks():
    """Auto-assign decks to pod members."""
    from core.domains.games.services import games_enhanced
    return games_enhanced.api_auto_assign_decks()


@games_api.route('/quick-game', methods=['POST'])
@login_required
def quick_game_save():
    """Save a quick game log."""
    from core.domains.games.services import games_enhanced
    return games_enhanced.api_quick_game_save()


def register_games_api(app):
    """Register the games API blueprint with the Flask app."""
    app.register_blueprint(games_api)
    app.register_blueprint(games_api, url_prefix="/api/v1/games", name="games_api_v1")
