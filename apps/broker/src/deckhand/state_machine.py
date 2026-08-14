from .models import JobState


class InvalidTransition(ValueError):
    pass


ALLOWED_TRANSITIONS: dict[JobState, frozenset[JobState]] = {
    JobState.RECEIVED: frozenset({JobState.VALIDATED, JobState.REJECTED}),
    JobState.VALIDATED: frozenset({JobState.AUTHORIZED, JobState.DENIED, JobState.REJECTED}),
    JobState.AUTHORIZED: frozenset({JobState.AWAITING_CONFIRMATION, JobState.QUEUED}),
    JobState.AWAITING_CONFIRMATION: frozenset(
        {JobState.QUEUED, JobState.EXPIRED, JobState.CANCELLED}
    ),
    JobState.QUEUED: frozenset({JobState.RUNNING, JobState.CANCELLED, JobState.EXPIRED}),
    JobState.RUNNING: frozenset(
        {JobState.VERIFYING, JobState.FAILED, JobState.UNKNOWN_OUTCOME, JobState.CANCELLED}
    ),
    JobState.VERIFYING: frozenset({JobState.SUCCEEDED, JobState.FAILED, JobState.UNKNOWN_OUTCOME}),
    JobState.UNKNOWN_OUTCOME: frozenset({JobState.VERIFYING, JobState.SUCCEEDED, JobState.FAILED}),
    JobState.SUCCEEDED: frozenset(),
    JobState.DENIED: frozenset(),
    JobState.REJECTED: frozenset(),
    JobState.EXPIRED: frozenset(),
    JobState.CANCELLED: frozenset(),
    JobState.FAILED: frozenset(),
}


def require_transition(current: JobState, target: JobState) -> None:
    if target not in ALLOWED_TRANSITIONS[current]:
        raise InvalidTransition(f"invalid job transition {current.value} -> {target.value}")
