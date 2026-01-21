"""Database connection pool configuration and optimization."""

from __future__ import annotations

import os
from typing import Dict, Any

from sqlalchemy import event, pool
from sqlalchemy.engine import Engine
from sqlalchemy.pool import QueuePool, StaticPool


def get_database_config() -> Dict[str, Any]:
    """Get optimized database configuration based on environment."""
    
    database_url = os.getenv('DATABASE_URL', '')
    
    # Base configuration
    config = {
        'pool_pre_ping': True,  # Validate connections before use
        'pool_recycle': 3600,   # Recycle connections every hour
        'echo': os.getenv('SQLALCHEMY_ECHO', 'false').lower() == 'true',
        'echo_pool': os.getenv('SQLALCHEMY_ECHO_POOL', 'false').lower() == 'true',
    }
    
    if 'sqlite' in database_url.lower():
        # SQLite-specific configuration
        config.update({
            'poolclass': StaticPool,
            'connect_args': {
                'check_same_thread': False,
                'timeout': 30,
                'isolation_level': None,  # Use autocommit mode
            },
            'pool_reset_on_return': None,  # Don't reset connections for SQLite
        })
    else:
        # PostgreSQL/MySQL configuration
        pool_size = int(os.getenv('DB_POOL_SIZE', '10'))
        max_overflow = int(os.getenv('DB_MAX_OVERFLOW', '20'))
        
        config.update({
            'poolclass': QueuePool,
            'pool_size': pool_size,
            'max_overflow': max_overflow,
            'pool_timeout': 30,
            'pool_reset_on_return': 'commit',
        })
        
        # PostgreSQL-specific settings
        if 'postgresql' in database_url.lower():
            config['connect_args'] = {
                'application_name': 'dragonsvault',
                'connect_timeout': 10,
            }
    
    return config


def configure_database_events(app) -> None:
    """Configure database event listeners for optimization."""
    
    @event.listens_for(Engine, 'connect')
    def set_sqlite_pragma(dbapi_connection, connection_record):
        """Set SQLite pragmas for better performance."""
        if 'sqlite' in str(dbapi_connection):
            cursor = dbapi_connection.cursor()
            
            # Performance pragmas
            pragmas = [
                'PRAGMA foreign_keys=ON',
                'PRAGMA journal_mode=WAL',
                'PRAGMA synchronous=NORMAL',
                'PRAGMA temp_store=MEMORY',
                'PRAGMA cache_size=-64000',  # 64MB cache
                'PRAGMA mmap_size=268435456',  # 256MB mmap
                'PRAGMA optimize',
            ]
            
            for pragma in pragmas:
                try:
                    cursor.execute(pragma)
                except Exception as e:
                    app.logger.warning(f'Failed to set pragma {pragma}: {e}')
            
            cursor.close()
    
    @event.listens_for(Engine, 'checkout')
    def receive_checkout(dbapi_connection, connection_record, connection_proxy):
        """Handle connection checkout events."""
        # Log connection pool statistics periodically
        if hasattr(connection_proxy, 'pool'):
            pool_obj = connection_proxy.pool
            if hasattr(pool_obj, 'size'):
                app.logger.debug(
                    'Connection pool stats',
                    extra={
                        'pool_size': pool_obj.size(),
                        'checked_in': pool_obj.checkedin(),
                        'checked_out': pool_obj.checkedout(),
                        'overflow': getattr(pool_obj, 'overflow', 0),
                    }
                )
    
    @event.listens_for(Engine, 'close')
    def receive_close(dbapi_connection, connection_record):
        """Handle connection close events."""
        app.logger.debug('Database connection closed')


class DatabaseHealthCheck:
    """Monitor database connection health."""
    
    def __init__(self, db):
        self.db = db
        self._last_check = 0
        self._check_interval = 60  # Check every minute
    
    def is_healthy(self) -> bool:
        """Check if database connection is healthy."""
        import time
        
        current_time = time.time()
        if current_time - self._last_check < self._check_interval:
            return True  # Skip check if done recently
        
        try:
            # Simple query to test connection
            self.db.session.execute('SELECT 1').scalar()
            self._last_check = current_time
            return True
        except Exception:
            return False
    
    def get_pool_status(self) -> Dict[str, Any]:
        """Get connection pool status information."""
        try:
            pool_obj = self.db.engine.pool
            return {
                'size': getattr(pool_obj, 'size', lambda: 0)(),
                'checked_in': getattr(pool_obj, 'checkedin', lambda: 0)(),
                'checked_out': getattr(pool_obj, 'checkedout', lambda: 0)(),
                'overflow': getattr(pool_obj, 'overflow', 0),
                'invalid': getattr(pool_obj, 'invalid', 0),
            }
        except Exception:
            return {}


def optimize_database_queries():
    """Decorator to optimize database queries."""
    
    def decorator(func):
        def wrapper(*args, **kwargs):
            # Add query optimization hints here
            return func(*args, **kwargs)
        return wrapper
    
    return decorator