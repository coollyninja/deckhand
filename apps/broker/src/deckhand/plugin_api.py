from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from pydantic import Field, field_validator

from .adapters import Adapter
from .models import ActionDefinition, StatusValue, StrictModel
from .status import StatusProvider

PLUGIN_API_VERSION = 1
PLUGIN_ENTRY_POINT_GROUP = "deckhand.plugins"


class PluginPermissions(StrictModel):
    mutation: bool = False
    credential_slots: list[str] = Field(default_factory=list)
    egress_bindings: list[str] = Field(default_factory=list)


class PluginManifest(StrictModel):
    schema_version: int = 1
    id: str = Field(pattern=r"^dh-[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
    name: str = Field(min_length=1, max_length=128)
    version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?$")
    api_version: int = PLUGIN_API_VERSION
    description: str = Field(min_length=1, max_length=1024)
    adapters: list[str] = Field(default_factory=list)
    status_provider_types: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    permissions: PluginPermissions = Field(default_factory=PluginPermissions)
    config_schema: dict[str, Any] = Field(
        default_factory=lambda: {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "additionalProperties": False,
            "properties": {},
        }
    )

    @field_validator("adapters")
    @classmethod
    def validate_adapter_names(cls, value: list[str], info: Any) -> list[str]:
        plugin_id = info.data.get("id")
        if plugin_id and any(not name.startswith(f"{plugin_id}.") for name in value):
            raise ValueError("adapter IDs must be namespaced by the plugin ID")
        if len(value) != len(set(value)):
            raise ValueError("adapter IDs must be unique")
        return value


@dataclass(frozen=True)
class PluginContext:
    config: Mapping[str, Any]


@dataclass(frozen=True)
class PluginContribution:
    adapters: Mapping[str, Adapter] = field(default_factory=dict)
    status_providers: Mapping[str, StatusProvider] = field(default_factory=dict)
    actions: tuple[ActionDefinition, ...] = ()


@runtime_checkable
class DeckhandPlugin(Protocol):
    @property
    def manifest(self) -> PluginManifest: ...

    def build(self, context: PluginContext) -> PluginContribution: ...


class StaticStatusProvider:
    """Small deterministic provider for plugin conformance tests and development."""

    def __init__(self, value: StatusValue) -> None:
        self.value = value

    async def observe(self) -> StatusValue:
        return self.value
