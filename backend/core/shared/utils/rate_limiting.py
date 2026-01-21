"""Enhanced rate limiting configuration."""

from __future__ import annotations

from typing import Dict, List

# Rate limiting rules by endpoint pattern
RATE_LIMITS: Dict[str, List[str]] = {
    # Authentication endpoints - strict limits
    'auth': [
        '5 per minute',
        '20 per hour',
        '100 per day'
    ],
    
    # API endpoints - moderate limits
    'api': [
        '60 per minute',
        '1000 per hour'
    ],
    
    # Search endpoints - moderate limits
    'search': [
        '30 per minute',
        '500 per hour'
    ],
    
    # Import/Export - strict limits due to resource usage
    'import_export': [
        '3 per minute',
        '20 per hour',
        '50 per day'
    ],
    
    # Card operations - generous limits
    'cards': [
        '100 per minute',
        '2000 per hour'
    ],
    
    # Default for other endpoints
    'default': [
        '200 per minute',
        '5000 per hour'
    ]
}

# Endpoint patterns to rate limit categories
ENDPOINT_PATTERNS: Dict[str, str] = {
    '/login': 'auth',
    '/register': 'auth',
    '/logout': 'auth',
    '/api/': 'api',
    '/search': 'search',
    '/scryfall': 'search',
    '/import': 'import_export',
    '/export': 'import_export',
    '/cards': 'cards',
    '/decks/proxy': 'import_export',
}


def get_rate_limits_for_endpoint(endpoint: str) -> List[str]:
    """Get rate limits for a specific endpoint."""
    
    # Check for exact matches first
    if endpoint in ENDPOINT_PATTERNS:
        category = ENDPOINT_PATTERNS[endpoint]
        return RATE_LIMITS.get(category, RATE_LIMITS['default'])
    
    # Check for pattern matches
    for pattern, category in ENDPOINT_PATTERNS.items():
        if endpoint.startswith(pattern):
            return RATE_LIMITS.get(category, RATE_LIMITS['default'])
    
    return RATE_LIMITS['default']


def is_rate_limited_endpoint(endpoint: str) -> bool:
    """Check if endpoint should have rate limiting applied."""
    
    # Skip rate limiting for static assets
    static_patterns = ['/static/', '/favicon', '/.well-known/']
    for pattern in static_patterns:
        if endpoint.startswith(pattern):
            return False
    
    return True