from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from importlib.metadata import EntryPoint, entry_points
from pathlib import Path
from typing import Any, Literal

import yaml
from jsonschema import Draft202012Validator
from pydantic import Field, model_validator

from .adapters import Adapter, AdapterRegistry, DisabledMutationAdapter, FakeAdapter
from .models import ActionDefinition, StrictModel
from .plugin_api import (
    PLUGIN_API_VERSION,
    PLUGIN_ENTRY_POINT_GROUP,
    DeckhandPlugin,
    PluginContext,
    PluginContribution,
    PluginManifest,
    PluginPermissions,
)
from .resilience import (
    ResilienceGuard,
    ResiliencePolicy,
    ResilientAdapter,
    ResilientStatusProvider,
)
from .sidecar import SidecarAdapter, SidecarClient, SidecarConnection, SidecarStatusProvider
from .status import StatusAggregator, StatusProvider


class PluginError(RuntimeError):
    pass


class PluginRuntime(ResiliencePolicy):
    mode: Literal["in_process", "sidecar"] = "in_process"
    sidecar: SidecarConnection | None = None

    @model_validator(mode="after")
    def validate_isolation_configuration(self) -> PluginRuntime:
        if self.mode == "sidecar" and self.sidecar is None:
            raise ValueError("sidecar runtime requires sidecar connection settings")
        if self.mode == "in_process" and self.sidecar is not None:
            raise ValueError("in-process runtime cannot declare sidecar settings")
        return self


class PluginActivation(StrictModel):
    enabled: bool = True
    config: dict[str, Any] = Field(default_factory=dict)
    runtime: PluginRuntime = Field(default_factory=PluginRuntime)


class PluginConfiguration(StrictModel):
    schema_version: int = 1
    plugins: dict[str, PluginActivation] = Field(default_factory=dict)


