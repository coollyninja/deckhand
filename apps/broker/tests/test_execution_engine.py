"""Execution-engine correctness: fencing leases, cancel TOCTOU, queue TTL,
reconcile budget, and supervision backoff."""

from pathlib import Path
from uuid import uuid4

import pytest
from deckhand.models import ActionRequest, JobState, RequestContext, Subject, Target
from deckhand.state_machine import InvalidTransition
from deckhand.store import ClaimedJob, StaleLease, Store
from deckhand.supervision import backoff_delay


def _request(key: str | None = None) -> ActionRequest:
    return ActionRequest(
        action_id="test.resource.observe",
        action_version=1,
        target=Target(type="resource", id="example"),
        context=RequestContext(client="mac", control="main:r1c1"),
        idempotency_key=key or uuid4(),
    )


def _store(tmp_path: Path) -> Store:
    store = Store(tmp_path / "store.db")
    store.initialize()
    return store


def _subject() -> Subject:
    return Subject(name="operator", device="mac", channel="mgmt-mtls")


def test_claim_returns_fencing_token(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create_job(_request(), _subject())
    claimed = store.claim_next_job("worker-1")
    assert isinstance(claimed, ClaimedJob)
    assert claimed.lease_token
    assert claimed.job.state == JobState.RUNNING


def test_transition_with_correct_fence_succeeds(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create_job(_request(), _subject())
    claimed = store.claim_next_job("worker-1")
    assert claimed is not None
    view = store.transition_job(claimed.job.id, JobState.VERIFYING, fence=claimed.lease_token)
    assert view.state == JobState.VERIFYING


def test_transition_with_stale_fence_is_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create_job(_request(), _subject())
    claimed = store.claim_next_job("worker-1")
    assert claimed is not None
    # A holder presenting the wrong fence (e.g. a zombie worker whose lease was
    # reclaimed) cannot write.
    with pytest.raises(StaleLease):
        store.transition_job(claimed.job.id, JobState.VERIFYING, fence="not-the-real-token")


def test_expired_lease_reclaim_locks_out_original_holder(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create_job(_request(), _subject())
    # Worker claims with an already-expired lease, then stalls.
    first = store.claim_next_job("worker-1", lease_seconds=-1)
    assert first is not None
    # The reconciler reclaims the abandoned RUNNING job → UNKNOWN_OUTCOME, nulling
    # the lease token.
    assert store.expire_leases() == 1
    # The zombie worker's fence is now stale; it cannot complete the job.
    with pytest.raises(StaleLease):
        store.transition_job(first.job.id, JobState.SUCCEEDED, fence=first.lease_token)


def test_expected_state_guard_prevents_toctou_write(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create_job(_request(), _subject())
    # Job is claimed → RUNNING. A cancel that decided from a stale QUEUED read
    # must not land on the now-RUNNING job.
    claimed = store.claim_next_job("worker-1")
    assert claimed is not None
    with pytest.raises(StaleLease):
        store.transition_job(claimed.job.id, JobState.CANCELLED, expected_state=JobState.QUEUED)


def test_lease_sized_from_action_timeout(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create_job(_request(), _subject())
    seen: list[ActionRequest] = []

    def lease_for(request: ActionRequest) -> int:
        seen.append(request)
        return 123

    claimed = store.claim_next_job("worker-1", lease_for=lease_for)
    assert claimed is not None
    assert len(seen) == 1  # sized from the claimed job's own request


def test_queue_ttl_expires_stale_queued_jobs(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create_job(_request(), _subject())
    # TTL of 0 → everything already-created is stale.
    expired = store.expire_stale_queued(ttl_seconds=0)
    assert expired == 1
    assert store.list_jobs(JobState.EXPIRED)[0].state == JobState.EXPIRED


def test_reconcile_budget_stops_after_max_attempts(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create_job(_request(), _subject())
    claimed = store.claim_next_job("worker-1")
    assert claimed is not None
    store.transition_job(claimed.job.id, JobState.VERIFYING, fence=claimed.lease_token)
    store.transition_job(claimed.job.id, JobState.UNKNOWN_OUTCOME, fence=claimed.lease_token)

    # With a budget of 3, the job is a candidate exactly 3 times, then parked.
    total = 0
    for _ in range(6):
        total += len(store.claim_reconcile_candidates(max_attempts=3))
    assert total == 3
    events = store.list_audit_events()
    assert any(e["event_type"] == "job.reconcile_exhausted" for e in events)


def test_backoff_is_bounded_and_grows() -> None:
    d1 = backoff_delay(1, jitter=False)
    d5 = backoff_delay(5, jitter=False)
    d100 = backoff_delay(100, jitter=False)
    assert d1 < d5 <= d100
    assert d100 <= 30.0  # capped


def test_invalid_transition_still_raises(tmp_path: Path) -> None:
    # Fencing must not swallow genuinely illegal transitions.
    store = _store(tmp_path)
    store.create_job(_request(), _subject())
    job = store.list_jobs(JobState.QUEUED)[0]
    with pytest.raises(InvalidTransition):
        store.transition_job(job.id, JobState.SUCCEEDED)  # QUEUED→SUCCEEDED is illegal
