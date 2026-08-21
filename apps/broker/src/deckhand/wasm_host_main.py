"""``deckhand-wasm-host`` — the out-of-process WASM host (ADR-0005, Phase 4).

This is the second boundary. The in-process ``wasm`` tier (``ganglion.py``) runs
the ``gang`` sandbox inside the broker: a single boundary, fine for dev/read-only.
Mutation-capable wasm plugins need a separate-UID process around the sandbox, and
this entry point is it.

It builds a ``GanglionClient`` for one signed WASM capability, describes it, wraps
that description as a ``DeckhandPlugin``, and serves it over the peer-authenticated
Unix-socket host transport (``WasmHostServer``: peer-auth via ``SO_PEERCRED``,
length-prefixed JSON frames). No new protocol. Run under its own UID and the
hardened systemd unit, the component then executes behind a **double boundary**:
the Wasmtime no-ambient-authority sandbox *inside* a separate-UID process. The
broker reaches it with the ordinary ``WasmHostClient``.

Wasm plugins carry no broker-side config (their config lives in the signed
manifest), so the served ``WasmHostServer`` config is always empty.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from .ganglion import GanglionClient, WasmConnection, WasmDescription, wasm_contribution
from .plugin_api import PluginContext, PluginContribution, PluginManifest
from .wasm_host_transport import DEFAULT_MAX_FRAME_BYTES, WasmHostServer


class _WasmHostPlugin:
    """A ``DeckhandPlugin`` whose contribution is one signed WASM component.

    ``manifest`` echoes the component's self-described manifest and ``build``
    returns the shared ``wasm_contribution`` — the exact same binding the
    in-process broker path uses — so the served plugin is identical to the dev
    path, only now behind the process boundary.
    """

    def __init__(self, client: GanglionClient, description: WasmDescription) -> None:
        self._client = client
        self._description = description

    @property
    def manifest(self) -> PluginManifest:
        return self._description.manifest

    def build(self, context: PluginContext) -> PluginContribution:
        # Wasm plugins take no broker-side config; the host is always served with
        # an empty config (see module docstring and _wasm's config rejection).
        del context
        return wasm_contribution(self._client, self._description)


async def _serve(arguments: argparse.Namespace) -> None:
    connection = WasmConnection(
        gang_binary=arguments.gang_binary,
        data_dir=arguments.data_dir,
        robot=arguments.robot,
        capability=arguments.capability,
        invoke_timeout_seconds=arguments.invoke_timeout_seconds,
    )
    client = GanglionClient(connection)
    description = await client.describe()
    plugin = _WasmHostPlugin(client, description)
    server = WasmHostServer(
        plugin=plugin,
        config={},
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
    parser = argparse.ArgumentParser(
        description="Run one signed Deckhand WASM capability in an out-of-process host"
    )
    parser.add_argument("--socket-path", required=True, type=Path)
    parser.add_argument("--broker-uid", required=True, type=int)
    parser.add_argument("--artifact-path", required=True, type=Path)
    parser.add_argument("--gang-binary", default="gang")
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--robot", required=True)
    parser.add_argument("--capability", required=True)
    parser.add_argument("--invoke-timeout-seconds", type=float, default=20.0)
    parser.add_argument("--max-frame-bytes", type=int, default=DEFAULT_MAX_FRAME_BYTES)
    parser.add_argument("--max-artifact-bytes", type=int, default=134_217_728)
    arguments = parser.parse_args()
    try:
        asyncio.run(_serve(arguments))
    except Exception:
        raise SystemExit("Deckhand wasm host failed secure startup") from None


if __name__ == "__main__":
    run()
