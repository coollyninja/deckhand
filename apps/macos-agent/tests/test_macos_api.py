from pathlib import Path

from deckhand_macos.api import create_app
from deckhand_macos.config import MacSettings
from deckhand_macos.models import LocalAction, LocalActionRequest, LocalActionResult
from fastapi.testclient import TestClient


class FakeExecutor:
    async def execute(self, request: LocalActionRequest) -> LocalActionResult:
        return LocalActionResult(
            action_id=request.action_id,
            target=request.target,
            state="running",
            verified=True,
        )


def test_agent_requires_local_bearer(tmp_path: Path) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("local-secret", encoding="utf-8")
    settings = MacSettings(token_file=token_file)
    client = TestClient(create_app(settings, executor=FakeExecutor()))
    payload = {"action_id": LocalAction.APP_ENSURE_RUNNING, "target": "terminal"}
    assert client.post("/v1/actions:execute", json=payload).status_code == 401
    response = client.post(
        "/v1/actions:execute",
        json=payload,
        headers={"Authorization": "Bearer local-secret"},
    )
    assert response.status_code == 200
    assert response.json()["verified"] is True


def test_agent_rejects_unknown_fields(tmp_path: Path) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("local-secret", encoding="utf-8")
    settings = MacSettings(token_file=token_file)
    client = TestClient(create_app(settings, executor=FakeExecutor()))
    response = client.post(
        "/v1/actions:execute",
        json={
            "action_id": LocalAction.APP_ENSURE_RUNNING,
            "target": "terminal",
            "command": "arbitrary shell",
        },
        headers={"Authorization": "Bearer local-secret"},
    )
    assert response.status_code == 422
