from pathlib import Path
from urllib.parse import urlparse

import yaml
from pydantic import Field, field_validator

from .models import StrictModel


class StatusEndpoint(StrictModel):
    base_url: str
    health_path: str = "/"
    authorization_file: Path | None = None
    verify_tls: bool = True
    timeout_seconds: float = Field(default=3.0, gt=0, le=30)
    stale_after_seconds: int = Field(default=30, ge=1, le=3600)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("base_url must be an absolute HTTP(S) URL")
        if parsed.username or parsed.password:
            raise ValueError("credentials are not allowed in base_url")
        if parsed.query or parsed.fragment:
            raise ValueError("base_url must not include query or fragment")
        return value.rstrip("/")

    @field_validator("health_path")
    @classmethod
    def validate_health_path(cls, value: str) -> str:
        if not value.startswith("/") or ".." in value or "?" in value or "#" in value:
            raise ValueError("health_path must be a fixed absolute path")
        return value


class Inventory(StrictModel):
    schema_version: int = 1
    status_endpoints: dict[str, StatusEndpoint] = Field(default_factory=dict)


def load_inventory(path: Path) -> Inventory:
    if not path.exists():
        return Inventory()
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("inventory must be a mapping")
    public_fields = {"schema_version", "status_endpoints"}
    return Inventory.model_validate(
        {key: value for key, value in raw.items() if key in public_fields}
    )
