"""Transport-level coverage for the peer-authenticated Unix-socket host transport.

These tests drive the transport that ``deckhand-wasm-host`` speaks — peer auth,
framing, handshake, signed-artifact/digest verification, and mutation-transport
loss — through the ``WasmHost*`` symbols, standing a plugin up behind a real
``WasmHostServer`` on a tmp socket exactly as ``test_wasm_host.py`` does. End-to-end
loading through ``PluginManager`` over the socket is covered by ``test_wasm_host.py``.
"""

import asyncio
import base64
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from deckhand.adapters import AdapterError, AdapterErrorKind, FakeAdapter, UnknownOutcome
from deckhand.models import (
    ActionDefinition,
    ActionRequest,
    ConfirmationMode,
    RequestContext,
    RiskClass,
    StatusValue,
    Target,
)
from deckhand.plugin_api import (
    PluginContext,
    PluginContribution,
    PluginManifest,
    PluginPermissions,
    StaticStatusProvider,
)
from deckhand.wasm_host_transport import (
    HostProtocolError,
    WasmHostAdapter,
    WasmHostClient,
    WasmHostConnection,
    WasmHostServer,
    WasmHostStatusProvider,
    artifact_digest,
)

ACTION = ActionDefinition(
    id="test.resource.observe",
    version=1,
    title="Observe resource",
    description="Read one deterministic test resource.",
    risk_class=RiskClass.READ,
    plugin="dh-host-test",
    adapter="dh-host-test.read",
    target_types=["test_resource"],
    parameter_schema={"type": "object", "additionalProperties": False},
    policy_action="test.resource.observe",
    confirmation=ConfirmationMode.NONE,
    timeout_seconds=5,
    idempotency="read-only",
    mutation=False,
)


class FixturePlugin:
    @property
    def manifest(self) -> PluginManifest:
        return PluginManifest(
            id="dh-host-test",
            name="Host transport test",
            version="1.0.0",
            description="Deterministic host transport test plugin.",
            adapters=["dh-host-test.read"],
            status_provider_types=["test-resource"],
            actions=[ACTION.id],
            permissions=PluginPermissions(mutation=False),
        )

    def build(self, context: PluginContext) -> PluginContribution:
        assert context.config == {"enabled": True}
        return PluginContribution(
            adapters={"dh-host-test.read": FakeAdapter()},
            status_providers={"test_resource": StaticStatusProvider(StatusValue(state="healthy"))},
            actions=(ACTION,),
        )


class LeakyAdapter(FakeAdapter):
    async def health(self):  # type: ignore[no-untyped-def]
        health = await super().health()
        return health.model_copy(update={"details": {"api_token": "must-not-cross"}})


class LeakyPlugin(FixturePlugin):
    def build(self, context: PluginContext) -> PluginContribution:
        contribution = super().build(context)
        return PluginContribution(
            adapters={"dh-host-test.read": LeakyAdapter()},
            status_providers=contribution.status_providers,
            actions=contribution.actions,
        )


@dataclass
class HostFixture:
    server: WasmHostServer
    client: WasmHostClient
    artifact: Path
    socket_root: Path
    connection: WasmHostConnection

    def cleanup(self) -> None:
        shutil.rmtree(self.socket_root)


def request() -> ActionRequest:
    return ActionRequest(
        action_id=ACTION.id,
        action_version=1,
        target=Target(type="test_resource", id="example"),
        parameters={},
        context=RequestContext(client="test"),
        idempotency_key=UUID("00000000-0000-4000-8000-000000000001"),
        confirmation_token="a" * 32,
    )


