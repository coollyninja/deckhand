"""Contract test for the REAL OpaPolicyEngine against a live OPA server.

This closes two review gaps at once: the real OpaPolicyEngine was 0%-tested (all
other tests substitute DevelopmentPolicyEngine), and the protected-target control
had no end-to-end proof. Skips cleanly when the `opa` binary is unavailable, so
it does not block environments without OPA (CI installs it).
"""

import shutil
import socket
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from deckhand.policy import OpaPolicyEngine, PolicyUnavailable

pytestmark = pytest.mark.skipif(shutil.which("opa") is None, reason="opa binary not available")

_POLICY_DIR = Path(__file__).parents[3] / "packages/policy"
_INVENTORY = {
    "inventory": {
        "operators": ["operator"],
        "managed_devices": ["device"],
        "allowed_targets": {"test.resource.ensure_active": ["example"]},
    }
}


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def opa_server(tmp_path: Path) -> Iterator[str]:
    import json

    data_file = tmp_path / "data.json"
    data_file.write_text(json.dumps(_INVENTORY), encoding="utf-8")
    port = _free_port()
    opa_bin = shutil.which("opa")
    assert opa_bin is not None  # guarded by pytestmark skipif
    proc = subprocess.Popen(  # noqa: S603 - fixed trusted argv, resolved absolute opa path
        [
            opa_bin,
            "run",
            "--server",
            "--addr",
            f"127.0.0.1:{port}",
            str(_POLICY_DIR),
            str(data_file),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(50):
            try:
                if httpx.get(f"{base}/health", timeout=0.2).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.1)
        else:
            raise RuntimeError("opa server did not become ready")
        yield base
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def _base_input(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "action": {
            "id": "test.resource.ensure_active",
            "risk_class": "reversible",
            "mutation": True,
            "confirmation": "confirm",
        },
        "subject": {"name": "operator", "device": "device", "channel": "mgmt-mtls"},
        "runtime": {"mutations_enabled": True, "audit_writable": True},
        "target": {"id": "example", "protected": False},
        "confirmation": {"valid": True, "request_digest": "d"},
        "request": {"digest": "d", "phase": "execute"},
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_real_opa_allows_valid_mutation(opa_server: str) -> None:
    engine = OpaPolicyEngine(opa_server, "/v1/data/deckhand/authz/decision")
    decision = await engine.decide(_base_input())
    assert decision.allow is True


@pytest.mark.asyncio
async def test_real_opa_denies_protected_target(opa_server: str) -> None:
    engine = OpaPolicyEngine(opa_server, "/v1/data/deckhand/authz/decision")
    decision = await engine.decide(_base_input(target={"id": "example", "protected": True}))
    assert decision.allow is False


@pytest.mark.asyncio
async def test_real_opa_ready(opa_server: str) -> None:
    engine = OpaPolicyEngine(opa_server, "/v1/data/deckhand/authz/decision")
    assert await engine.ready() is True


@pytest.mark.asyncio
async def test_real_opa_fail_closed_when_unreachable() -> None:
    engine = OpaPolicyEngine("http://127.0.0.1:1", "/v1/data/deckhand/authz/decision")
    with pytest.raises(PolicyUnavailable):
        await engine.decide(_base_input())
