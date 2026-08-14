from .catalog import Catalog
from .config import Settings
from .plugins import (
    LoadedPlugins,
    PluginError,
    PluginManager,
    load_plugin_configuration,
    load_plugin_lock,
)


def load_extensions(settings: Settings) -> LoadedPlugins:
    return PluginManager().load(
        load_plugin_configuration(settings.plugin_config_path),
        load_plugin_lock(settings.plugin_lock_path),
        allow_external=settings.allow_external_plugins,
    )


def load_catalog(settings: Settings, extensions: LoadedPlugins) -> Catalog:
    catalog = Catalog.from_path(settings.catalog_path, additional=extensions.actions)
    plugin_ids = {manifest.id for manifest in extensions.manifests}
    for action in catalog.list_actions():
        if action.plugin not in plugin_ids:
            raise PluginError(
                f"action {action.id!r} requires disabled or missing plugin {action.plugin!r}"
            )
        extensions.adapters.get(action.adapter)
    return catalog
