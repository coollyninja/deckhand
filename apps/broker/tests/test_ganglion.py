"""Ganglion WASM isolation tier (ADR-0005).

The load-bearing test: a wasm-tier adapter passes the IDENTICAL frozen conformance
suite the in-process host passes. Uses a fake `gang` invoker so no real binary or
component is needed — the fake returns valid deckhand:adapter lifecycle JSON for
each named export.
"""

from pathlib import Path
from typing import Any

import pytest
from deckhand.adapters import AdapterErrorKind
from deckhand.conformance import assert_adapter_conformance, read_action
from deckhand.ganglion import (
    GanglionAdapter,
    GanglionClient,
    WasmConnection,
    WasmError,
)


def _connection(tmp_path: Path) -> WasmConnection:
    return WasmConnection(
        data_dir=tmp_path / "gang-data",
        robot="up-robot",
        capability="dh-http-status",
    )


class FakeInvoker:
    """Stands in for a signed WASM component invoked through `gang run --export`.

    Returns valid deckhand:adapter@1.0.0 lifecycle results for every export, so a
    GanglionAdapter built on it must satisfy the frozen conformance suite exactly
    as the in-process host does.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def invoke(self, export: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(export)
        if export == "health":
            return {"state": "healthy", "details": {"source": "wasm-fake"}}
        if export == "plan":
            return {"steps": ["observe target via wasm component"]}
        if export == "execute":
            return {"reference": "wasm:exec-1", "details": {}}
        if export == "observe":
            return {"state": "healthy", "details": {}}
        if export == "verify":
            return {"satisfied": True, "details": {}}
        if export == "cancel":
            return {"disposition": "already_terminal", "details": {}}
        if export == "describe":
            return {
                "manifest": {
                    "id": "dh-http-status",
                    "name": "HTTP status",
                    "version": "0.1.0",
                    "adapters": ["dh-http-status.read"],
                    "permissions": {"mutation": False},
                },
                "adapters": ["dh-http-status.read"],
                "status_providers": [],
                "actions": [],
            }
        raise AssertionError(f"unexpected export {export}")


@pytest.mark.asyncio
async def test_wasm_tier_passes_the_frozen_conformance_suite(tmp_path: Path) -> None:
    fake = FakeInvoker()
    adapter = GanglionAdapter("dh-http-status.read", fake)  # type: ignore[arg-type]
    await assert_adapter_conformance(adapter, adapter_id="dh-http-status.read")
    # All six lifecycle exports were exercised.
    for export in ("health", "plan", "execute", "observe", "verify", "cancel"):
        assert export in fake.calls


def test_wasm_connection_rejects_shell_metacharacters(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="absolute path or a bare command name"):
        WasmConnection(
            data_dir=tmp_path / "d",
            robot="up-robot",
            capability="cap",
            gang_binary="gang; rm -rf /",
        )


def test_wasm_connection_requires_absolute_data_dir() -> None:
    with pytest.raises(ValueError, match="absolute"):
        WasmConnection(data_dir=Path("relative/dir"), robot="r", capability="c")


@pytest.mark.asyncio
async def test_invoke_rejects_unknown_export(tmp_path: Path) -> None:
    client = GanglionClient(_connection(tmp_path))
    with pytest.raises(WasmError, match="unknown lifecycle export"):
        await client.invoke("not_an_export", {})


@pytest.mark.asyncio
async def test_invoke_maps_missing_binary_to_configuration_error(tmp_path: Path) -> None:
    # A bare-name binary that does not resolve on PATH → CONFIGURATION.
    conn = WasmConnection(
        data_dir=tmp_path / "d",
        robot="r",
        capability="c",
        gang_binary="definitely-not-a-real-binary-xyz",
    )
    client = GanglionClient(conn)
    with pytest.raises(WasmError) as captured:
        await client.invoke("health", {})
    assert captured.value.kind == AdapterErrorKind.CONFIGURATION


@pytest.mark.asyncio
async def test_mutation_transport_failure_is_unknown_outcome(tmp_path: Path) -> None:
    # A WasmError during execute of a MUTATION must become UnknownOutcome so the
    # worker reconciles rather than concluding FAILED.
    from deckhand.adapters import UnknownOutcome
    from deckhand.conformance import conformance_request

    class Failing:
        async def invoke(self, export: str, payload: dict[str, Any]) -> dict[str, Any]:
            raise WasmError("gang unreachable", kind=AdapterErrorKind.UNAVAILABLE)

    adapter = GanglionAdapter("dh-x.mutate", Failing())  # type: ignore[arg-type]
    action = read_action(adapter_id="dh-x.mutate").model_copy(update={"mutation": True})
    request = conformance_request(action)
    with pytest.raises(UnknownOutcome):
        await adapter.execute(action, request)


@pytest.mark.asyncio
async def test_oversized_result_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A component flooding the broker with a huge result is bounded.
    import asyncio

    client = GanglionClient(
        WasmConnection(data_dir=tmp_path / "d", robot="r", capability="c", gang_binary="/bin/echo")
    )

    class FakeProc:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return (b"x" * (2 * 1024 * 1024), b"")

    async def fake_exec(*args: Any, **kwargs: Any) -> FakeProc:
        return FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    with pytest.raises(WasmError, match="exceeds the size limit"):
        await client.invoke("health", {})
