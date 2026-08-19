import re
from pathlib import Path
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


_ALIAS_VALUE = re.compile(r"^[A-Za-z0-9 ._-]{1,128}$")


class ObsSettings(StrictModel):
    """Connection to a local OBS instance via obs-websocket (v5)."""

    host: str = "127.0.0.1"
    port: int = Field(default=4455, ge=1, le=65535)
    password_file: Path | None = None
    scenes: dict[str, str] = Field(default_factory=dict)
    sources: dict[str, str] = Field(default_factory=dict)

    @field_validator("scenes", "sources")
    @classmethod
    def validate_names(cls, value: dict[str, str]) -> dict[str, str]:
        if any(not _ALIAS_VALUE.fullmatch(name) for name in value.values()):
            raise ValueError("OBS scene/source names contain unsupported characters")
        return value


class MacInventory(StrictModel):
    apps: dict[str, str] = Field(default_factory=dict)
    browser_sessions: dict[str, str] = Field(default_factory=dict)
    shortcuts: dict[str, str] = Field(default_factory=dict)
    # Audio devices, keyed by logical alias → the device name macOS reports.
    audio_inputs: dict[str, str] = Field(default_factory=dict)
    audio_outputs: dict[str, str] = Field(default_factory=dict)
    # Focus modes, keyed by alias → the exact Focus name (or a Shortcut name that
    # sets it, since macOS has no first-party Focus CLI).
    focus_modes: dict[str, str] = Field(default_factory=dict)
    # Pomodoro routines, keyed by alias → a Shortcut name that starts the routine.
    pomodoro_routines: dict[str, str] = Field(default_factory=dict)
    # Display-mode layouts, keyed by alias → a Shortcut name (or displayplacer
    # profile) that applies the layout.
    display_modes: dict[str, str] = Field(default_factory=dict)
    # Dev workspaces, keyed by alias → an absolute path to open (vault/project).
    workspaces: dict[str, str] = Field(default_factory=dict)
    obs: ObsSettings = Field(default_factory=ObsSettings)

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

    @field_validator("audio_inputs", "audio_outputs", "focus_modes")
    @classmethod
    def validate_device_names(cls, value: dict[str, str]) -> dict[str, str]:
        if any(not _ALIAS_VALUE.fullmatch(name) for name in value.values()):
            raise ValueError("device/mode names contain unsupported characters")
        return value

    @field_validator("shortcuts", "pomodoro_routines", "display_modes")
    @classmethod
    def validate_shortcut_names(cls, value: dict[str, str]) -> dict[str, str]:
        if any(not _ALIAS_VALUE.fullmatch(name) for name in value.values()):
            raise ValueError("shortcut names contain unsupported characters")
        return value

    @field_validator("workspaces")
    @classmethod
    def validate_workspace_paths(cls, value: dict[str, str]) -> dict[str, str]:
        for path in value.values():
            if not path.startswith("/") and not path.startswith("~"):
                raise ValueError("workspace values must be absolute paths")
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
