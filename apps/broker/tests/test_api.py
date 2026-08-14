from uuid import uuid4

from fastapi.testclient import TestClient


def request(action_id: str = "test.resource.observe") -> dict[str, object]:
    return {
        "action_id": action_id,
        "action_version": 1,
        "target": {"type": "resource", "id": "example"},
        "parameters": {},
        "context": {"client": "macbook-air-m2", "control": "main:r1c1"},
        "idempotency_key": str(uuid4()),
        "dry_run": False,
        "confirmation_token": None,
    }


def test_health_does_not_require_identity(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}


def test_actions_require_authenticated_identity(client: TestClient) -> None:
    assert client.get("/v1/actions").status_code == 401


def test_plugin_inventory_requires_identity(client: TestClient, headers: dict[str, str]) -> None:
    assert client.get("/v1/plugins").status_code == 401
    response = client.get("/v1/plugins", headers=headers)
    assert response.status_code == 200
    assert response.json()[0]["id"] == "dh-core"


def test_tailscale_identity_requires_unspoofable_app_capability(client: TestClient) -> None:
    headers = {
        "Tailscale-User-Login": "operator@example.com",
        "X-Deckhand-Channel": "tailscale",
        "X-Deckhand-Proxy-Assertion": "test-proxy-assertion",
        "X-Deckhand-Device": "spoofed-device",
    }
    assert client.get("/v1/actions", headers=headers).status_code == 401


def test_tailscale_app_capability_supplies_device_identity(client: TestClient) -> None:
    headers = {
        "Tailscale-User-Login": "operator@example.com",
        "Tailscale-App-Capabilities": (
            '{"coollyninja.com/cap/deckhand":[{"device":"managed-mac"}]}'
        ),
        "X-Deckhand-Channel": "tailscale",
        "X-Deckhand-Proxy-Assertion": "test-proxy-assertion",
    }
    assert client.get("/v1/actions", headers=headers).status_code == 200


def test_unknown_parameters_are_rejected(client: TestClient, headers: dict[str, str]) -> None:
    payload = request()
    payload["parameters"] = {"command": "rm -rf /"}
    response = client.post("/v1/actions/test.resource.observe:plan", json=payload, headers=headers)
    assert response.status_code == 422
    assert "Additional properties are not allowed" in response.json()["detail"]


def test_mutations_are_disabled_by_default(client: TestClient, headers: dict[str, str]) -> None:
    payload = request("test.resource.ensure_active")
    response = client.post(
        "/v1/actions/test.resource.ensure_active:execute", json=payload, headers=headers
    )
    assert response.status_code == 403


def test_read_job_submission_is_idempotent(client: TestClient, headers: dict[str, str]) -> None:
    payload = request()
    first = client.post("/v1/actions/test.resource.observe:execute", json=payload, headers=headers)
    second = client.post("/v1/actions/test.resource.observe:execute", json=payload, headers=headers)
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["state"] == "queued"


def test_idempotency_key_cannot_change_request(client: TestClient, headers: dict[str, str]) -> None:
    payload = request()
    first = client.post("/v1/actions/test.resource.observe:execute", json=payload, headers=headers)
    assert first.status_code == 200
    payload["target"] = {"type": "resource", "id": "different"}
    second = client.post("/v1/actions/test.resource.observe:execute", json=payload, headers=headers)
    assert second.status_code == 409
