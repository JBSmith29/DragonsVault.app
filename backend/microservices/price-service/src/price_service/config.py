from dataclasses import dataclass
import os
from typing import Tuple


def _split_pref(value: str | None, default: Tuple[str, ...]) -> Tuple[str, ...]:
    if not value:
        return default
    items = [item.strip().lower() for item in value.split(",") if item.strip()]
    return tuple(items) if items else default


@dataclass(frozen=True)
class ServiceConfig:
    service_name: str
    database_url: str
    database_schema: str
    mtgjson_graphql_url: str
    mtgjson_api_token: str | None
    user_agent: str
    request_timeout: int
    cache_ttl_seconds: int
    provider_preference: Tuple[str, ...]
    list_type_preference: Tuple[str, ...]


def load_config() -> ServiceConfig:
    service_name = os.getenv("SERVICE_NAME", "price-service")
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")
    database_schema = os.getenv("DATABASE_SCHEMA", "price_service")
    mtgjson_graphql_url = os.getenv("MTGJSON_GRAPHQL_URL", "https://graphql.mtgjson.com/")
    mtgjson_api_token = (
        os.getenv("MTGJSON_API_TOKEN")
        or os.getenv("MTGJSON_ACCESS_TOKEN")
        or os.getenv("MTGJSON_TOKEN")
    )
    user_agent = os.getenv(
        "PRICE_UA",
        os.getenv("SCRYFALL_UA", "DragonsVault/6 (+https://dragonsvault.app)"),
    )
    request_timeout = int(os.getenv("PRICE_REQUEST_TIMEOUT", "20"))
    cache_ttl_seconds = int(os.getenv("PRICE_CACHE_TTL", "43200"))
    provider_preference = _split_pref(
        os.getenv("PRICE_PROVIDER_PREFERENCE"),
        ("tcgplayer", "cardmarket", "cardkingdom", "mtgstocks"),
    )
    list_type_preference = _split_pref(
        os.getenv("PRICE_LISTTYPE_PREFERENCE"),
        ("retail", "market"),
    )

    return ServiceConfig(
        service_name=service_name,
        database_url=database_url,
        database_schema=database_schema,
        mtgjson_graphql_url=mtgjson_graphql_url,
        mtgjson_api_token=mtgjson_api_token,
        user_agent=user_agent,
        request_timeout=request_timeout,
        cache_ttl_seconds=cache_ttl_seconds,
        provider_preference=provider_preference,
        list_type_preference=list_type_preference,
    )
