from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from enum import StrEnum
from time import monotonic
from typing import TypeVar

from pydantic import Field

from .adapters import (
    Adapter,
    AdapterCancellation,
    AdapterError,
    AdapterErrorKind,
    AdapterExecution,
    AdapterHealth,
    AdapterObservation,
    AdapterPlan,
    AdapterVerification,
    UnknownOutcome,
)
from .metrics import (
    PLUGIN_CALL_SECONDS,
    PLUGIN_CALLS,
    PLUGIN_CIRCUIT_STATE,
    PLUGIN_IN_FLIGHT,
    PLUGIN_QUEUE_SECONDS,
)
from .models import ActionDefinition, ActionRequest, RetryDisposition, StatusValue, StrictModel
from .status import StatusProvider

T = TypeVar("T")


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class ResiliencePolicy(StrictModel):
    timeout_seconds: float = Field(default=10.0, gt=0, le=300)
    max_concurrency: int = Field(default=8, ge=1, le=256)
    requests_per_second: float = Field(default=20.0, gt=0, le=10_000)
    burst: int = Field(default=20, ge=1, le=10_000)
    failure_threshold: int = Field(default=5, ge=1, le=100)
    recovery_seconds: float = Field(default=30.0, gt=0, le=3600)


class ResilienceSnapshot(StrictModel):
    state: CircuitState
    consecutive_failures: int = Field(ge=0)
    retry_after_seconds: float | None = Field(default=None, ge=0)
    max_concurrency: int = Field(ge=1)
    requests_per_second: float = Field(gt=0)


TRANSIENT_FAILURES = frozenset(
    {
        AdapterErrorKind.RATE_LIMITED,
        AdapterErrorKind.UNAVAILABLE,
        AdapterErrorKind.TIMEOUT,
        AdapterErrorKind.PROTOCOL,
        AdapterErrorKind.UNEXPECTED,
    }
)


class ResilienceGuard:
    def __init__(self, plugin_id: str, policy: ResiliencePolicy) -> None:
        self.plugin_id = plugin_id
        self.policy = policy
        self._lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(policy.max_concurrency)
        self._tokens = float(policy.burst)
        self._last_refill = monotonic()
        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._opened_at: float | None = None
        self._half_open_probe_active = False
        PLUGIN_CIRCUIT_STATE.labels(plugin=plugin_id).set(0)

    async def snapshot(self) -> ResilienceSnapshot:
        async with self._lock:
            retry_after = None
            if self._state == CircuitState.OPEN and self._opened_at is not None:
                retry_after = max(
                    0.0,
                    self.policy.recovery_seconds - (monotonic() - self._opened_at),
                )
            return ResilienceSnapshot(
                state=self._state,
                consecutive_failures=self._consecutive_failures,
                retry_after_seconds=retry_after,
                max_concurrency=self.policy.max_concurrency,
                requests_per_second=self.policy.requests_per_second,
            )

    async def _enter_circuit(self) -> None:
        async with self._lock:
            now = monotonic()
            if self._state == CircuitState.OPEN:
                opened_at = self._opened_at
                if opened_at is None:
                    opened_at = now
                    self._opened_at = opened_at
                if now - opened_at < self.policy.recovery_seconds:
                    raise AdapterError(
                        "plugin circuit is open",
                        kind=AdapterErrorKind.UNAVAILABLE,
                        retry=RetryDisposition.SAFE,
                        details={"circuit_state": CircuitState.OPEN.value},
                    )
                self._state = CircuitState.HALF_OPEN
                self._half_open_probe_active = False
                PLUGIN_CIRCUIT_STATE.labels(plugin=self.plugin_id).set(0.5)
            if self._state == CircuitState.HALF_OPEN:
                if self._half_open_probe_active:
                    raise AdapterError(
                        "plugin circuit recovery probe is already running",
                        kind=AdapterErrorKind.UNAVAILABLE,
                        retry=RetryDisposition.SAFE,
                        details={"circuit_state": CircuitState.HALF_OPEN.value},
                    )
                self._half_open_probe_active = True

    async def _acquire_rate_token(self) -> None:
        while True:
            async with self._lock:
                now = monotonic()
                elapsed = now - self._last_refill
                self._tokens = min(
                    float(self.policy.burst),
                    self._tokens + elapsed * self.policy.requests_per_second,
                )
                self._last_refill = now
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                wait_seconds = (1 - self._tokens) / self.policy.requests_per_second
            await asyncio.sleep(wait_seconds)

    async def _record_success(self) -> None:
        async with self._lock:
            self._state = CircuitState.CLOSED
            self._consecutive_failures = 0
            self._opened_at = None
            self._half_open_probe_active = False
            PLUGIN_CIRCUIT_STATE.labels(plugin=self.plugin_id).set(0)

    async def _record_failure(self, error: AdapterError) -> None:
        async with self._lock:
            if error.details.get("circuit_state") is not None:
                return
            if error.kind not in TRANSIENT_FAILURES:
                if self._state == CircuitState.HALF_OPEN:
                    self._state = CircuitState.CLOSED
                    self._consecutive_failures = 0
                    self._opened_at = None
                    self._half_open_probe_active = False
                    PLUGIN_CIRCUIT_STATE.labels(plugin=self.plugin_id).set(0)
                return
            self._consecutive_failures += 1
            if (
                self._state == CircuitState.HALF_OPEN
                or self._consecutive_failures >= self.policy.failure_threshold
            ):
                self._state = CircuitState.OPEN
                self._opened_at = monotonic()
                self._half_open_probe_active = False
                PLUGIN_CIRCUIT_STATE.labels(plugin=self.plugin_id).set(1)

    async def _record_cancellation(self) -> None:
        async with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                self._opened_at = monotonic()
                self._half_open_probe_active = False
                PLUGIN_CIRCUIT_STATE.labels(plugin=self.plugin_id).set(1)

    async def call(
        self,
        operation: str,
        factory: Callable[[], Awaitable[T]],
        *,
        unknown_on_started_timeout: bool = False,
        timeout_seconds: float | None = None,
    ) -> T:
        started_at = monotonic()
        queue_started_at = started_at
        slot_acquired = False
        operation_started = False
        outcome = "success"
        try:
            await self._enter_circuit()
            timeout = min(
                timeout_seconds or self.policy.timeout_seconds, self.policy.timeout_seconds
            )
            async with asyncio.timeout(timeout):
                await self._acquire_rate_token()
                await self._semaphore.acquire()
                slot_acquired = True
                PLUGIN_QUEUE_SECONDS.labels(plugin=self.plugin_id).observe(
                    monotonic() - queue_started_at
                )
                PLUGIN_IN_FLIGHT.labels(plugin=self.plugin_id).inc()
                operation_started = True
                result = await factory()
            await self._record_success()
            return result
        except TimeoutError as cause:
            outcome = "timeout"
            if operation_started and unknown_on_started_timeout:
                timeout_error: AdapterError = UnknownOutcome("plugin operation timed out")
            else:
                timeout_error = AdapterError(
                    "plugin operation timed out",
                    kind=AdapterErrorKind.TIMEOUT,
                    retry=RetryDisposition.SAFE,
                )
            await self._record_failure(timeout_error)
            raise timeout_error from cause
        except asyncio.CancelledError:
            outcome = "cancelled"
            await self._record_cancellation()
            raise
        except AdapterError as error:
            outcome = "error"
            await self._record_failure(error)
            raise
        except Exception:
            outcome = "error"
            unexpected_error = AdapterError(
                "plugin operation failed",
                kind=AdapterErrorKind.UNEXPECTED,
            )
            await self._record_failure(unexpected_error)
            raise unexpected_error from None
        finally:
            if slot_acquired:
                PLUGIN_IN_FLIGHT.labels(plugin=self.plugin_id).dec()
                self._semaphore.release()
            PLUGIN_CALLS.labels(
                plugin=self.plugin_id,
                operation=operation,
                outcome=outcome,
            ).inc()
            PLUGIN_CALL_SECONDS.labels(
                plugin=self.plugin_id,
                operation=operation,
            ).observe(monotonic() - started_at)


