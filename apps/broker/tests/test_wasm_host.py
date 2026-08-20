"""Out-of-process WASM host (``deckhand-wasm-host``) — ADR-0005 Phase 4.

The double boundary: a signed WASM component runs under ``gang``'s no-ambient-
authority sandbox INSIDE a ``deckhand-wasm-host`` process that itself runs under
its own UID and the hardened sidecar systemd unit. The host speaks the EXISTING
ADR-0004 sidecar transport, so the broker reaches it with the ordinary
``SidecarClient`` — indistinguishable from a ``sidecar``-source plugin.

The load-bearing test stands up a real ``deckhand-wasm-host`` ``SidecarServer`` on
a tmp socket whose ``GanglionClient`` uses a FAKE invoker (no ``gang`` binary, no
live component) and runs the IDENTICAL frozen conformance suite through a
``SidecarClient``/``SidecarAdapter`` — it must pass exactly as the in-process wasm
tier and the sidecar tier do.
"""

import asyncio
import base64
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from deckhand.adapters import AdapterError, UnknownOutcome
from deckhand.conformance import assert_adapter_conformance, conformance_request, read_action
from deckhand.ganglion import GanglionClient, WasmConnection
from deckhand.models import RiskClass
from deckhand.plugins import (
    PluginActivation,
    PluginConfiguration,
    PluginError,
    PluginLock,
    PluginLockEntry,
    PluginManager,
    PluginRuntime,
)
from deckhand.sidecar import (
    SidecarAdapter,
    SidecarClient,
    SidecarConnection,
    SidecarProtocolError,
    SidecarServer,
    artifact_digest,
)
from deckhand.wasm_host_main import _WasmHostPlugin

PLUGIN_ID = "dh-http-status"
ADAPTER_ID = "dh-http-status.read"


