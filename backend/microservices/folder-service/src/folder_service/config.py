from dataclasses import dataclass
import os


@dataclass(frozen=True)
class ServiceConfig:
    service_name: str
    database_url: str
    database_schema: str


def load_config() -> ServiceConfig:
    service_name = os.getenv("SERVICE_NAME", "folder-service")
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")
    database_schema = os.getenv("DATABASE_SCHEMA", "folder_service")

    return ServiceConfig(
        service_name=service_name,
        database_url=database_url,
        database_schema=database_schema,
    )
