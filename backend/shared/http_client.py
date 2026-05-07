"""Centralized HTTP client configuration with timeouts and retry logic.

This module provides standardized timeout values and helper functions for
external API calls to prevent hanging requests and improve reliability.
"""

from __future__ import annotations

import os
from typing import Any, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# Default timeout values (connect_timeout, read_timeout) in seconds
DEFAULT_TIMEOUT = (5, 30)  # 5s to connect, 30s to read
SCRYFALL_TIMEOUT = (5, 10)  # Scryfall is usually fast
EDHREC_TIMEOUT = (5, 30)  # EDHREC can be slower
PRICE_SERVICE_TIMEOUT = (3, 15)  # Internal service, should be fast
EXTERNAL_SERVICE_TIMEOUT = (10, 60)  # Generic external services


def get_timeout(service: str = "default") -> tuple[int, int]:
    """Get timeout configuration for a specific service.
    
    Args:
        service: Service name (default, scryfall, edhrec, price, external)
    
    Returns:
        Tuple of (connect_timeout, read_timeout) in seconds
    """
    timeouts = {
        "default": DEFAULT_TIMEOUT,
        "scryfall": SCRYFALL_TIMEOUT,
        "edhrec": EDHREC_TIMEOUT,
        "price": PRICE_SERVICE_TIMEOUT,
        "external": EXTERNAL_SERVICE_TIMEOUT,
    }
    return timeouts.get(service, DEFAULT_TIMEOUT)


def create_session_with_retries(
    retries: int = 3,
    backoff_factor: float = 0.3,
    status_forcelist: Optional[tuple[int, ...]] = None,
) -> requests.Session:
    """Create a requests Session with retry logic.
    
    Args:
        retries: Number of retry attempts
        backoff_factor: Backoff multiplier (0.3 means 0.3s, 0.6s, 1.2s delays)
        status_forcelist: HTTP status codes to retry on
    
    Returns:
        Configured requests.Session with retry adapter
    """
    if status_forcelist is None:
        status_forcelist = (500, 502, 503, 504)
    
    session = requests.Session()
    retry_strategy = Retry(
        total=retries,
        backoff_factor=backoff_factor,
        status_forcelist=status_forcelist,
        allowed_methods=["HEAD", "GET", "OPTIONS", "POST"],
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def safe_get(
    url: str,
    timeout: Optional[tuple[int, int]] = None,
    **kwargs: Any
) -> requests.Response:
    """Perform a GET request with default timeout.
    
    Args:
        url: URL to request
        timeout: Optional timeout tuple (connect, read). Uses DEFAULT_TIMEOUT if not provided
        **kwargs: Additional arguments passed to requests.get
    
    Returns:
        requests.Response object
    
    Raises:
        requests.RequestException: On request failure
    """
    if timeout is None:
        timeout = DEFAULT_TIMEOUT
    return requests.get(url, timeout=timeout, **kwargs)


def safe_post(
    url: str,
    timeout: Optional[tuple[int, int]] = None,
    **kwargs: Any
) -> requests.Response:
    """Perform a POST request with default timeout.
    
    Args:
        url: URL to request
        timeout: Optional timeout tuple (connect, read). Uses DEFAULT_TIMEOUT if not provided
        **kwargs: Additional arguments passed to requests.post
    
    Returns:
        requests.Response object
    
    Raises:
        requests.RequestException: On request failure
    """
    if timeout is None:
        timeout = DEFAULT_TIMEOUT
    return requests.post(url, timeout=timeout, **kwargs)


__all__ = [
    "DEFAULT_TIMEOUT",
    "SCRYFALL_TIMEOUT",
    "EDHREC_TIMEOUT",
    "PRICE_SERVICE_TIMEOUT",
    "EXTERNAL_SERVICE_TIMEOUT",
    "get_timeout",
    "create_session_with_retries",
    "safe_get",
    "safe_post",
]