class FakeInvoker:
    """A signed WASM component invoked through ``gang run --export``, faked.

    Returns valid deckhand:adapter@1.0.0 lifecycle results for every export plus a
    read-only ``describe`` — so a host built on it satisfies the frozen conformance
    suite exactly as the real tiers do. ``mutation`` toggles whether the described
    component declares a mutating action (used by the fail-closed gate test).
    """

    def __init__(self, *, mutation: bool = False) -> None:
        self.calls: list[str] = []
        self._mutation = mutation

    async def __call__(self, export: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(export)
        if export == "health":
            return {"state": "healthy", "details": {"source": "wasm-host-fake"}}
        if export == "plan":
            return {"steps": ["observe target via out-of-process wasm host"]}
        if export == "execute":
            return {"reference": "wasm-host:exec-1", "details": {}}
        if export == "observe":
            return {"state": "healthy", "details": {}}
        if export == "verify":
            return {"satisfied": True, "details": {}}
        if export == "cancel":
            return {"disposition": "already_terminal", "details": {}}
        if export == "describe":
            return self._describe()
        raise AssertionError(f"unexpected export {export}")

    def _describe(self) -> dict[str, Any]:
        # Always declare the conformance read action so the host SidecarServer
        # recognises the action the frozen suite drives it with.
        actions: list[dict[str, Any]] = [read_action(adapter_id=ADAPTER_ID).model_dump(mode="json")]
        if self._mutation:
            actions.append(
                {
                    "id": "http_status.resource.ensure_active",
                    "version": 1,
                    "title": "Ensure active",
                    "description": "A mutating action declared by the component.",
                    "risk_class": "reversible",
                    "plugin": PLUGIN_ID,
                    "adapter": ADAPTER_ID,
                    "target_types": ["resource"],
                    "parameter_schema": {"type": "object", "additionalProperties": False},
                    "policy_action": "http_status.resource.ensure_active",
                    "confirmation": "confirm",
                    "timeout_seconds": 10,
                    "idempotency": "idempotency-key",
                    "mutation": True,
                }
            )
        return {
            "manifest": {
                "id": PLUGIN_ID,
                "name": "HTTP status",
                "version": "0.1.0",
                "description": "Signed WASM HTTP status component.",
                "adapters": [ADAPTER_ID],
                "actions": [action["id"] for action in actions],
                # permissions.mutation stays False even when a mutating action is
                # declared, so the gate test proves the per-action ActionDefinition
                # mutation flag is authoritative on its own.
                "permissions": {"mutation": False},
            },
            "adapters": [ADAPTER_ID],
            "status_providers": [],
            "actions": actions,
        }


@dataclass
class WasmHostFixture:
    server: SidecarServer
    client: SidecarClient
    artifact: Path
    socket_root: Path
    connection: SidecarConnection
    invoker: FakeInvoker

    def cleanup(self) -> None:
        shutil.rmtree(self.socket_root)


async def make_host_fixture(
    tmp_path: Path,
    *,
    invoker: FakeInvoker | None = None,
    broker_uid: int | None = None,
    expected_uid: int | None = None,
) -> WasmHostFixture:
    """Stand up a real deckhand-wasm-host SidecarServer over a fake ``gang``.

    Mirrors the sidecar fixture: a signed+digested artifact, an Ed25519 trust key,
    and a peer-authenticated Unix socket. The host builds its own GanglionClient
    with the injected fake invoker, describes it, wraps it as a DeckhandPlugin, and
    serves it via the EXISTING SidecarServer.
    """
    invoker = invoker or FakeInvoker()
    socket_base = Path("/private/tmp") if sys.platform == "darwin" else Path("/tmp")  # noqa: S108
    socket_root = socket_base / f"dh-wh-{uuid4().hex[:8]}"
    socket_root.mkdir(mode=0o700)
    socket_directory = socket_root / PLUGIN_ID
    socket_directory.mkdir(mode=0o700)

    artifact = tmp_path / f"{PLUGIN_ID}.wasm"
    artifact.write_bytes(b"deterministic signed wasm component")
    digest = artifact_digest(artifact, max_bytes=1024)

    trust_root = tmp_path / "trust"
    trust_root.mkdir(mode=0o700)
    private_key = Ed25519PrivateKey.generate()
    public_key_path = trust_root / "publisher.pem"
    public_key_path.write_bytes(
        private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    public_key_path.chmod(0o600)
    signature_path = tmp_path / f"{PLUGIN_ID}.sig"
    signature_path.write_bytes(base64.b64encode(private_key.sign(digest.encode("ascii"))))

    connection = SidecarConnection(
        socket_path=socket_directory / "plugin.sock",
        socket_root=socket_root,
        expected_uid=os.getuid() if expected_uid is None else expected_uid,
        artifact_path=artifact,
        signature_path=signature_path,
        public_key_path=public_key_path,
        trust_root=trust_root,
        trust_owner_uid=os.getuid(),
        artifact_owner_uid=os.getuid(),
        max_artifact_bytes=1024,
    )

    # The host wraps a real GanglionClient (fake invoker) as a DeckhandPlugin.
    ganglion_client = GanglionClient(
        WasmConnection(
            data_dir=tmp_path / "gang-data", robot="up-robot", capability="dh-http-status"
        ),
        invoker=invoker,
    )
    description = await ganglion_client.describe()
    plugin = _WasmHostPlugin(ganglion_client, description)

    server = SidecarServer(
        plugin=plugin,
        config={},
        artifact_path=artifact,
        socket_path=connection.socket_path,
        broker_uid=os.getuid() if broker_uid is None else broker_uid,
        max_artifact_bytes=1024,
    )
    return WasmHostFixture(
        server=server,
        client=SidecarClient(PLUGIN_ID, connection, digest),
        artifact=artifact,
        socket_root=socket_root,
        connection=connection,
        invoker=invoker,
    )


@pytest.mark.asyncio
async def test_out_of_process_wasm_host_passes_the_frozen_conformance_suite(
    tmp_path: Path,
) -> None:
    # THE LOAD-BEARING TEST. The out-of-process host runs the IDENTICAL frozen
    # conformance suite the in-process wasm tier and the sidecar tier pass —
    # driven through a real SidecarClient/SidecarAdapter over the socket.
    isolated = await make_host_fixture(tmp_path)
    await isolated.server.start()
    try:
        await asyncio.to_thread(isolated.client.handshake)
        adapter = SidecarAdapter(ADAPTER_ID, isolated.client)
        await assert_adapter_conformance(adapter, adapter_id=ADAPTER_ID)
        for export in ("health", "plan", "execute", "observe", "verify", "cancel"):
            assert export in isolated.invoker.calls
    finally:
        await isolated.server.close()
        isolated.cleanup()


@pytest.mark.asyncio
async def test_host_reports_the_locked_digest_and_rejects_a_mismatch(tmp_path: Path) -> None:
    # The handshake reports the locked artifact digest; a client that expects a
    # different digest is refused before any lifecycle call (reused sidecar path).
    isolated = await make_host_fixture(tmp_path)
    await isolated.server.start()
    try:
        handshake = await asyncio.to_thread(isolated.client.handshake)
        assert handshake.artifact_digest == artifact_digest(isolated.artifact, max_bytes=1024)

        wrong = "sha256:" + "0" * 64
        with pytest.raises(SidecarProtocolError, match="digest"):
            SidecarClient(PLUGIN_ID, isolated.connection, wrong)
    finally:
        await isolated.server.close()
        isolated.cleanup()


@pytest.mark.asyncio
async def test_host_rejects_peer_uid_mismatch(tmp_path: Path) -> None:
    # A broker whose UID does not match the host's configured broker_uid gets no
    # service — the SidecarServer drops the connection before dispatch.
    isolated = await make_host_fixture(tmp_path, broker_uid=os.getuid() + 1)
    await isolated.server.start()
    try:
        adapter = SidecarAdapter(ADAPTER_ID, isolated.client)
        # The server drops the connection before dispatch, so the client observes
        # transport loss (a SidecarTransportError / AdapterError), never a result.
        with pytest.raises(AdapterError):
            await adapter.health()
    finally:
        await isolated.server.close()
        isolated.cleanup()


@pytest.mark.asyncio
async def test_broker_loads_wasm_over_the_out_of_process_host(tmp_path: Path) -> None:
    # A wasm-source plugin whose runtime declares a socket transport loads through
    # the SidecarClient handshake — the production out-of-process path end to end.
    isolated = await make_host_fixture(tmp_path)
    await isolated.server.start()
    try:
        wasm_connection = WasmConnection(
            data_dir=tmp_path / "gang-data",
            robot="up-robot",
            capability="dh-http-status",
            socket=isolated.connection,
        )
        configuration = PluginConfiguration(
            plugins={
                PLUGIN_ID: PluginActivation(
                    runtime=PluginRuntime(mode="wasm", wasm=wasm_connection)
                )
            }
        )
        lock = PluginLock(
            plugins=[
                PluginLockEntry(
                    id=PLUGIN_ID,
                    version="0.1.0",
                    source="wasm",
                    digest=isolated.client.expected_digest,
                )
            ]
        )
        loaded = await asyncio.to_thread(
            lambda: PluginManager(external_entry_points={}).load(
                configuration,
                lock,
                allow_external=False,
                allow_wasm=True,
            )
        )
        assert [manifest.id for manifest in loaded.manifests] == [PLUGIN_ID]
        assert loaded.adapters.get(ADAPTER_ID) is not None
    finally:
        await isolated.server.close()
        isolated.cleanup()


def test_mutation_capable_wasm_requires_the_out_of_process_host(tmp_path: Path) -> None:
    # A wasm component that declares a mutating action (per-action mutation=True,
    # even with manifest permissions.mutation=False) but configures ONLY the
    # in-process CLI transport (socket=None) fails closed at load: mutation needs
    # the second boundary the out-of-process host provides.
    invoker = FakeInvoker(mutation=True)
    wasm_connection = WasmConnection(
        data_dir=tmp_path / "gang-data",
        robot="up-robot",
        capability="dh-http-status",
    )

    # The in-process path builds its own GanglionClient inside _wasm, so patch the
    # class to inject the fake invoker without touching the production argv path.
    from deckhand import plugins as plugins_module

    real_client = plugins_module.GanglionClient

    def _client(connection: WasmConnection) -> GanglionClient:
        return real_client(connection, invoker=invoker)

    configuration = PluginConfiguration(
        plugins={
            PLUGIN_ID: PluginActivation(runtime=PluginRuntime(mode="wasm", wasm=wasm_connection))
        }
    )
    lock = PluginLock(
        plugins=[
            PluginLockEntry(
                id=PLUGIN_ID,
                version="0.1.0",
                source="wasm",
                digest="sha256:" + "a" * 64,
            )
        ]
    )
    plugins_module.GanglionClient = _client  # type: ignore[misc]
    try:
        with pytest.raises(PluginError, match="mutation-capable wasm plugin"):
            PluginManager(external_entry_points={}).load(
                configuration,
                lock,
                allow_external=False,
                allow_wasm=True,
            )
    finally:
        plugins_module.GanglionClient = real_client  # type: ignore[misc]


@pytest.mark.asyncio
async def test_host_mutation_transport_loss_is_unknown_outcome(tmp_path: Path) -> None:
    # Mutation transport loss over the socket to the host maps to UnknownOutcome so
    # the worker reconciles rather than concluding FAILED — the SidecarAdapter
    # mutation path already does this; assert it holds for the host-backed adapter.
    isolated = await make_host_fixture(tmp_path)
    # Never start the server: the connect fails, i.e. transport is lost.
    action = read_action(adapter_id=ADAPTER_ID).model_copy(
        update={"risk_class": RiskClass.REVERSIBLE, "mutation": True}
    )
    request = conformance_request(action)
    adapter = SidecarAdapter(ADAPTER_ID, isolated.client)
    try:
        with pytest.raises(UnknownOutcome):
            await adapter.execute(action, request)
    finally:
        isolated.cleanup()
