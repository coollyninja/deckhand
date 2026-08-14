import pytest
from deckhand.models import JobState
from deckhand.state_machine import InvalidTransition, require_transition


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (JobState.RECEIVED, JobState.VALIDATED),
        (JobState.QUEUED, JobState.RUNNING),
        (JobState.RUNNING, JobState.VERIFYING),
        (JobState.VERIFYING, JobState.SUCCEEDED),
        (JobState.UNKNOWN_OUTCOME, JobState.VERIFYING),
    ],
)
def test_allowed_transitions(current: JobState, target: JobState) -> None:
    require_transition(current, target)


def test_terminal_state_cannot_transition() -> None:
    with pytest.raises(InvalidTransition):
        require_transition(JobState.SUCCEEDED, JobState.RUNNING)
