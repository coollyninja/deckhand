from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DECKHAND_", extra="forbid")

    bind_host: str = "127.0.0.1"
    bind_port: int = Field(default=19470, ge=1024, le=65535)
    database_path: Path = Path("deckhand.db")
    catalog_path: Path = Path("packages/catalog/actions")
    trusted_proxy: bool = False
    allow_mutations: bool = False
