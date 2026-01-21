"""Enhanced logging configuration for DragonsVault."""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from flask import Flask, g, has_request_context, request


class StructuredFormatter(logging.Formatter):
    """Structured JSON formatter for better log parsing."""
    
    def format(self, record: logging.LogRecord) -> str:
        """Format log record as structured JSON."""
        
        # Base log data
        log_data = {
            'timestamp': self.formatTime(record, self.datefmt),
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
            'module': record.module,
            'function': record.funcName,
            'line': record.lineno,
        }
        
        # Add request context if available
        if has_request_context():
            log_data.update({
                'request_id': getattr(g, 'request_id', 'unknown'),
                'method': request.method,
                'path': request.path,
                'remote_addr': request.remote_addr,
                'user_agent': request.headers.get('User-Agent', ''),
            })
        
        # Add exception info if present
        if record.exc_info:
            log_data['exception'] = self.formatException(record.exc_info)
        
        # Add extra fields from record
        for key, value in record.__dict__.items():
            if key not in log_data and not key.startswith('_'):
                if isinstance(value, (str, int, float, bool, list, dict)):
                    log_data[key] = value
        
        return json.dumps(log_data, ensure_ascii=False, default=str)


class SecurityAuditHandler(logging.Handler):
    """Special handler for security-related events."""
    
    def __init__(self, audit_file: str):
        super().__init__()
        self.audit_file = audit_file
        
        # Ensure audit directory exists
        Path(audit_file).parent.mkdir(parents=True, exist_ok=True)
        
        # Create rotating file handler for audit logs
        self.file_handler = logging.handlers.RotatingFileHandler(
            audit_file,
            maxBytes=10 * 1024 * 1024,  # 10MB
            backupCount=5,
            encoding='utf-8'
        )
        self.file_handler.setFormatter(StructuredFormatter())
    
    def emit(self, record: logging.LogRecord) -> None:
        """Emit security audit log."""
        # Only log security-related events
        security_keywords = [
            'login', 'logout', 'authentication', 'authorization',
            'csrf', 'security', 'audit', 'permission', 'access'
        ]
        
        message = record.getMessage().lower()
        if any(keyword in message for keyword in security_keywords):
            self.file_handler.emit(record)


def configure_logging(app: Flask) -> None:
    """Configure comprehensive logging for the application."""
    
    # Get log level from environment
    log_level = getattr(logging, os.getenv('LOG_LEVEL', 'INFO').upper())
    
    # Create logs directory
    logs_dir = Path(app.instance_path) / 'logs'
    logs_dir.mkdir(parents=True, exist_ok=True)
    
    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    
    # Clear existing handlers
    root_logger.handlers.clear()
    
    # Console handler with structured formatting
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(StructuredFormatter())
    root_logger.addHandler(console_handler)
    
    # Application log file handler
    app_log_file = logs_dir / 'application.log'
    app_handler = logging.handlers.RotatingFileHandler(
        app_log_file,
        maxBytes=50 * 1024 * 1024,  # 50MB
        backupCount=10,
        encoding='utf-8'
    )
    app_handler.setLevel(log_level)
    app_handler.setFormatter(StructuredFormatter())
    root_logger.addHandler(app_handler)
    
    # Error log file handler (errors and above only)\n    error_log_file = logs_dir / 'errors.log'\n    error_handler = logging.handlers.RotatingFileHandler(\n        error_log_file,\n        maxBytes=20 * 1024 * 1024,  # 20MB\n        backupCount=5,\n        encoding='utf-8'\n    )\n    error_handler.setLevel(logging.ERROR)\n    error_handler.setFormatter(StructuredFormatter())\n    root_logger.addHandler(error_handler)\n    \n    # Security audit handler\n    audit_log_file = logs_dir / 'security_audit.log'\n    audit_handler = SecurityAuditHandler(str(audit_log_file))\n    audit_handler.setLevel(logging.INFO)\n    root_logger.addHandler(audit_handler)\n    \n    # Performance log handler\n    perf_log_file = logs_dir / 'performance.log'\n    perf_handler = logging.handlers.RotatingFileHandler(\n        perf_log_file,\n        maxBytes=20 * 1024 * 1024,  # 20MB\n        backupCount=3,\n        encoding='utf-8'\n    )\n    perf_handler.setLevel(logging.INFO)\n    perf_handler.setFormatter(StructuredFormatter())\n    \n    # Add filter for performance logs\n    class PerformanceFilter(logging.Filter):\n        def filter(self, record):\n            return 'performance' in record.getMessage().lower() or 'slow' in record.getMessage().lower()\n    \n    perf_handler.addFilter(PerformanceFilter())\n    root_logger.addHandler(perf_handler)\n    \n    # Configure specific loggers\n    configure_specific_loggers()\n    \n    app.logger.info('Logging configuration completed', extra={\n        'log_level': logging.getLevelName(log_level),\n        'logs_directory': str(logs_dir)\n    })\n\n\ndef configure_specific_loggers() -> None:\n    \"\"\"Configure specific loggers for different components.\"\"\"\n    \n    # Database query logger\n    db_logger = logging.getLogger('sqlalchemy.engine')\n    db_logger.setLevel(logging.WARNING)  # Only log warnings and errors by default\n    \n    # Cache logger\n    cache_logger = logging.getLogger('cache')\n    cache_logger.setLevel(logging.INFO)\n    \n    # Security logger\n    security_logger = logging.getLogger('security')\n    security_logger.setLevel(logging.INFO)\n    \n    # Performance logger\n    perf_logger = logging.getLogger('performance')\n    perf_logger.setLevel(logging.INFO)\n\n\ndef get_logger(name: str) -> logging.Logger:\n    \"\"\"Get a logger with the specified name.\"\"\"\n    return logging.getLogger(name)\n\n\n# Convenience loggers\nsecurity_logger = get_logger('security')\nperformance_logger = get_logger('performance')\ncache_logger = get_logger('cache')\ndb_logger = get_logger('database')