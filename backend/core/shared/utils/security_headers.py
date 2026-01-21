"""Security headers middleware for enhanced protection."""

from __future__ import annotations

from typing import Dict, Optional

from flask import Flask, Response


class SecurityHeadersMiddleware:
    """Middleware to add security headers to all responses."""
    
    def __init__(self, app: Optional[Flask] = None):
        self.app = app
        if app is not None:
            self.init_app(app)
    
    def init_app(self, app: Flask) -> None:
        """Initialize the middleware with Flask app."""
        app.after_request(self.add_security_headers)
    
    def add_security_headers(self, response: Response) -> Response:
        """Add security headers to response."""
        
        # Content Security Policy
        if not response.headers.get('Content-Security-Policy'):
            csp = self._build_csp()
            response.headers['Content-Security-Policy'] = csp
        
        # Security headers
        security_headers = {
            'X-Content-Type-Options': 'nosniff',
            'X-Frame-Options': 'DENY',
            'X-XSS-Protection': '1; mode=block',
            'Referrer-Policy': 'strict-origin-when-cross-origin',
            'Permissions-Policy': 'geolocation=(), microphone=(), camera=()',
        }
        
        for header, value in security_headers.items():
            if not response.headers.get(header):
                response.headers[header] = value
        
        # HSTS for HTTPS
        if self._is_https_request():
            response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
        
        return response
    
    def _build_csp(self) -> str:
        """Build Content Security Policy header."""
        directives = {
            'default-src': "'self'",
            'script-src': "'self' 'unsafe-inline' https://cdn.jsdelivr.net https://unpkg.com https://instant.page",
            'style-src': "'self' 'unsafe-inline' https://cdn.jsdelivr.net",
            'img-src': "'self' data: https: blob:",
            'font-src': "'self' data: https://cdn.jsdelivr.net",
            'connect-src': "'self' https://api.scryfall.com",
            'frame-src': "'none'",
            'object-src': "'none'",
            'base-uri': "'self'",
            'form-action': "'self'",
        }
        
        return '; '.join(f"{directive} {sources}" for directive, sources in directives.items())
    
    def _is_https_request(self) -> bool:
        """Check if the current request is over HTTPS."""
        from flask import request
        return request.is_secure or request.headers.get('X-Forwarded-Proto') == 'https'


def configure_security_headers(app: Flask) -> None:
    """Configure security headers for the Flask application."""
    
    # Initialize security headers middleware
    SecurityHeadersMiddleware(app)
    
    # Additional security configurations
    app.config.setdefault('SESSION_COOKIE_SECURE', True)
    app.config.setdefault('SESSION_COOKIE_HTTPONLY', True)
    app.config.setdefault('SESSION_COOKIE_SAMESITE', 'Lax')
    
    # Disable server header
    @app.after_request
    def remove_server_header(response: Response) -> Response:
        response.headers.pop('Server', None)
        return response