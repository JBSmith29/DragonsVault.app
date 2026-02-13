from dataclasses import dataclass
import os


@dataclass(frozen=True)
class ServiceConfig:
    service_name: str
    database_url: str
    database_schema: str
    rules_version: str
    auth_schema: str
    auth_table: str
    card_data_base_url: str
    shared_secret: str


def _shared_secret() -> str:
    for key in ("GAME_ENGINE_SHARED_SECRET", "ENGINE_SHARED_SECRET", "GAME_ENGINE_SECRET"):
        value = os.getenv(key)
        if value:
            return value.strip()
    return ""


def load_config() -> ServiceConfig:
    service_name = os.getenv("SERVICE_NAME", "game-engine")
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")
    database_schema = os.getenv("DATABASE_SCHEMA", "game_engine")
    rules_version = os.getenv("RULES_VERSION", "v1")
    auth_schema = os.getenv("AUTH_SCHEMA", "public")
    auth_table = os.getenv("AUTH_TABLE", "users")
    card_data_base_url = os.getenv("CARD_DATA_BASE_URL", "http://card-data:5000/v1")
    shared_secret = _shared_secret()

    return ServiceConfig(
        service_name=service_name,
        database_url=database_url,
        database_schema=database_schema,
        rules_version=rules_version,
        auth_schema=auth_schema,
        auth_table=auth_table,
        card_data_base_url=card_data_base_url,
        shared_secret=shared_secret,
    )
