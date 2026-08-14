import re
from pathlib import Path
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MacInventory(StrictModel):
    apps: dict[str, str] = Field(default_factory=dict)
    browser_sessions: dict[str, str] = Field(default_factory=dict)
    shortcuts: dict[str, str] = Field(default_factory=dict)

    @field_validator("apps")
    @classmethod
    def validate_apps(cls, value: dict[str, str]) -> dict[str, str]:
        pattern = re.compile(r"^[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$")
        if any(not pattern.fullmatch(bundle_id) for bundle_id in value.values()):
            raise ValueError("app values must be bundle identifiers")
        return value

    @field_validator("browser_sessions")
    @classmethod
    def validate_urls(cls, value: dict[str, str]) -> dict[str, str]:
        for url in value.values():
            parsed = urlparse(url)
            if parsed.scheme not in {"https", "http"} or not parsed.hostname:
                raise ValueError("browser session values must be HTTP(S) URLs")
        return value


class MacSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DECKHAND_MACOS_", extra="forbid")

    bind_host: str = "127.0.0.1"
    bind_port: int = Field(default=19471, ge=1024, le=65535)
    inventory_path: Path = Path("config/macos.yaml")
    token_file: Path


def load_inventory(path: Path) -> MacInventory:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("macOS inventory must be a mapping")
    return MacInventory.model_validate(raw)
