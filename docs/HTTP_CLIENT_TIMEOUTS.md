# HTTP Client Timeout Configuration

## Overview

All external HTTP requests in DragonsVault are configured with timeouts to prevent hanging requests and improve reliability. This document describes the timeout strategy and configuration.

## Timeout Values

Timeouts are specified as tuples of `(connect_timeout, read_timeout)` in seconds:

- **connect_timeout**: Maximum time to establish a connection
- **read_timeout**: Maximum time to wait for response data

### Default Timeouts by Service

| Service | Connect | Read | Total | Use Case |
|---------|---------|------|-------|----------|
| Default | 5s | 30s | 35s | Generic external APIs |
| Scryfall | 5s | 10s | 15s | Fast card data API |
| EDHREC | 5s | 30s | 35s | Deck recommendation service |
| Price Service | 3s | 15s | 18s | Internal microservice |
| External | 10s | 60s | 70s | Slow external services |

## Usage

### Using the Centralized HTTP Client

```python
from shared.http_client import safe_get, safe_post, get_timeout

# Simple GET with default timeout
response = safe_get("https://api.example.com/data")

# GET with service-specific timeout
response = safe_get(
    "https://api.scryfall.com/cards/123",
    timeout=get_timeout("scryfall")
)

# POST with custom timeout
response = safe_post(
    "https://api.example.com/submit",
    json={"data": "value"},
    timeout=(5, 20)  # 5s connect, 20s read
)
```

### Using Requests Directly

If you need to use `requests` directly, always specify a timeout:

```python
import requests

# Good: Explicit timeout
response = requests.get(url, timeout=(5, 30))

# Bad: No timeout (can hang indefinitely)
response = requests.get(url)  # DON'T DO THIS
```

### Using Session with Retries

For services that may have transient failures:

```python
from shared.http_client import create_session_with_retries, get_timeout

session = create_session_with_retries(
    retries=3,
    backoff_factor=0.3,
    status_forcelist=(500, 502, 503, 504)
)

response = session.get(
    "https://api.example.com/data",
    timeout=get_timeout("external")
)
```

## Current Timeout Locations

### External API Calls

1. **Scryfall API** (`backend/core/domains/cards/services/scryfall_print_service.py`)
   - Timeout: 6s (should be updated to use `get_timeout("scryfall")`)
   - Used for: Card data lookups

2. **EDHREC Service** (`backend/core/domains/decks/services/edhrec_client.py`)
   - Timeout: Configured via `_edhrec_service_timeout()`
   - Used for: Deck recommendations

3. **Price Service** (`backend/core/domains/cards/services/pricing.py`)
   - Timeout: Configured via `_price_service_timeout()`
   - Used for: Card pricing data

4. **hCaptcha Verification** (`backend/core/domains/users/routes/auth.py`)
   - Timeout: 5s
   - Used for: Registration captcha verification

5. **Proxy Deck Downloads** (`backend/core/domains/decks/services/proxy_decks.py`)
   - Timeout: 10-12s
   - Used for: Importing decks from external sites

6. **Symbol Cache** (`backend/core/shared/utils/symbols_cache.py`)
   - Timeout: 30s
   - Used for: Downloading mana symbols

7. **Rules Cache** (`backend/core/shared/utils/rules_cache.py`)
   - Timeout: 30s
   - Used for: Downloading MTG comprehensive rules

## Best Practices

### 1. Always Set Timeouts

Never make HTTP requests without a timeout. Hanging requests can exhaust connection pools and cause cascading failures.

```python
# Bad
response = requests.get(url)

# Good
response = requests.get(url, timeout=(5, 30))
```

### 2. Choose Appropriate Timeout Values

- **Fast internal services**: 3-5s total
- **External APIs**: 10-30s total
- **Large file downloads**: 60s+ total
- **Real-time user requests**: Keep under 30s total

### 3. Handle Timeout Exceptions

```python
import requests

try:
    response = requests.get(url, timeout=(5, 30))
    response.raise_for_status()
except requests.Timeout:
    # Handle timeout specifically
    logger.warning("Request timed out: %s", url)
    return None
except requests.RequestException as exc:
    # Handle other request errors
    logger.error("Request failed: %s", exc)
    return None
```

### 4. Use Retries for Transient Failures

For services with occasional failures, use retry logic:

```python
from shared.http_client import create_session_with_retries

session = create_session_with_retries(retries=3)
response = session.get(url, timeout=(5, 30))
```

### 5. Log Slow Requests

Monitor request latency to identify performance issues:

```python
import time
import logging

logger = logging.getLogger(__name__)

start = time.monotonic()
try:
    response = requests.get(url, timeout=(5, 30))
    latency = time.monotonic() - start
    if latency > 5.0:
        logger.warning("Slow request: %s took %.2fs", url, latency)
except requests.Timeout:
    logger.error("Request timeout after %.2fs: %s", time.monotonic() - start, url)
```

## Environment Variables

Timeout values can be overridden via environment variables:

```bash
# Example: Increase EDHREC timeout for slow networks
EDHREC_SERVICE_TIMEOUT=60

# Example: Decrease price service timeout for fast internal network
PRICE_SERVICE_TIMEOUT=5
```

## Monitoring

### Metrics to Track

1. **Request latency**: P50, P95, P99 response times
2. **Timeout rate**: Percentage of requests that timeout
3. **Retry rate**: Percentage of requests that require retries
4. **Error rate**: Percentage of failed requests

### Alerting Thresholds

- **Warning**: Timeout rate > 1%
- **Critical**: Timeout rate > 5%
- **Warning**: P95 latency > 10s
- **Critical**: P95 latency > 30s

## Troubleshooting

### Requests Timing Out

1. Check network connectivity to the service
2. Verify the service is responding (use curl/httpie)
3. Check if the service is rate-limiting requests
4. Consider increasing timeout if service is legitimately slow
5. Implement circuit breaker pattern for failing services

### Requests Too Slow

1. Check if timeout is too generous
2. Verify service performance (may need optimization)
3. Consider caching responses
4. Implement request coalescing for duplicate requests

## Migration Checklist

To migrate existing code to use centralized timeouts:

- [ ] Import `safe_get`/`safe_post` from `shared.http_client`
- [ ] Replace `requests.get()` with `safe_get()`
- [ ] Replace `requests.post()` with `safe_post()`
- [ ] Use `get_timeout(service)` for service-specific timeouts
- [ ] Add timeout exception handling
- [ ] Add request latency logging
- [ ] Update tests to mock timeout scenarios

## References

- [Requests Timeouts Documentation](https://requests.readthedocs.io/en/latest/user/advanced/#timeouts)
- [urllib3 Retry Documentation](https://urllib3.readthedocs.io/en/stable/reference/urllib3.util.html#urllib3.util.Retry)
- [Python Socket Timeout](https://docs.python.org/3/library/socket.html#socket.socket.settimeout)
