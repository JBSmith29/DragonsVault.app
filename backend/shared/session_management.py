"""Enhanced session management with timeout tracking and security features.

This module provides session timeout enforcement, activity tracking,
and security features like session fixation prevention.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Optional

from flask import Flask, current_app, g, request, session
from flask_login import current_user


def init_session_management(app: Flask) -> None:
    """Initialize session management features.
    
    Args:
        app: Flask application instance
    """
    
    @app.before_request
    def check_session_timeout():
        """Check and enforce session timeouts."""
        if not current_user.is_authenticated:
            return
        
        now = time.time()
        
        # Get session creation time
        session_created = session.get('_session_created')
        if not session_created:
            # First request, set creation time
            session['_session_created'] = now
            session['_last_activity'] = now
            return
        
        # Check absolute timeout (maximum session lifetime)
        absolute_timeout = current_app.config.get('SESSION_ABSOLUTE_TIMEOUT', 0)
        if absolute_timeout > 0:
            session_age = now - session_created
            if session_age > absolute_timeout:
                current_app.logger.info(
                    "Session expired due to absolute timeout",
                    extra={
                        "user_id": current_user.id,
                        "session_age": session_age,
                        "absolute_timeout": absolute_timeout
                    }
                )
                session.clear()
                return
        
        # Check idle timeout (inactivity timeout)
        idle_timeout = current_app.config.get('SESSION_IDLE_TIMEOUT', 0)
        if idle_timeout > 0:
            last_activity = session.get('_last_activity', session_created)
            idle_time = now - last_activity
            if idle_time > idle_timeout:
                current_app.logger.info(
                    "Session expired due to idle timeout",
                    extra={
                        "user_id": current_user.id,
                        "idle_time": idle_time,
                        "idle_timeout": idle_timeout
                    }
                )
                session.clear()
                return
        
        # Update last activity time
        session['_last_activity'] = now
    
    @app.after_request
    def add_session_headers(response):
        """Add session-related headers to response."""
        if current_user.is_authenticated and '_session_created' in session:
            now = time.time()
            session_created = session.get('_session_created', now)
            session_age = int(now - session_created)
            
            # Add session age header (for debugging/monitoring)
            response.headers['X-Session-Age'] = str(session_age)
            
            # Calculate time until timeout
            absolute_timeout = current_app.config.get('SESSION_ABSOLUTE_TIMEOUT', 0)
            if absolute_timeout > 0:
                time_remaining = int(absolute_timeout - session_age)
                if time_remaining > 0:
                    response.headers['X-Session-Expires-In'] = str(time_remaining)
        
        return response


def get_session_info() -> dict[str, any]:
    """Get information about the current session.
    
    Returns:
        Dictionary with session information
    """
    if not current_user.is_authenticated:
        return {"authenticated": False}
    
    now = time.time()
    session_created = session.get('_session_created', now)
    last_activity = session.get('_last_activity', now)
    
    session_age = int(now - session_created)
    idle_time = int(now - last_activity)
    
    absolute_timeout = current_app.config.get('SESSION_ABSOLUTE_TIMEOUT', 0)
    idle_timeout = current_app.config.get('SESSION_IDLE_TIMEOUT', 0)
    
    info = {
        "authenticated": True,
        "user_id": current_user.id,
        "session_age_seconds": session_age,
        "idle_time_seconds": idle_time,
        "session_created": datetime.fromtimestamp(session_created).isoformat(),
        "last_activity": datetime.fromtimestamp(last_activity).isoformat(),
    }
    
    if absolute_timeout > 0:
        time_remaining = absolute_timeout - session_age
        info["absolute_timeout_seconds"] = absolute_timeout
        info["time_until_absolute_timeout"] = max(0, time_remaining)
        info["absolute_timeout_at"] = (
            datetime.fromtimestamp(session_created + absolute_timeout).isoformat()
        )
    
    if idle_timeout > 0:
        time_remaining = idle_timeout - idle_time
        info["idle_timeout_seconds"] = idle_timeout
        info["time_until_idle_timeout"] = max(0, time_remaining)
    
    return info


def refresh_session() -> None:
    """Manually refresh the session (reset idle timeout).
    
    This can be called from API endpoints that should reset the idle timer
    without requiring a full page load.
    """
    if current_user.is_authenticated:
        session['_last_activity'] = time.time()


def invalidate_session() -> None:
    """Invalidate the current session (force logout).
    
    This clears all session data and forces the user to log in again.
    """
    session.clear()
    current_app.logger.info(
        "Session manually invalidated",
        extra={"user_id": getattr(current_user, 'id', None)}
    )


def rotate_session_id() -> None:
    """Rotate the session ID to prevent session fixation attacks.
    
    This should be called after login or privilege escalation.
    """
    # Flask's session is cookie-based, so we need to regenerate it
    # by copying data to a new session
    if session:
        old_data = dict(session)
        session.clear()
        session.update(old_data)
        # Reset creation time for new session
        session['_session_created'] = time.time()
        session['_last_activity'] = time.time()


__all__ = [
    "init_session_management",
    "get_session_info",
    "refresh_session",
    "invalidate_session",
    "rotate_session_id",
]
