"""Tier-parametrized adapter lifecycle conformance (lifecycle v1).

Every isolation tier runs the IDENTICAL conformance suite. Today that is the
in-process tier (FakeAdapter, the reference implementation); the sidecar tier is
exercised through its own fixture in test_sidecar.py, and the future wasm
(Ganglion) tier must be added here and pass unchanged before it can be enabled.
The suite is the frozen contract — see deckhand.conformance.
"""

import json
from pathlib import Path

import pytest
from deckhand.adapters import AdapterError, AdapterErrorKind, FakeAdapter
from deckhand.conformance import (
    LIFECYCLE_VERSION,
    ConformanceError,
    assert_adapter_conformance,
    assert_error_shape,
)
from deckhand.models import RetryDisposition


@pytest.mark.asyncio
async def test_in_process_tier_is_conformant() -> None:
    # The in-process reference adapter must satisfy the frozen lifecycle contract.
    await assert_adapter_conformance(FakeAdapter())


@pytest.mark.asyncio
async def test_sidecar_tier_is_conformant(tmp_path: Path) -> None:
    # The sidecar tier runs the IDENTICAL suite against a live signed sidecar —
    # proving contract parity across tiers, which is exactly what the wasm tier
    # will have to do to be enabled. Uses the same server fixture as test_sidecar.
    import asyncio

    from deckhand.sidecar import SidecarAdapter
    from test_sidecar import ACTION, make_fixture
    from test_sidecar import request as sidecar_request

    isolated = make_fixture(tmp_path)
    await isolated.server.start()
    try:
        await asyncio.to_thread(isolated.client.handshake)
        adapter = SidecarAdapter("dh-sidecar-test.read", isolated.client)
        # Drive with the sidecar fixture's own action/target so the fake plugin
        # recognises the request; the frozen assertions are identical.
        await assert_adapter_conformance(
            adapter,
            adapter_id="dh-sidecar-test.read",
            action=ACTION,
            request=sidecar_request(),
        )
    finally:
        await isolated.server.close()
        isolated.cleanup()


def test_error_shape_is_bounded() -> None:
    assert_error_shape(
        AdapterError("x", kind=AdapterErrorKind.UNAVAILABLE, retry=RetryDisposition.SAFE)
    )


@pytest.mark.asyncio
async def test_conformance_detects_a_non_conformant_adapter() -> None:
    # An adapter that returns the wrong type must be caught by the suite — proving
    # the suite actually enforces the contract, not just passes the good case.
    class Broken(FakeAdapter):
        async def plan(self, action, request):  # type: ignore[no-untyped-def]
            return "not a plan"  # type: ignore[return-value]

    with pytest.raises(ConformanceError):
        await assert_adapter_conformance(Broken())


def test_lifecycle_version_matches_the_frozen_contracts() -> None:
    # The version marker, the JSON schema, and the WIT package must move together.
    # This locks the Phase-0 rule: contract drift is a visible, reviewed event.
    assert LIFECYCLE_VERSION == "1.0.0"

    contracts = Path(__file__).parents[3] / "packages/contracts"
    wit = (contracts / "deckhand-adapter.wit").read_text(encoding="utf-8")
    assert "deckhand:adapter@1.0.0" in wit, "WIT package version must match LIFECYCLE_VERSION"

    # The JSON schema still defines every lifecycle value the suite asserts.
    schema = json.loads((contracts / "adapter-lifecycle.schema.json").read_text(encoding="utf-8"))
    defs = schema.get("$defs", {})
    for name in ("health", "execution", "observation", "verification"):
        assert name in defs, f"adapter-lifecycle.schema.json missing $defs.{name}"
