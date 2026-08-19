"""Adapter lifecycle conformance suite (lifecycle v1) — the frozen contract.

Phase 0 of the Deckhand-Ganglion transition: this is the single, versioned,
tier-agnostic definition of what it means to be a conformant Deckhand adapter.
Every isolation tier — ``in_process``, ``sidecar``, and the future ``wasm``
(Ganglion) tier — must pass the IDENTICAL suite before it can be enabled
anywhere. The suite is the contract; prose is not.

``assert_adapter_conformance(adapter)`` drives any object implementing the
``Adapter`` protocol through the six-operation lifecycle
(health/plan/execute/observe/verify/cancel) and asserts the ADR-0002 semantic
invariants: structured returns, observe-before-success, verification gating,
typed cancellation, and bounded error kinds.

Rule from Phase 0 onward: changing the lifecycle contract requires bumping
``LIFECYCLE_VERSION`` here, ``adapter-lifecycle.schema.json``, and the
``deckhand-adapter.wit`` package version in the SAME change. Contract drift then
becomes a visible, reviewed event rather than an accident.
"""

from __future__ import annotations

from uuid import uuid4

from .adapters import (
    Adapter,
    AdapterCancellation,
    AdapterError,
    AdapterErrorKind,
    AdapterExecution,
    AdapterHealth,
    AdapterHealthState,
    AdapterObservation,
    AdapterPlan,
    AdapterVerification,
    CancellationDisposition,
)
from .models import (
    ActionDefinition,
    ActionRequest,
    ConfirmationMode,
    RequestContext,
    RetryDisposition,
    RiskClass,
    Target,
)

# The frozen lifecycle contract version. Bump in lockstep with
# adapter-lifecycle.schema.json and deckhand-adapter.wit.
LIFECYCLE_VERSION = "1.0.0"

_VALID_ERROR_KINDS = {kind.value for kind in AdapterErrorKind}
_VALID_RETRY = {disposition.value for disposition in RetryDisposition}
_VALID_CANCEL = {disposition.value for disposition in CancellationDisposition}


class ConformanceError(AssertionError):
    """A conformance invariant was violated by the adapter under test."""


def read_action(*, adapter_id: str = "dh-core.fake") -> ActionDefinition:
    """A minimal read (non-mutating) action for conformance."""
    plugin = adapter_id.split(".", 1)[0]
    return ActionDefinition(
        id="conformance.resource.observe",
        version=1,
        title="Conformance observe",
        description="Read action used by the lifecycle conformance suite.",
        risk_class=RiskClass.READ,
        plugin=plugin,
        adapter=adapter_id,
        target_types=["resource"],
        parameter_schema={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "additionalProperties": False,
            "properties": {},
        },
        policy_action="conformance.resource.read",
        confirmation=ConfirmationMode.NONE,
        timeout_seconds=10,
        idempotency="read-only",
        mutation=False,
    )


def conformance_request(action: ActionDefinition) -> ActionRequest:
    return ActionRequest(
        action_id=action.id,
        action_version=action.version,
        target=Target(type="resource", id="example"),
        parameters={},
        context=RequestContext(client="conformance"),
        idempotency_key=uuid4(),
    )


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ConformanceError(message)


def assert_error_shape(error: AdapterError) -> None:
    """Assert an AdapterError carries a bounded, structured shape (ADR-0002)."""
    job_error = error.as_job_error()
    _require(
        job_error.code in _VALID_ERROR_KINDS,
        f"adapter error kind {job_error.code!r} is not a bounded AdapterErrorKind",
    )
    _require(
        job_error.retry.value in _VALID_RETRY,
        f"adapter retry disposition {job_error.retry!r} is not bounded",
    )
    _require(
        isinstance(job_error.reconciliation_required, bool), "reconciliation_required must be bool"
    )


async def assert_adapter_conformance(
    adapter: Adapter,
    *,
    adapter_id: str = "dh-core.fake",
    expect_health: bool = True,
    action: ActionDefinition | None = None,
    request: ActionRequest | None = None,
) -> None:
    """Drive an adapter through the full lifecycle and assert the frozen contract.

    Tier-agnostic: pass an in-process adapter, a SidecarAdapter, or a future
    GanglionAdapter — the assertions are identical. Raises ConformanceError on any
    violation. A tier whose fixture pins a specific action/target (e.g. the
    sidecar fake) may pass its own ``action``/``request``; the assertions are the
    same regardless of which concrete action drives them.
    """
    action = action or read_action(adapter_id=adapter_id)
    request = request or conformance_request(action)

    # --- health ---
    health = await adapter.health()
    _require(isinstance(health, AdapterHealth), "health() must return AdapterHealth")
    _require(
        isinstance(health.state, AdapterHealthState),
        "health.state must be an AdapterHealthState",
    )
    if expect_health:
        _require(
            health.state == AdapterHealthState.HEALTHY,
            f"expected healthy adapter, got {health.state}",
        )

    # --- plan: non-mutating, returns ordered steps ---
    plan = await adapter.plan(action, request)
    _require(isinstance(plan, AdapterPlan), "plan() must return AdapterPlan")
    _require(len(plan.steps) >= 1, "plan must return at least one step")

    # --- execute: returns a structured execution reference ---
    execution = await adapter.execute(action, request)
    _require(isinstance(execution, AdapterExecution), "execute() must return AdapterExecution")

    # --- observe: independent read of target state ---
    observation = await adapter.observe(action, request)
    _require(
        isinstance(observation, AdapterObservation),
        "observe() must return AdapterObservation",
    )
    _require(len(observation.state) >= 1, "observation.state must be non-empty")

    # --- verify: gates success on the observation (observe-before-success) ---
    verification = await adapter.verify(action, request, execution, observation)
    _require(
        isinstance(verification, AdapterVerification),
        "verify() must return AdapterVerification",
    )
    _require(isinstance(verification.satisfied, bool), "verification.satisfied must be bool")

    # --- cancel: typed disposition, never an ambiguous success ---
    cancellation = await adapter.cancel(action, request, execution)
    _require(
        isinstance(cancellation, AdapterCancellation),
        "cancel() must return AdapterCancellation",
    )
    _require(
        cancellation.disposition.value in _VALID_CANCEL,
        f"cancel disposition {cancellation.disposition!r} is not bounded",
    )
