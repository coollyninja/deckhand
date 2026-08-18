from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RiskClass(StrEnum):
    READ = "read"
    LOCAL = "local"
    REVERSIBLE = "reversible"
    DISRUPTIVE = "disruptive"
    DESTRUCTIVE = "destructive"
    SAFETY_SENSITIVE = "safety_sensitive"


class JobState(StrEnum):
    RECEIVED = "received"
    VALIDATED = "validated"
    AUTHORIZED = "authorized"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    QUEUED = "queued"
    RUNNING = "running"
    VERIFYING = "verifying"
    SUCCEEDED = "succeeded"
    DENIED = "denied"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    FAILED = "failed"
    UNKNOWN_OUTCOME = "unknown_outcome"


class RetryDisposition(StrEnum):
    NEVER = "never"
    SAFE = "safe"
    RECONCILE_FIRST = "reconcile_first"


TERMINAL_JOB_STATES = frozenset(
    {
        JobState.SUCCEEDED,
        JobState.DENIED,
        JobState.REJECTED,
        JobState.EXPIRED,
        JobState.CANCELLED,
        JobState.FAILED,
    }
)


class ConfirmationMode(StrEnum):
    NONE = "none"
    CONFIRM = "confirm"
    HOLD = "hold"
    TYPED = "typed"
    DUAL_CONTROL = "dual_control"
    POLICY = "policy"


class Target(StrictModel):
    type: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    id: str = Field(min_length=1, max_length=256)


class RequestContext(StrictModel):
    profile: str | None = Field(default=None, max_length=128)
    frontmost_app: str | None = Field(default=None, max_length=256)
    client: str = Field(min_length=1, max_length=128)
    control: str | None = Field(default=None, max_length=128)


class ActionRequest(StrictModel):
    action_id: str = Field(pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
    action_version: int = Field(ge=1)
    target: Target
    parameters: dict[str, Any] = Field(default_factory=dict)
    context: RequestContext
    idempotency_key: UUID
    dry_run: bool = False
    confirmation_token: str | None = Field(default=None, min_length=32, max_length=256)
    # Typed-challenge response (for ConfirmationMode.TYPED). Proof-of-approval,
    # not part of request identity, so excluded from both digests.
    confirmation_response: str | None = Field(default=None, max_length=256)


class ActionDefinition(StrictModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
    version: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=1024)
    risk_class: RiskClass
    plugin: str = Field(pattern=r"^dh-[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
    adapter: str = Field(
        pattern=r"^dh-[a-z][a-z0-9]*(?:-[a-z0-9]+)*\.[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$"
    )
    target_types: list[str] = Field(min_length=1)
    parameter_schema: dict[str, Any]
    policy_action: str
    confirmation: ConfirmationMode
    timeout_seconds: int = Field(gt=0, le=3600)
    idempotency: str
    mutation: bool

    @model_validator(mode="after")
    def mutation_matches_risk(self) -> "ActionDefinition":
        if self.risk_class == RiskClass.READ and self.mutation:
            raise ValueError("read actions cannot be mutations")
        return self


class Subject(StrictModel):
    name: str
    device: str
    channel: str


class PolicyDecision(StrictModel):
    allow: bool
    reason: str = ""
    required_confirmation: ConfirmationMode = ConfirmationMode.NONE
    decision_id: str | None = None


class ConfirmationChallenge(StrictModel):
    id: str
    token: str = Field(min_length=32, max_length=256)
    mode: ConfirmationMode
    expires_at: datetime
    prompt: str


class ConfirmationSubmission(StrictModel):
    token: str = Field(min_length=32, max_length=256)
    response: str | None = Field(default=None, max_length=256)


class PlanView(StrictModel):
    request_digest: str
    # The authority-bearing digest a confirmation is bound to. Clients treat this
    # as the explicit contract: the same (action, version, target, parameters)
    # that produced this plan is what the confirmation authorizes at execute time.
    confirmation_digest: str
    action_id: str
    action_version: int
    target: Target
    mutation: bool
    executable: bool
    steps: list[str]
    required_confirmation: ConfirmationMode
    confirmation: ConfirmationChallenge | None = None
    denial_reason: str | None = None


class JobError(StrictModel):
    code: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    message: str = Field(min_length=1, max_length=1024)
    retry: RetryDisposition = RetryDisposition.NEVER
    reconciliation_required: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


class JobView(StrictModel):
    id: str
    state: JobState
    action_id: str
    target: Target
    created_at: datetime
    updated_at: datetime
    result: dict[str, Any] | None = None
    error: JobError | None = None


class StatusValue(StrictModel):
    state: str
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    stale_after_seconds: int = Field(default=30, ge=1)
    details: dict[str, Any] = Field(default_factory=dict)
