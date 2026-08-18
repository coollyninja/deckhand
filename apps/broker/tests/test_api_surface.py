"""Phase 3 API surface: GET /v1/actions/{id}, SSE event stream, WebSocket feed."""

import json
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from deckhand.identity import mint_token
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect


def _read_request() -> dict[str, object]:
    return {
        "action_id": "test.resource.observe",
        "action_version": 1,
        "target": {"type": "resource", "id": "example"},
        "parameters": {},
        "context": {"client": "mac", "control": "main:r1c1"},
        "idempotency_key": str(uuid4()),
        "dry_run": False,
        "confirmation_token": None,
    }


def test_action_detail_returns_latest(client: TestClient, headers: dict[str, str]) -> None:
    response = client.get("/v1/actions/test.resource.observe", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "test.resource.observe"
    assert body["version"] == 1
    assert body["risk_class"] == "read"


def test_action_detail_unknown_is_404(client: TestClient, headers: dict[str, str]) -> None:
    assert client.get("/v1/actions/nope.does.not_exist", headers=headers).status_code == 404


def test_action_detail_requires_identity(client: TestClient) -> None:
    assert client.get("/v1/actions/test.resource.observe").status_code == 401


def test_sse_stream_emits_existing_events(client: TestClient, headers: dict[str, str]) -> None:
    # Generate an audit event by submitting a read job.
    client.post("/v1/actions/test.resource.observe:execute", json=_read_request(), headers=headers)
    # `once=true` drains existing events and closes, so the sync test client gets
    # the full body without an open-ended stream.
    response = client.get("/v1/events/stream?after=0&once=true", headers=headers)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    body = response.text
    assert "event:" in body
    assert "data:" in body
    data_line = next(line for line in body.splitlines() if line.startswith("data:"))
    payload = json.loads(data_line[len("data: ") :])
    assert "sequence" in payload
    assert "event_type" in payload


def test_websocket_requires_valid_identity_token(signed_client: TestClient) -> None:
    # No token → connection is closed with a policy-violation code before accept.
    with pytest.raises(WebSocketDisconnect), signed_client.websocket_connect("/v1/ws") as ws:
        ws.receive_text()


def test_websocket_rejects_forged_token(signed_client: TestClient) -> None:
    attacker = Ed25519PrivateKey.generate()
    forged = mint_token(attacker, subject="bobby", device="mac", channel="mcp", nonce="n")
    with (
        pytest.raises(WebSocketDisconnect),
        signed_client.websocket_connect(f"/v1/ws?identity={forged}") as ws,
    ):
        ws.receive_text()


def test_websocket_streams_events_with_valid_token(
    signed_client: TestClient, identity_key: Ed25519PrivateKey
) -> None:
    token = mint_token(identity_key, subject="bobby", device="mac", channel="mgmt-mtls", nonce="n")
    # Drive a read job so at least one audit event exists to stream.
    signed_client.post(
        "/v1/actions/test.resource.observe:execute",
        json=_read_request(),
        headers={"X-Deckhand-Identity": token},
    )
    with signed_client.websocket_connect(f"/v1/ws?identity={token}&after=0") as ws:
        event = ws.receive_json()
        assert "sequence" in event
        assert "event_type" in event
