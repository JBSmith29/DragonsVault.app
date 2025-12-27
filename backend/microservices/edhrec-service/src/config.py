from dataclasses import dataclass
import os


@dataclass(frozen=True)
class ServiceConfig:
    service_name: str
    database_url: str
    database_schema: str
    cache_ttl_hours: int
    request_timeout: int
    request_delay: float
    user_agent: str
    http_retries: int
    refresh_concurrency: int


def _int_from_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _float_from_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def load_config() -> ServiceConfig:
    service_name = os.getenv("SERVICE_NAME", "edhrec-service")
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")
    database_schema = os.getenv("DATABASE_SCHEMA", "edhrec_service")
    cache_ttl_hours = _int_from_env("EDHREC_CACHE_TTL_HOURS", 72)
    request_timeout = _int_from_env("EDHREC_REQUEST_TIMEOUT", 30)
    request_delay = _float_from_env("EDHREC_REQUEST_DELAY", 0.0)
    http_retries = _int_from_env("EDHREC_HTTP_RETRIES", 2)
    refresh_concurrency = _int_from_env("EDHREC_REFRESH_CONCURRENCY", 4)
    user_agent = os.getenv(
        "EDHREC_USER_AGENT",
        os.getenv("SCRYFALL_UA", "DragonsVault/6 (+https://dragonsvault.app)"),
    )

    return ServiceConfig(
        service_name=service_name,
        database_url=database_url,
        database_schema=database_schema,
        cache_ttl_hours=cache_ttl_hours,
        request_timeout=request_timeout,
        request_delay=request_delay,
        user_agent=user_agent,
        http_retries=http_retries,
        refresh_concurrency=refresh_concurrency,
    )