def make_fixture(tmp_path: Path, plugin: FixturePlugin | None = None) -> HostFixture:
    socket_base = Path("/private/tmp") if sys.platform == "darwin" else Path("/tmp")  # noqa: S108
    socket_root = socket_base / f"dh-ht-{uuid4().hex[:8]}"
    socket_root.mkdir(mode=0o700)
    socket_directory = socket_root / "dh-host-test"
    socket_directory.mkdir(mode=0o700)

    artifact = tmp_path / "dh-host-test.pyz"
    artifact.write_bytes(b"deterministic host transport artifact")
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
    signature_path = tmp_path / "dh-host-test.sig"
    signature_path.write_bytes(base64.b64encode(private_key.sign(digest.encode("ascii"))))

    connection = WasmHostConnection(
        socket_path=socket_directory / "plugin.sock",
        socket_root=socket_root,
        expected_uid=os.getuid(),
        artifact_path=artifact,
        signature_path=signature_path,
        public_key_path=public_key_path,
        trust_root=trust_root,
        trust_owner_uid=os.getuid(),
        artifact_owner_uid=os.getuid(),
        max_artifact_bytes=1024,
    )
    server = WasmHostServer(
        plugin=plugin or FixturePlugin(),
        config={"enabled": True},
        artifact_path=artifact,
        socket_path=connection.socket_path,
        broker_uid=os.getuid(),
        max_artifact_bytes=1024,
    )
    return HostFixture(
        server=server,
        client=WasmHostClient("dh-host-test", connection, digest),
        artifact=artifact,
        socket_root=socket_root,
        connection=connection,
    )


@pytest.mark.asyncio
async def test_signed_host_proxies_complete_lifecycle_and_status(tmp_path: Path) -> None:
    isolated = make_fixture(tmp_path)
    await isolated.server.start()
    try:
        handshake = await asyncio.to_thread(isolated.client.handshake)
        assert handshake.manifest.id == "dh-host-test"
        assert handshake.artifact_digest == artifact_digest(isolated.artifact, max_bytes=1024)
        assert handshake.status_providers == ["test_resource"]

        adapter = WasmHostAdapter("dh-host-test.read", isolated.client)
        action_request = request()
        assert (await adapter.health()).state == "healthy"
        plan = await adapter.plan(ACTION, action_request)
        execution = await adapter.execute(ACTION, action_request)
        observation = await adapter.observe(ACTION, action_request)
        verification = await adapter.verify(ACTION, action_request, execution, observation)
        cancellation = await adapter.cancel(ACTION, action_request, execution)
        status = await WasmHostStatusProvider("test_resource", isolated.client).observe()

        assert plan.steps
        assert execution.reference
        assert observation.state == "healthy"
        assert verification.satisfied is True
        assert cancellation.disposition == "already_terminal"
        assert status.state == "healthy"
    finally:
        await isolated.server.close()
        isolated.cleanup()


@pytest.mark.asyncio
async def test_host_rejects_sensitive_result_fields(tmp_path: Path) -> None:
    isolated = make_fixture(tmp_path, LeakyPlugin())
    await isolated.server.start()
    try:
        adapter = WasmHostAdapter("dh-host-test.read", isolated.client)
        with pytest.raises(AdapterError) as captured:
            await adapter.health()
        assert captured.value.kind == AdapterErrorKind.PROTOCOL
        assert "must-not-cross" not in str(captured.value)
    finally:
        await isolated.server.close()
        isolated.cleanup()


def test_host_rejects_tampered_artifact_before_connect(tmp_path: Path) -> None:
    isolated = make_fixture(tmp_path)
    try:
        isolated.artifact.write_bytes(b"tampered")
        with pytest.raises(HostProtocolError, match="digest"):
            WasmHostClient(
                "dh-host-test",
                isolated.client.connection,
                isolated.client.expected_digest,
            )
    finally:
        isolated.cleanup()


@pytest.mark.asyncio
async def test_host_transport_loss_during_mutation_requires_reconciliation(
    tmp_path: Path,
) -> None:
    isolated = make_fixture(tmp_path)
    mutation = ACTION.model_copy(
        update={
            "id": "test.resource.ensure_active",
            "risk_class": RiskClass.REVERSIBLE,
            "mutation": True,
            "confirmation": ConfirmationMode.CONFIRM,
        }
    )
    mutation_request = request().model_copy(update={"action_id": mutation.id})
    try:
        adapter = WasmHostAdapter("dh-host-test.read", isolated.client)
        with pytest.raises(UnknownOutcome) as captured:
            await adapter.execute(mutation, mutation_request)
        assert captured.value.reconciliation_required is True
    finally:
        isolated.cleanup()


def test_host_runtime_paths_must_be_absolute() -> None:
    with pytest.raises(ValueError, match="absolute"):
        WasmHostConnection(
            socket_path=Path("relative.sock"),
            expected_uid=os.getuid(),
            artifact_path=Path("/artifact"),
            signature_path=Path("/signature"),
            public_key_path=Path("/key"),
        )
