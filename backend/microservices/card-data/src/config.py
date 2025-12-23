from dataclasses import dataclass
import os


@dataclass(frozen=True)
class ServiceConfig:
    service_name: str
    database_url: str
    database_schema: str
    scryfall_data_dir: str
    scryfall_user_agent: str
    scryfall_keep_downloads: bool
    scryfall_timeout: int


def load_config() -> ServiceConfig:
    service_name = os.getenv("SERVICE_NAME", "card-data")
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")
    database_schema = os.getenv("DATABASE_SCHEMA", "card_data")
    scryfall_data_dir = os.getenv("SCRYFALL_DATA_DIR", "/tmp/scryfall")
    scryfall_user_agent = os.getenv(
        "SCRYFALL_UA", "DragonsVault/6 (+https://dragonsvault.app)"
    )
    scryfall_keep_downloads = os.getenv("SCRYFALL_KEEP_DOWNLOADS", "0") == "1"
    scryfall_timeout = int(os.getenv("SCRYFALL_TIMEOUT", "120"))

    return ServiceConfig(
        service_name=service_name,
        database_url=database_url,
        database_schema=database_schema,
        scryfall_data_dir=scryfall_data_dir,
        scryfall_user_agent=scryfall_user_agent,
        scryfall_keep_downloads=scryfall_keep_downloads,
        scryfall_timeout=scryfall_timeout,
    )
