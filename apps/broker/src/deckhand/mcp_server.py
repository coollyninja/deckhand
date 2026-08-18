"""Authenticated MCP server for Deckhand — a thin broker client, not an executor.

This exposes Deckhand's typed actions to MCP clients (Claude and other agents) as
tools, but it never executes anything itself: every tool call is translated into
the same typed ActionRequest and forwarded to the broker, which applies identity,
deny-by-default policy, confirmation, durable jobs, observed postconditions, and
audit. An LLM-originated call therefore receives no additional authority — it is
just another typed-intent client alongside the Stream Deck (spec §8, §21).

Auth: the MCP caller presents a bearer token verified by a TokenVerifier. The
verified caller identity (subject, device) is then used to mint a short-lived
signed Deckhand identity token (channel=mcp) for the broker hop, so the broker
sees a real per-caller identity rather than a shared secret.

The `mcp` package is an optional dependency (install `deckhand-control-plane[mcp]`).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from .broker_client import BrokerClient
from .config import Settings
from .identity import load_private_key


@dataclass(frozen=True)
class McpCaller:
    """The authenticated identity behind an MCP session."""

    subject: str
    device: str


class McpConfigError(RuntimeError):
    pass


def _nonce() -> str:
    return uuid.uuid4().hex


def build_tool_handlers(client: BrokerClient, caller: McpCaller) -> dict[str, Any]:
    """Return the async tool implementations bound to a broker client + caller.

    Kept separate from the MCP wiring so the tool logic is unit-testable without a
    running MCP transport. Every handler forwards to the broker; none executes.
    """

    async def list_actions() -> list[dict[str, Any]]:
        """List the Deckhand actions visible to the caller."""
        return await client.list_actions(
            subject=caller.subject, device=caller.device, nonce=_nonce()
        )

    async def status_summary() -> dict[str, Any]:
        """Return the authoritative global status summary."""
        return await client.status_summary(
            subject=caller.subject, device=caller.device, nonce=_nonce()
        )

    async def plan_action(
        action_id: str, target_type: str, target_id: str, parameters: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Plan (dry-run) a typed action: validate + authorize + return intended
        steps and any required confirmation, WITHOUT mutating anything."""
        body = _request_body(action_id, target_type, target_id, parameters, dry_run=True)
        plan = await client.plan(
            action_id, body, subject=caller.subject, device=caller.device, nonce=_nonce()
        )
        return plan.model_dump(mode="json")

    async def execute_action(
        action_id: str,
        target_type: str,
        target_id: str,
        parameters: dict[str, Any] | None = None,
        confirmation_token: str | None = None,
        confirmation_response: str | None = None,
    ) -> dict[str, Any]:
        """Submit a typed action for execution through the broker. Mutations still
        require policy allowance and (where declared) a confirmation token obtained
        from plan_action — the MCP surface cannot bypass either."""
        body = _request_body(
            action_id,
            target_type,
            target_id,
            parameters,
            dry_run=False,
            confirmation_token=confirmation_token,
            confirmation_response=confirmation_response,
        )
        job = await client.execute(
            action_id, body, subject=caller.subject, device=caller.device, nonce=_nonce()
        )
        return job.model_dump(mode="json")

    async def job_status(job_id: str) -> dict[str, Any]:
        """Fetch the current state/result of a submitted job."""
        job = await client.job(job_id, subject=caller.subject, device=caller.device, nonce=_nonce())
        return job.model_dump(mode="json")

    return {
        "list_actions": list_actions,
        "status_summary": status_summary,
        "plan_action": plan_action,
        "execute_action": execute_action,
        "job_status": job_status,
    }


def _request_body(
    action_id: str,
    target_type: str,
    target_id: str,
    parameters: dict[str, Any] | None,
    *,
    dry_run: bool,
    confirmation_token: str | None = None,
    confirmation_response: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "action_id": action_id,
        "action_version": 1,
        "target": {"type": target_type, "id": target_id},
        "parameters": parameters or {},
        "context": {"client": "deckhand-mcp", "control": "mcp"},
        "idempotency_key": str(uuid.uuid4()),
        "dry_run": dry_run,
    }
    if confirmation_token is not None:
        body["confirmation_token"] = confirmation_token
    if confirmation_response is not None:
        body["confirmation_response"] = confirmation_response
    return body


def build_broker_client(settings: Settings) -> BrokerClient:
    if settings.identity_signing_key_file is None:
        raise McpConfigError(
            "DECKHAND_IDENTITY_SIGNING_KEY_FILE is required for the MCP server to "
            "mint broker identity tokens"
        )
    signing_key = load_private_key(settings.identity_signing_key_file)
    return BrokerClient(
        settings.mcp_broker_url,
        signing_key=signing_key,
        channel="mcp",
    )


def build_server(settings: Settings, caller: McpCaller) -> Any:
    """Construct the MCP server with Deckhand's tools bound to a broker client.

    Imports the MCP SDK lazily so the core package does not hard-require it.
    """
    try:
        from mcp.server import MCPServer
    except ImportError as error:  # pragma: no cover
        raise McpConfigError(
            "the MCP server requires the 'mcp' extra: pip install 'deckhand-control-plane[mcp]'"
        ) from error

    client = build_broker_client(settings)
    handlers = build_tool_handlers(client, caller)
    server = MCPServer(
        name="deckhand",
        version="0.5.0",
        instructions=(
            "Deckhand exposes typed lab/workstation actions. Every tool call is "
            "authorized, confirmed where required, executed durably, and audited "
            "by the broker; you cannot bypass that path. Use plan_action first to "
            "see required confirmations before execute_action."
        ),
    )
    for tool_name, handler in handlers.items():
        server.add_tool(handler, name=tool_name)
    return server


def run() -> None:  # pragma: no cover - process entry point
    """Entry point for the `deckhand-mcp` console script.

    Auth of the MCP caller is deployment-supplied (a TokenVerifier backed by the
    operator's OAuth provider); a single-operator deployment maps every session to
    the configured default identity. The bearer-token verification is enforced by
    the transport before any tool runs.
    """
    import anyio

    settings = Settings()
    caller = McpCaller(subject=settings.mcp_default_subject, device=settings.mcp_default_device)
    server = build_server(settings, caller)
    anyio.run(
        lambda: server.run_streamable_http_async(
            host=settings.mcp_bind_host, port=settings.mcp_bind_port
        )
    )