class ResilientAdapter:
    def __init__(self, adapter: Adapter, guard: ResilienceGuard) -> None:
        self.adapter = adapter
        self.guard = guard

    async def health(self) -> AdapterHealth:
        health = await self.guard.call("health", self.adapter.health)
        snapshot = await self.guard.snapshot()
        return health.model_copy(
            update={
                "details": {
                    **health.details,
                    "resilience": snapshot.model_dump(mode="json"),
                }
            }
        )

    async def plan(self, action: ActionDefinition, request: ActionRequest) -> AdapterPlan:
        return await self.guard.call(
            "plan",
            lambda: self.adapter.plan(action, request),
            timeout_seconds=action.timeout_seconds,
        )

    async def execute(self, action: ActionDefinition, request: ActionRequest) -> AdapterExecution:
        return await self.guard.call(
            "execute",
            lambda: self.adapter.execute(action, request),
            unknown_on_started_timeout=action.mutation,
            timeout_seconds=action.timeout_seconds,
        )

    async def observe(self, action: ActionDefinition, request: ActionRequest) -> AdapterObservation:
        return await self.guard.call(
            "observe",
            lambda: self.adapter.observe(action, request),
            timeout_seconds=action.timeout_seconds,
        )

    async def verify(
        self,
        action: ActionDefinition,
        request: ActionRequest,
        execution: AdapterExecution,
        observation: AdapterObservation,
    ) -> AdapterVerification:
        return await self.guard.call(
            "verify",
            lambda: self.adapter.verify(action, request, execution, observation),
            timeout_seconds=action.timeout_seconds,
        )

    async def cancel(
        self,
        action: ActionDefinition,
        request: ActionRequest,
        execution: AdapterExecution | None,
    ) -> AdapterCancellation:
        return await self.guard.call(
            "cancel",
            lambda: self.adapter.cancel(action, request, execution),
            timeout_seconds=action.timeout_seconds,
        )


class ResilientStatusProvider:
    def __init__(self, provider: StatusProvider, guard: ResilienceGuard) -> None:
        self.provider = provider
        self.guard = guard

    async def observe(self) -> StatusValue:
        value = await self.guard.call("status.observe", self.provider.observe)
        snapshot = await self.guard.snapshot()
        return value.model_copy(
            update={
                "details": {
                    **value.details,
                    "resilience": snapshot.model_dump(mode="json"),
                }
            }
        )
