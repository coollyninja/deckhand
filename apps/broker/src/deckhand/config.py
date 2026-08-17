from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DECKHAND_", extra="forbid")

    bind_host: str = "127.0.0.1"
    bind_port: int = Field(default=19470, ge=1024, le=65535)
    database_path: Path = Path("deckhand.db")
    catalog_path: Path = Path("packages/catalog/actions")
    plugin_config_path: Path = Path("config/plugins.yaml")
    plugin_lock_path: Path = Path("config/plugins.lock.yaml")
    allow_external_plugins: bool = False
    allow_sidecar_plugins: bool = False
    opa_url: str = "http://127.0.0.1:8181"
    opa_decision_path: str = "/v1/data/deckhand/authz/decision"
    trusted_proxy: bool = False
    proxy_assertion_file: Path | None = None
    tailscale_app_capability: str = "coollyninja.com/cap/deckhand"
    allow_mutations: bool = False
    worker_id: str = "deckhand-worker-1"
    confirmation_ttl_seconds: int = Field(default=60, ge=10, le=300)
