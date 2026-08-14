from pathlib import Path
from typing import Any

import pytest
from deckhand.plugin_api import PluginContext, PluginContribution, PluginManifest
from deckhand.plugins import (
    PluginActivation,
    PluginConfiguration,
    PluginError,
    PluginLock,
    PluginLockEntry,
    PluginManager,
    default_plugin_configuration,
    default_plugin_lock,
    load_plugin_configuration,
)


def test_builtin_plugin_uses_the_locked_manifest() -> None:
    loaded = PluginManager(external_entry_points={}).load(
        default_plugin_configuration(), default_plugin_lock(), allow_external=False
    )
    assert [manifest.id for manifest in loaded.manifests] == ["dh-core"]
    assert loaded.adapters.get("dh-core.fake") is not None
    assert loaded.status.providers == {}


def test_enabled_plugin_must_be_locked() -> None:
    configuration = PluginConfiguration(plugins={"dh-core": PluginActivation()})
    with pytest.raises(PluginError, match="not version-locked"):
        PluginManager(external_entry_points={}).load(
            configuration, PluginLock(), allow_external=False
        )


def test_plugin_configuration_is_validated_before_build() -> None:
    configuration = PluginConfiguration(
        plugins={"dh-core": PluginActivation(config={"endpoint": "https://example.invalid"})}
    )
    with pytest.raises(PluginError, match="invalid configuration"):
        PluginManager(external_entry_points={}).load(
            configuration, default_plugin_lock(), allow_external=False
        )


def test_locked_version_must_match_manifest() -> None:
    lock = PluginLock(plugins=[PluginLockEntry(id="dh-core", version="9.9.9", source="builtin")])
    with pytest.raises(PluginError, match="does not match lock"):
        PluginManager(external_entry_points={}).load(
            default_plugin_configuration(), lock, allow_external=False
        )


def test_external_plugins_are_fail_closed() -> None:
    configuration = PluginConfiguration(plugins={"dh-example": PluginActivation()})
    lock = PluginLock(plugins=[PluginLockEntry(id="dh-example", version="1.0.0", source="python")])
    with pytest.raises(PluginError, match="ALLOW_EXTERNAL_PLUGINS"):
        PluginManager(external_entry_points={}).load(configuration, lock, allow_external=False)


def test_missing_configuration_defaults_to_core(tmp_path: Path) -> None:
    loaded = load_plugin_configuration(tmp_path / "missing.yaml")
    assert list(loaded.plugins) == ["dh-core"]


class IncompleteAdapter:
    async def plan(self, action: Any, request: Any) -> Any:
        return None


class IncompletePlugin:
    @property
    def manifest(self) -> PluginManifest:
        return PluginManifest(
            id="dh-incomplete",
            name="Incomplete",
            version="1.0.0",
            description="Test fixture missing lifecycle methods.",
            adapters=["dh-incomplete.adapter"],
        )

    def build(self, context: PluginContext) -> PluginContribution:
        del context
        return PluginContribution(adapters={"dh-incomplete.adapter": IncompleteAdapter()})  # type: ignore[dict-item]


def test_incomplete_adapter_lifecycle_is_rejected() -> None:
    manager = PluginManager(
        builtin_factories={"dh-incomplete": IncompletePlugin}, external_entry_points={}
    )
    with pytest.raises(PluginError, match="complete lifecycle contract"):
        manager.load(
            PluginConfiguration(plugins={"dh-incomplete": PluginActivation()}),
            PluginLock(
                plugins=[PluginLockEntry(id="dh-incomplete", version="1.0.0", source="builtin")]
            ),
            allow_external=False,
        )
