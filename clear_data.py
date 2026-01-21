#!/usr/bin/env python3
"""Clear game log, user, and pod database data."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'backend'))

from app import create_app
from extensions import db
from models.game import (
    GameSession, GameSeat, GamePlayer, GameDeck, GameSeatAssignment,
    GameRosterPlayer, GamePod, GamePodMember, GameRosterDeck
)
from models.user import User, AuditLog, UserFollow

def clear_database_data():
    """Clear all data from game log, user, and pod tables."""
    app = create_app()
    
    with app.app_context():
        try:
            # Clear game-related tables (in dependency order)
            GameSeatAssignment.query.delete()
            GameSeat.query.delete()
            GameDeck.query.delete()
            GameSession.query.delete()
            GamePodMember.query.delete()
            GamePod.query.delete()
            GameRosterDeck.query.delete()
            GameRosterPlayer.query.delete()
            GamePlayer.query.delete()
            
            # Clear user-related tables
            UserFollow.query.delete()
            AuditLog.query.delete()
            User.query.delete()
            
            # Commit all changes
            db.session.commit()
            print("Successfully cleared all game log, user, and pod data from the database.")
            
        except Exception as e:
            db.session.rollback()
            print(f"Error clearing database data: {e}")
            return 1
    
    return 0

if __name__ == "__main__":
    sys.exit(clear_database_data())