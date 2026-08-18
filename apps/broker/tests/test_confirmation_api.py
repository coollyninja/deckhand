"""End-to-end confirm→execute through the HTTP API.

This is the flow that was impossible before the digest fix: plan (with dry_run
semantics) returns a challenge, and execute (dry_run=false, fresh key) consumes
it and reaches a queued job.
"""

from uuid import uuid4

from fastapi.testclient import TestClient


def _mutation_request(*, dry_run: bool, token: str | None = None) -> dict[str, object]:
    return {
        "action_id": "test.resource.ensure_active",
        "action_version": 1,
        "target": {"type": "resource", "id": "example"},
        "parameters": {},
        "context": {"client": "macbook-air-m2", "control": "main:r2c4"},
        "idempotency_key": str(uuid4()),
        "dry_run": dry_run,
        "confirmation_token": token,
    }


def test_plan_returns_confirmation_and_digest(
    mutation_client: TestClient, headers: dict[str, str]
) -> None:
    plan = mutation_client.post(
        "/v1/actions/test.resource.ensure_active:plan",
        json=_mutation_request(dry_run=True),
        headers=headers,
    )
    assert plan.status_code == 200
    body = plan.json()
    assert body["executable"] is True
    assert body["required_confirmation"] == "confirm"
    assert body["confirmation"] is not None
    assert body["confirmation"]["token"]
    assert body["confirmation_digest"]


def test_confirm_flow_reaches_queued_job(
    mutation_client: TestClient, headers: dict[str, str]
) -> None:
    plan = mutation_client.post(
        "/v1/actions/test.resource.ensure_active:plan",
        json=_mutation_request(dry_run=True),
        headers=headers,
    )
    token = plan.json()["confirmation"]["token"]

    # Execute with dry_run flipped and a fresh idempotency key — the exact client
    # behaviour that used to 403 forever.
    execute = mutation_client.post(
        "/v1/actions/test.resource.ensure_active:execute",
        json=_mutation_request(dry_run=False, token=token),
        headers=headers,
    )
    assert execute.status_code == 200
    assert execute.json()["state"] == "queued"


def test_execute_without_confirmation_is_denied(
    mutation_client: TestClient, headers: dict[str, str]
) -> None:
    execute = mutation_client.post(
        "/v1/actions/test.resource.ensure_active:execute",
        json=_mutation_request(dry_run=False),
        headers=headers,
    )
    assert execute.status_code == 403


def test_confirmation_cancel_endpoint(mutation_client: TestClient, headers: dict[str, str]) -> None:
    plan = mutation_client.post(
        "/v1/actions/test.resource.ensure_active:plan",
        json=_mutation_request(dry_run=True),
        headers=headers,
    )
    confirmation_id = plan.json()["confirmation"]["id"]
    token = plan.json()["confirmation"]["token"]

    cancelled = mutation_client.post(f"/v1/confirmations/{confirmation_id}:cancel", headers=headers)
    assert cancelled.status_code == 200

    # A cancelled confirmation can no longer authorize execute.
    execute = mutation_client.post(
        "/v1/actions/test.resource.ensure_active:execute",
        json=_mutation_request(dry_run=False, token=token),
        headers=headers,
    )
    assert execute.status_code == 403


def test_cancel_unknown_confirmation_is_404(
    mutation_client: TestClient, headers: dict[str, str]
) -> None:
    response = mutation_client.post(
        "/v1/confirmations/confirm_does_not_exist:cancel", headers=headers
    )
    assert response.status_code == 404
