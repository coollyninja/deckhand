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
    # Primary identity boundary: the broker verifies an Ed25519-signed identity
    # token (channel/subject/device/exp/nonce) minted by a trusted local issuer.
    # When set, this supersedes the shared-assertion header path.
    identity_public_key_file: Path | None = None
    # Backward-compatible fallback: accept the legacy shared-assertion + trusted
    # headers path. Off by default so a fresh deployment is signed-token only.
    allow_legacy_proxy_assertion: bool = False
    tailscale_app_capability: str = "coollyninja.com/cap/deckhand"
    allow_mutations: bool = False
    worker_id: str = "deckhand-worker-1"
    confirmation_ttl_seconds: int = Field(default=60, ge=10, le=300)
    # A confirmed intent that has waited longer than this in QUEUED is expired
    # rather than executed arbitrarily late (e.g. worker was down at submit time).
    queue_ttl_seconds: int = Field(default=900, ge=60, le=86400)
    # Bound how many times the reconciler will re-attempt one UNKNOWN_OUTCOME job
    # before parking it for operator attention, so a permanently-unresolvable job
    # cannot churn the audit log forever.
    reconcile_max_attempts: int = Field(default=10, ge=1, le=1000)
