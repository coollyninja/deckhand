import asyncio
from time import monotonic

import pytest
from deckhand.adapters import AdapterError, AdapterErrorKind, UnknownOutcome
from deckhand.models import RetryDisposition
from deckhand.resilience import CircuitState, ResilienceGuard, ResiliencePolicy


def policy(**overrides: float | int) -> ResiliencePolicy:
    return ResiliencePolicy.model_validate(
        {
            "timeout_seconds": 1,
            "max_concurrency": 8,
            "requests_per_second": 10_000,
            "burst": 100,
            "failure_threshold": 2,
            "recovery_seconds": 0.02,
            **overrides,
        }
    )


@pytest.mark.asyncio
async def test_timeout_is_retryable_before_unknown_mutation_outcome() -> None:
    guard = ResilienceGuard("dh-test-read-timeout", policy(timeout_seconds=0.01))

    async def slow() -> None:
        await asyncio.sleep(1)

    with pytest.raises(AdapterError) as captured:
        await guard.call("observe", slow)
    assert captured.value.kind == AdapterErrorKind.TIMEOUT
    assert captured.value.retry == RetryDisposition.SAFE
    assert captured.value.reconciliation_required is False

    mutation_guard = ResilienceGuard("dh-test-mutation-timeout", policy(timeout_seconds=0.01))
    with pytest.raises(UnknownOutcome) as mutation:
        await mutation_guard.call("execute", slow, unknown_on_started_timeout=True)
    assert mutation.value.retry == RetryDisposition.RECONCILE_FIRST
    assert mutation.value.reconciliation_required is True


@pytest.mark.asyncio
async def test_circuit_opens_rejects_and_recovers_with_single_probe() -> None:
    guard = ResilienceGuard("dh-test-circuit", policy())
    calls = 0

    async def unavailable() -> None:
        nonlocal calls
        calls += 1
        raise AdapterError(
            "upstream unavailable",
            kind=AdapterErrorKind.UNAVAILABLE,
            retry=RetryDisposition.SAFE,
        )

    for _ in range(2):
        with pytest.raises(AdapterError):
            await guard.call("observe", unavailable)
    assert (await guard.snapshot()).state == CircuitState.OPEN

    with pytest.raises(AdapterError) as rejected:
        await guard.call("observe", unavailable)
    assert rejected.value.details == {"circuit_state": "open"}
    assert calls == 2

    await asyncio.sleep(0.03)
    assert await guard.call("observe", lambda: asyncio.sleep(0, result="healthy")) == "healthy"
    snapshot = await guard.snapshot()
    assert snapshot.state == CircuitState.CLOSED
    assert snapshot.consecutive_failures == 0


@pytest.mark.asyncio
async def test_concurrency_limit_serializes_plugin_calls() -> None:
    guard = ResilienceGuard("dh-test-concurrency", policy(max_concurrency=1))
    active = 0
    peak = 0

    async def operation() -> None:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1

    await asyncio.gather(
        guard.call("observe", operation),
        guard.call("observe", operation),
    )
    assert peak == 1


@pytest.mark.asyncio
async def test_token_bucket_throttles_calls_after_burst() -> None:
    guard = ResilienceGuard(
        "dh-test-rate",
        policy(requests_per_second=50, burst=1),
    )

    async def operation() -> None:
        return None

    started = monotonic()
    await guard.call("observe", operation)
    await guard.call("observe", operation)
    assert monotonic() - started >= 0.015


@pytest.mark.asyncio
async def test_unexpected_plugin_exception_is_sanitized() -> None:
    guard = ResilienceGuard("dh-test-sanitize", policy())

    async def unsafe() -> None:
        raise RuntimeError("secret upstream response")

    with pytest.raises(AdapterError) as captured:
        await guard.call("observe", unsafe)
    assert captured.value.kind == AdapterErrorKind.UNEXPECTED
    assert str(captured.value) == "plugin operation failed"
    assert "secret" not in str(captured.value)
    assert captured.value.__cause__ is None