class PluginLockEntry(StrictModel):
    id: str = Field(pattern=r"^dh-[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
    version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?$")
    source: Literal["builtin", "python", "sidecar"]
    digest: str | None = Field(default=None, pattern=r"^sha256:[a-f0-9]{64}$")


class PluginLock(StrictModel):
    schema_version: int = 1
    plugins: list[PluginLockEntry] = Field(default_factory=list)

    def by_id(self) -> dict[str, PluginLockEntry]:
        result = {plugin.id: plugin for plugin in self.plugins}
        if len(result) != len(self.plugins):
            raise PluginError("plugin lock contains duplicate IDs")
        return result


@dataclass(frozen=True)
class LoadedPlugins:
    manifests: tuple[PluginManifest, ...]
    adapters: AdapterRegistry
    status: StatusAggregator
    actions: tuple[ActionDefinition, ...]
    resilience: Mapping[str, ResilienceGuard]


PluginFactory = Callable[[], DeckhandPlugin]


class CorePlugin:
    @property
    def manifest(self) -> PluginManifest:
        return PluginManifest(
            id="dh-core",
            name="Deckhand Core Development Adapter",
            version="0.5.0",
            description="Topology-neutral deterministic adapters for development and tests.",
            adapters=["dh-core.fake", "dh-core.disabled"],
            permissions=PluginPermissions(mutation=False),
        )

    def build(self, context: PluginContext) -> PluginContribution:
        del context
        return PluginContribution(
            adapters={
                "dh-core.fake": FakeAdapter(),
                "dh-core.disabled": DisabledMutationAdapter("disabled"),
            }
        )


BUILTIN_PLUGIN_FACTORIES: Mapping[str, PluginFactory] = {"dh-core": CorePlugin}


def default_plugin_configuration() -> PluginConfiguration:
    return PluginConfiguration(plugins={"dh-core": PluginActivation()})


def default_plugin_lock() -> PluginLock:
    return PluginLock(plugins=[PluginLockEntry(id="dh-core", version="0.5.0", source="builtin")])


def _load_yaml_mapping(path: Path, description: str) -> dict[str, Any]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise PluginError(f"{description} must be a mapping")
    return raw


def load_plugin_configuration(path: Path) -> PluginConfiguration:
    if not path.exists():
        return default_plugin_configuration()
    return PluginConfiguration.model_validate(_load_yaml_mapping(path, "plugin configuration"))


def load_plugin_lock(path: Path) -> PluginLock:
    if not path.exists():
        return default_plugin_lock()
    return PluginLock.model_validate(_load_yaml_mapping(path, "plugin lock"))


class PluginManager:
    def __init__(
        self,
        *,
        builtin_factories: Mapping[str, PluginFactory] | None = None,
        external_entry_points: Mapping[str, EntryPoint] | None = None,
    ) -> None:
        self._builtin_factories = dict(builtin_factories or BUILTIN_PLUGIN_FACTORIES)
        self._external_entry_points = dict(
            external_entry_points if external_entry_points is not None else self._discover()
        )

    @staticmethod
    def _discover() -> dict[str, EntryPoint]:
        discovered: dict[str, EntryPoint] = {}
        for point in entry_points(group=PLUGIN_ENTRY_POINT_GROUP):
            if point.name in discovered:
                raise PluginError(f"multiple installed distributions provide {point.name!r}")
            discovered[point.name] = point
        return discovered

    def load(
        self,
        configuration: PluginConfiguration,
        lock: PluginLock,
        *,
        allow_external: bool,
        allow_sidecars: bool = False,
    ) -> LoadedPlugins:
        locked = lock.by_id()
        manifests: list[PluginManifest] = []
        adapters: dict[str, Adapter] = {}
        status_providers: dict[str, StatusProvider] = {}
        actions: list[ActionDefinition] = []
        resilience: dict[str, ResilienceGuard] = {}

        for plugin_id, activation in sorted(configuration.plugins.items()):
            if not activation.enabled:
                continue
            lock_entry = locked.get(plugin_id)
            if lock_entry is None:
                raise PluginError(f"enabled plugin {plugin_id!r} is not version-locked")
            in_process_plugin: DeckhandPlugin | None = None
            if lock_entry.source == "sidecar":
                manifest, contribution = self._sidecar(
                    plugin_id,
                    activation,
                    lock_entry,
                    allow_sidecars=allow_sidecars,
                )
            else:
                if activation.runtime.mode != "in_process":
                    raise PluginError(
                        f"plugin {plugin_id!r} lock source does not permit sidecar isolation"
                    )
                in_process_plugin = self._instantiate(lock_entry, allow_external=allow_external)
                manifest = PluginManifest.model_validate(in_process_plugin.manifest)
            self._validate_manifest(plugin_id, manifest, lock_entry)
            if lock_entry.source != "sidecar":
                errors = sorted(
                    Draft202012Validator(manifest.config_schema).iter_errors(activation.config),
                    key=lambda item: list(item.path),
                )
                if errors:
                    detail = "; ".join(error.message for error in errors)
                    raise PluginError(f"invalid configuration for {plugin_id}: {detail}")
                if in_process_plugin is None:
                    raise PluginError(f"plugin {plugin_id!r} was not instantiated")
                contribution = in_process_plugin.build(PluginContext(config=activation.config))
            guard = ResilienceGuard(plugin_id, activation.runtime)
            self._merge(
                plugin_id,
                manifest,
                contribution,
                adapters,
                status_providers,
                actions,
                guard,
            )
            resilience[plugin_id] = guard
            manifests.append(manifest)

        return LoadedPlugins(
            manifests=tuple(manifests),
            adapters=AdapterRegistry(adapters),
            status=StatusAggregator(status_providers),
            actions=tuple(actions),
            resilience=resilience,
        )

    @staticmethod
    def _sidecar(
        plugin_id: str,
        activation: PluginActivation,
        lock: PluginLockEntry,
        *,
        allow_sidecars: bool,
    ) -> tuple[PluginManifest, PluginContribution]:
        if not allow_sidecars:
            raise PluginError(
                f"sidecar plugin {plugin_id!r} requires DECKHAND_ALLOW_SIDECAR_PLUGINS=true"
            )
        if activation.runtime.mode != "sidecar" or activation.runtime.sidecar is None:
            raise PluginError(f"sidecar plugin {plugin_id!r} requires sidecar runtime settings")
        if activation.config:
            raise PluginError(
                f"sidecar plugin {plugin_id!r} configuration must be delivered to the sidecar"
            )
        if lock.digest is None:
            raise PluginError(f"sidecar plugin {plugin_id!r} requires an artifact digest")
        try:
            client = SidecarClient(plugin_id, activation.runtime.sidecar, lock.digest)
            handshake = client.handshake()
        except (OSError, ValueError, RuntimeError) as error:
            raise PluginError(f"sidecar plugin {plugin_id!r} failed secure handshake") from error
        return (
            handshake.manifest,
            PluginContribution(
                adapters={
                    name: SidecarAdapter(name, client) for name in handshake.manifest.adapters
                },
                status_providers={
                    name: SidecarStatusProvider(name, client) for name in handshake.status_providers
                },
                actions=tuple(handshake.actions),
            ),
        )

    def _instantiate(self, lock: PluginLockEntry, *, allow_external: bool) -> DeckhandPlugin:
        if lock.source == "builtin":
            try:
                return self._builtin_factories[lock.id]()
            except KeyError as error:
                raise PluginError(f"locked builtin plugin {lock.id!r} is unavailable") from error
        if not allow_external:
            raise PluginError(
                f"external plugin {lock.id!r} requires DECKHAND_ALLOW_EXTERNAL_PLUGINS=true"
            )
        try:
            point = self._external_entry_points[lock.id]
        except KeyError as error:
            raise PluginError(f"locked Python plugin {lock.id!r} is not installed") from error
        distribution = point.dist
        if distribution is not None and distribution.version != lock.version:
            raise PluginError(
                f"installed distribution for {lock.id!r} is {distribution.version}, "
                f"but the lock requires {lock.version}"
            )
        loaded = point.load()
        plugin = loaded() if callable(loaded) else loaded
        if not isinstance(plugin, DeckhandPlugin):
            raise PluginError(f"entry point {lock.id!r} does not implement DeckhandPlugin")
        return plugin

    @staticmethod
    def _validate_manifest(
        expected_id: str, manifest: PluginManifest, lock: PluginLockEntry
    ) -> None:
        if manifest.id != expected_id:
            raise PluginError(f"plugin manifest ID {manifest.id!r} does not match {expected_id!r}")
        if manifest.version != lock.version:
            raise PluginError(
                f"plugin {expected_id!r} version {manifest.version} "
                f"does not match lock {lock.version}"
            )
        if manifest.api_version != PLUGIN_API_VERSION:
            raise PluginError(
                f"plugin {expected_id!r} requires unsupported API {manifest.api_version}"
            )

    @staticmethod
    def _merge(
        plugin_id: str,
        manifest: PluginManifest,
        contribution: PluginContribution,
        adapters: dict[str, Adapter],
        status_providers: dict[str, StatusProvider],
        actions: list[ActionDefinition],
        guard: ResilienceGuard,
    ) -> None:
        if set(contribution.adapters) != set(manifest.adapters):
            raise PluginError(f"plugin {plugin_id!r} adapter contribution differs from manifest")
        undeclared_actions = {action.id for action in contribution.actions} - set(manifest.actions)
        if undeclared_actions:
            raise PluginError(
                f"plugin {plugin_id!r} contributed undeclared actions: {sorted(undeclared_actions)}"
            )
        for action in contribution.actions:
            if action.plugin != plugin_id:
                raise PluginError(f"action {action.id!r} declares the wrong plugin")
            if action.adapter not in contribution.adapters:
                raise PluginError(f"action {action.id!r} references an unowned adapter")
        for name, adapter in contribution.adapters.items():
            if name in adapters:
                raise PluginError(f"adapter {name!r} is contributed more than once")
            if not isinstance(adapter, Adapter):
                raise PluginError(
                    f"adapter {name!r} does not implement the complete lifecycle contract"
                )
            adapters[name] = ResilientAdapter(adapter, guard)
        for name, provider in contribution.status_providers.items():
            if name in status_providers:
                raise PluginError(f"status provider {name!r} is contributed more than once")
            if not isinstance(provider, StatusProvider):
                raise PluginError(f"status provider {name!r} does not implement observe")
            status_providers[name] = ResilientStatusProvider(provider, guard)
        known_actions = {action.id for action in actions}
        if any(action.id in known_actions for action in contribution.actions):
            raise PluginError(f"plugin {plugin_id!r} contributes a duplicate action ID")
        actions.extend(contribution.actions)
