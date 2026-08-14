from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from pydantic import Field

from .models import ActionDefinition, ActionRequest, JobError, RetryDisposition, StrictModel


class AdapterErrorKind(StrEnum):
    CONFIGURATION = "configuration"
    AUTHENTICATION = "authentication"
    AUTHORIZATION = "authorization"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    RATE_LIMITED = "rate_limited"
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    PROTOCOL = "protocol"
    UNEXPECTED = "unexpected"


class AdapterError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        kind: AdapterErrorKind = AdapterErrorKind.UNEXPECTED,
        retry: RetryDisposition = RetryDisposition.NEVER,
        reconciliation_required: bool = False,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.retry = retry
        self.reconciliation_required = reconciliation_required
        self.details = dict(details or {})

    def as_job_error(self) -> JobError:
        return JobError(
            code=self.kind.value,
            message=str(self),
            retry=self.retry,
            reconciliation_required=self.reconciliation_required,
            details=self.details,
        )


class UnknownOutcome(AdapterError):
    def __init__(self, message: str, *, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(
            message,
            kind=AdapterErrorKind.TIMEOUT,
            retry=RetryDisposition.RECONCILE_FIRST,
            reconciliation_required=True,
            details=details,
        )


class AdapterPlan(StrictModel):
    steps: list[str] = Field(min_length=1)


class AdapterHealthState(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class AdapterHealth(StrictModel):
    state: AdapterHealthState
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    details: dict[str, Any] = Field(default_factory=dict)


class AdapterExecution(StrictModel):
    reference: str | None = Field(default=None, max_length=512)
    details: dict[str, Any] = Field(default_factory=dict)


class AdapterObservation(StrictModel):
    state: str = Field(min_length=1, max_length=128)
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    details: dict[str, Any] = Field(default_factory=dict)


class AdapterVerification(StrictModel):
    satisfied: bool
    details: dict[str, Any] = Field(default_factory=dict)


class CancellationDisposition(StrEnum):
    CANCELLED = "cancelled"
    NOT_SUPPORTED = "not_supported"
    ALREADY_TERMINAL = "already_terminal"
    UNKNOWN = "unknown"


class AdapterCancellation(StrictModel):
    disposition: CancellationDisposition
    details: dict[str, Any] = Field(default_factory=dict)


@runtime_checkable
class Adapter(Protocol):
    async def health(self) -> AdapterHealth: ...

    async def plan(self, action: ActionDefinition, request: ActionRequest) -> AdapterPlan: ...

    async def execute(
        self, action: ActionDefinition, request: ActionRequest
    ) -> AdapterExecution: ...

    async def observe(
        self, action: ActionDefinition, request: ActionRequest
    ) -> AdapterObservation: ...

    async def verify(
        self,
        action: ActionDefinition,
        request: ActionRequest,
        execution: AdapterExecution,
        observation: AdapterObservation,
    ) -> AdapterVerification: ...

    async def cancel(
        self,
        action: ActionDefinition,
        request: ActionRequest,
        execution: AdapterExecution | None,
    ) -> AdapterCancellation: ...


class FakeAdapter:
    async def health(self) -> AdapterHealth:
        return AdapterHealth(state=AdapterHealthState.HEALTHY, details={"source": "fake"})

    async def plan(self, action: ActionDefinition, request: ActionRequest) -> AdapterPlan:
        return AdapterPlan(steps=[f"observe {request.target.type}:{request.target.id}"])

    async def execute(self, action: ActionDefinition, request: ActionRequest) -> AdapterExecution:
        return AdapterExecution(
            reference=f"fake:{request.idempotency_key}",
            details={"target": request.target.model_dump(), "source": "fake"},
        )

    async def observe(self, action: ActionDefinition, request: ActionRequest) -> AdapterObservation:
        return AdapterObservation(
            state="healthy",
            details={"target": request.target.model_dump(), "source": "fake"},
        )

    async def verify(
        self,
        action: ActionDefinition,
        request: ActionRequest,
        execution: AdapterExecution,
        observation: AdapterObservation,
    ) -> AdapterVerification:
        return AdapterVerification(
            satisfied=observation.state == "healthy",
            details={"execution_reference": execution.reference},
        )

    async def cancel(
        self,
        action: ActionDefinition,
        request: ActionRequest,
        execution: AdapterExecution | None,
    ) -> AdapterCancellation:
        return AdapterCancellation(disposition=CancellationDisposition.ALREADY_TERMINAL)


class DisabledMutationAdapter:
    def __init__(self, name: str) -> None:
        self.name = name

    async def health(self) -> AdapterHealth:
        return AdapterHealth(
            state=AdapterHealthState.UNAVAILABLE,
            details={"reason": "configuration_required"},
        )

    async def plan(self, action: ActionDefinition, request: ActionRequest) -> AdapterPlan:
        return AdapterPlan(
            steps=[f"validate {self.name} target", f"execute {action.id}", "verify postcondition"]
        )

    async def execute(self, action: ActionDefinition, request: ActionRequest) -> AdapterExecution:
        raise AdapterError(
            f"{self.name} adapter requires approved production inventory",
            kind=AdapterErrorKind.CONFIGURATION,
        )

    async def observe(self, action: ActionDefinition, request: ActionRequest) -> AdapterObservation:
        raise AdapterError(
            f"{self.name} adapter requires approved production inventory",
            kind=AdapterErrorKind.CONFIGURATION,
        )

    async def verify(
        self,
        action: ActionDefinition,
        request: ActionRequest,
        execution: AdapterExecution,
        observation: AdapterObservation,
    ) -> AdapterVerification:
        raise AdapterError(
            f"{self.name} adapter cannot verify an unconfigured target",
            kind=AdapterErrorKind.CONFIGURATION,
        )

    async def cancel(
        self,
        action: ActionDefinition,
        request: ActionRequest,
        execution: AdapterExecution | None,
    ) -> AdapterCancellation:
        return AdapterCancellation(disposition=CancellationDisposition.NOT_SUPPORTED)


class AdapterRegistry:
    def __init__(self, adapters: dict[str, Adapter] | None = None) -> None:
        self._adapters: dict[str, Adapter] = adapters or {}

    def register(self, name: str, adapter: Adapter) -> None:
        if name in self._adapters:
            raise ValueError(f"adapter {name!r} is already registered")
        self._adapters[name] = adapter

    def get(self, name: str) -> Adapter:
        try:
            return self._adapters[name]
        except KeyError as error:
            raise AdapterError(f"adapter {name!r} is not registered") from error

    def items(self) -> tuple[tuple[str, Adapter], ...]:
        return tuple(sorted(self._adapters.items()))

    async def health(self) -> dict[str, AdapterHealth]:
        result: dict[str, AdapterHealth] = {}
        for name, adapter in self.items():
            try:
                result[name] = await adapter.health()
            except AdapterError as error:
                result[name] = AdapterHealth(
                    state=AdapterHealthState.UNAVAILABLE,
                    details={"error_code": error.kind.value, "retry": error.retry.value},
                )
        return result
