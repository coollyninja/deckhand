"""A malformed sidecar response after a started mutation must become an
UNKNOWN_OUTCOME (reconcilable), not a raw ValidationError / hard FAILED."""

from typing import Any
from uuid import uuid4

import pytest
from deckhand.adapters import UnknownOutcome
from deckhand.models import (
    ActionDefinition,
    ActionRequest,
    ConfirmationMode,
    RequestContext,
    RiskClass,
    Target,
)
from deckhand.sidecar import SidecarAdapter, SidecarTransportError

_MUTATION = ActionDefinition(
    id="test.resource.ensure_active",
    version=1,
    title="Ensure active",
    description="Mutation for malformed-response test.",
    risk_class=RiskClass.REVERSIBLE,
    plugin="dh-sidecar-test",
    adapter="dh-sidecar-test.read",
    target_types=["resource"],
    parameter_schema={"type": "object", "additionalProperties": False},
    policy_action="test.resource.mutate",
    confirmation=ConfirmationMode.CONFIRM,
    timeout_seconds=5,
    idempotency="ensure-state",
    mutation=True,
)


def _request() -> ActionRequest:
    return ActionRequest(
        action_id=_MUTATION.id,
        action_version=1,
        target=Target(type="resource", id="example"),
        context=RequestContext(client="test"),
        idempotency_key=uuid4(),
    )


class _MalformedClient:
    """A client whose framed response is well-formed at the envelope level but
    whose result body does not match the expected lifecycle model."""

    async def call(self, operation: Any, payload: Any) -> dict[str, Any]:
        return {"totally": "wrong", "shape": True}


@pytest.mark.asyncio
async def test_malformed_execute_result_becomes_unknown_outcome() -> None:
    adapter = SidecarAdapter("dh-sidecar-test.read", _MalformedClient())  # type: ignore[arg-type]
    with pytest.raises(UnknownOutcome) as captured:
        await adapter.execute(_MUTATION, _request())
    assert captured.value.reconciliation_required is True


@pytest.mark.asyncio
async def test_malformed_read_result_is_transport_error_not_crash() -> None:
    read_action = _MUTATION.model_copy(
        update={
            "id": "test.resource.observe",
            "risk_class": RiskClass.READ,
            "mutation": False,
            "confirmation": ConfirmationMode.NONE,
        }
    )
    read_request = _request().model_copy(update={"action_id": read_action.id})
    adapter = SidecarAdapter("dh-sidecar-test.read", _MalformedClient())  # type: ignore[arg-type]
    # For a non-mutation, a malformed result is a typed transport error (safe to
    # retry), never a raw pydantic ValidationError escaping the boundary.
    with pytest.raises(SidecarTransportError):
        await adapter.observe(read_action, read_request)
