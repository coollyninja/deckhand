from pathlib import Path

import pytest
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


def test_tilde_paths_are_expanded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`~`-prefixed paths in settings and the OBS inventory must be expanded.

    Regression: every Path-valued field was read with a bare ``Path(...)``, so an
    operator writing the natural ``~/.deckhand/...`` got ENOENT at read time (it
    surfaced live as "OBS password file unavailable: ... '~/.deckhand/obs-password'").
    """
    from deckhand_macos.config import MacSettings, ObsSettings
    from deckhand_macos.obs import ObsClient

    home = tmp_path / "home"
    (home / ".deckhand").mkdir(parents=True)
    (home / ".deckhand" / "obs-password").write_text("s3cret", encoding="utf-8")
    (home / ".deckhand" / "agent-token").write_text("tok", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))

    # OBS password file: the exact reported repro.
    client = ObsClient(ObsSettings(password_file=Path("~/.deckhand/obs-password")))
    assert client._password() == "s3cret"

    # Settings-level path fields expand at load, so every read site benefits.
    settings = MacSettings(
        token_file=Path("~/.deckhand/agent-token"),
        inventory_path=Path("~/.deckhand/macos.yaml"),
    )
    assert not str(settings.token_file).startswith("~")
    assert settings.token_file == home / ".deckhand" / "agent-token"
    assert settings.inventory_path == home / ".deckhand" / "macos.yaml"


def test_issuer_tilde_paths_are_expanded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The issuer's signing key and caller-token paths expand `~` too."""
    from deckhand_macos.issuer import IssuerSettings

    home = tmp_path / "home"
    (home / ".deckhand").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))

    settings = IssuerSettings(
        signing_key_file=Path("~/.deckhand/identity.key"),
        subject="operator",
        device="workstation",
        caller_token_file=Path("~/.deckhand/agent-token"),
    )
    assert settings.signing_key_file == home / ".deckhand" / "identity.key"
    assert settings.caller_token_file == home / ".deckhand" / "agent-token"

    # None stays None (the field is optional).
    without = IssuerSettings(
        signing_key_file=Path("~/.deckhand/identity.key"),
        subject="operator",
        device="workstation",
    )
    assert without.caller_token_file is None
