import argparse
import asyncio
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

from .plugin_api import PLUGIN_ENTRY_POINT_GROUP, DeckhandPlugin, PluginManifest
from .sidecar import DEFAULT_MAX_FRAME_BYTES, SidecarProtocolError, SidecarServer


def _configuration(path: Path) -> dict[str, Any]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise SidecarProtocolError("sidecar configuration must be a mapping")
    return raw


def _plugin(plugin_id: str) -> DeckhandPlugin:
    matches = [
        point for point in entry_points(group=PLUGIN_ENTRY_POINT_GROUP) if point.name == plugin_id
    ]
    if len(matches) != 1:
        raise SidecarProtocolError("sidecar requires exactly one matching plugin distribution")
    point = matches[0]
    loaded = point.load()
    plugin = loaded() if callable(loaded) else loaded
    if not isinstance(plugin, DeckhandPlugin):
        raise SidecarProtocolError("sidecar entry point does not implement DeckhandPlugin")
    manifest = PluginManifest.model_validate(plugin.manifest)
    if point.dist is not None and point.dist.version != manifest.version:
        raise SidecarProtocolError("sidecar distribution and manifest versions do not match")
    return plugin


async def _serve(arguments: argparse.Namespace) -> None:
    plugin = _plugin(arguments.plugin_id)
    manifest = PluginManifest.model_validate(plugin.manifest)
    configuration = _configuration(arguments.config_path)
    errors = sorted(
        Draft202012Validator(manifest.config_schema).iter_errors(configuration),
        key=lambda item: list(item.path),
    )
    if errors:
        raise SidecarProtocolError("sidecar plugin configuration is invalid")
    server = SidecarServer(
        plugin=plugin,
        config=configuration,
        artifact_path=arguments.artifact_path,
        socket_path=arguments.socket_path,
        broker_uid=arguments.broker_uid,
        max_frame_bytes=arguments.max_frame_bytes,
        max_artifact_bytes=arguments.max_artifact_bytes,
    )
    listener = await server.start()
    async with listener:
        await listener.serve_forever()


def run() -> None:
    parser = argparse.ArgumentParser(description="Run one Deckhand plugin in an isolated sidecar")
    parser.add_argument("--plugin-id", required=True)
    parser.add_argument("--config-path", required=True, type=Path)
    parser.add_argument("--artifact-path", required=True, type=Path)
    parser.add_argument("--socket-path", required=True, type=Path)
    parser.add_argument("--broker-uid", required=True, type=int)
    parser.add_argument("--max-frame-bytes", type=int, default=DEFAULT_MAX_FRAME_BYTES)
    parser.add_argument("--max-artifact-bytes", type=int, default=134_217_728)
    arguments = parser.parse_args()
    try:
        asyncio.run(_serve(arguments))
    except Exception:
        raise SystemExit("Deckhand sidecar failed secure startup") from None


if __name__ == "__main__":
    run()
