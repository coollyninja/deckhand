"""The MCP surface is a thin broker client: every tool call goes through the
broker's policy/confirmation/audit/durable-job path and cannot bypass it."""

from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from deckhand.api import create_app
from deckhand.broker_client import BrokerClient, BrokerClientError
from deckhand.config import Settings
from deckhand.mcp_server import McpCaller, build_tool_handlers
from deckhand.policy import DevelopmentPolicyEngine


def _make_client(tmp_path: Path, *, allow_mutations: bool) -> BrokerClient:
    root = Path(__file__).parents[3]
    key = Ed25519PrivateKey.generate()
    (tmp_path / "id.pub.pem").write_bytes(
        key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    settings = Settings(
        database_path=tmp_path / "deckhand.db",
        catalog_path=root / "apps/broker/tests/fixtures/catalog",
        trusted_proxy=True,
        identity_public_key_file=tmp_path / "id.pub.pem",
        allow_legacy_proxy_assertion=False,
        allow_mutations=allow_mutations,
    )
    app = create_app(settings, policy=DevelopmentPolicyEngine())
    # ASGITransport does not run the app lifespan, so initialise the store the way
    # the lifespan would before driving requests in-process.
    app.state.runtime.store.initialize()
    transport = httpx.ASGITransport(app=app)
    return BrokerClient(
        "http://broker.local",
        signing_key=key,
        channel="mcp",
        transport=transport,
    )


@pytest.fixture
def caller() -> McpCaller:
    return McpCaller(subject="mcp-operator", device="mcp-agent")


@pytest.mark.asyncio
async def test_mcp_list_actions_goes_through_broker(tmp_path: Path, caller: McpCaller) -> None:
    client = _make_client(tmp_path, allow_mutations=False)
    handlers = build_tool_handlers(client, caller)
    actions = await handlers["list_actions"]()
    ids = {a["id"] for a in actions}
    assert "test.resource.observe" in ids


@pytest.mark.asyncio
async def test_mcp_execute_read_reaches_queued_job(tmp_path: Path, caller: McpCaller) -> None:
    client = _make_client(tmp_path, allow_mutations=False)
    handlers = build_tool_handlers(client, caller)
    job = await handlers["execute_action"](
        action_id="test.resource.observe", target_type="resource", target_id="example"
    )
    assert job["state"] == "queued"


@pytest.mark.asyncio
async def test_mcp_cannot_bypass_policy_on_mutation(tmp_path: Path, caller: McpCaller) -> None:
    # Mutations disabled → an MCP execute of a mutation action is refused by the
    # broker (403), proving the MCP surface has no independent authority.
    client = _make_client(tmp_path, allow_mutations=False)
    handlers = build_tool_handlers(client, caller)
    with pytest.raises(BrokerClientError) as captured:
        await handlers["execute_action"](
            action_id="test.resource.ensure_active",
            target_type="resource",
            target_id="example",
        )
    assert captured.value.status_code == 403


@pytest.mark.asyncio
async def test_mcp_plan_surfaces_required_confirmation(tmp_path: Path, caller: McpCaller) -> None:
    # With mutations enabled, plan returns the confirmation requirement — the MCP
    # client must plan first and carry the token to execute, same as any client.
    client = _make_client(tmp_path, allow_mutations=True)
    handlers = build_tool_handlers(client, caller)
    plan = await handlers["plan_action"](
        action_id="test.resource.ensure_active", target_type="resource", target_id="example"
    )
    assert plan["required_confirmation"] == "confirm"
    assert plan["confirmation"] is not None
    assert plan["confirmation_digest"]


@pytest.mark.asyncio
async def test_mcp_full_confirm_flow_through_broker(tmp_path: Path, caller: McpCaller) -> None:
    client = _make_client(tmp_path, allow_mutations=True)
    handlers = build_tool_handlers(client, caller)
    plan = await handlers["plan_action"](
        action_id="test.resource.ensure_active", target_type="resource", target_id="example"
    )
    token = plan["confirmation"]["token"]
    job = await handlers["execute_action"](
        action_id="test.resource.ensure_active",
        target_type="resource",
        target_id="example",
        confirmation_token=token,
    )
    assert job["state"] == "queued"
