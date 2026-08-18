"""Confirmation binding: the plan→execute round trip must actually work.

The shipped flow was impossible because the confirmation digest bound dry_run and
idempotency_key, which legitimately differ between plan and execute. These tests
lock in the fix: a confirmation is bound to the authority-bearing subset only.
"""

from pathlib import Path
from uuid import uuid4

from deckhand.digests import confirmation_digest, request_digest
from deckhand.models import ActionRequest, ConfirmationMode, RequestContext, Subject, Target
from deckhand.store import Store


def _request(
    *, dry_run: bool = False, key: str | None = None, control: str = "main:r2c4"
) -> ActionRequest:
    return ActionRequest(
        action_id="test.resource.ensure_active",
        action_version=1,
        target=Target(type="resource", id="example"),
        parameters={},
        context=RequestContext(client="mac", control=control),
        idempotency_key=key or uuid4(),
        dry_run=dry_run,
    )


def test_confirmation_digest_ignores_dry_run_and_key() -> None:
    plan_req = _request(dry_run=True, key=str(uuid4()))
    exec_req = _request(dry_run=False, key=str(uuid4()))
    # Different full requests (dry_run + key differ)...
    assert request_digest(plan_req) != request_digest(exec_req)
    # ...but the SAME authority-bearing confirmation digest.
    assert confirmation_digest(plan_req) == confirmation_digest(exec_req)


def test_confirmation_digest_changes_with_target_or_params() -> None:
    base = _request()
    other_target = ActionRequest(
        action_id="test.resource.ensure_active",
        action_version=1,
        target=Target(type="resource", id="different"),
        context=RequestContext(client="mac", control="main:r2c4"),
        idempotency_key=uuid4(),
    )
    assert confirmation_digest(base) != confirmation_digest(other_target)


def test_plan_time_confirmation_authorizes_execute_time_request(tmp_path: Path) -> None:
    store = Store(tmp_path / "store.db")
    store.initialize()
    subject = Subject(name="operator", device="mac", channel="mgmt-mtls")
    plan_req = _request(dry_run=True, key=str(uuid4()))
    challenge = store.create_confirmation(plan_req, subject, ConfirmationMode.CONFIRM, "Confirm")
    # The execute request differs in dry_run and idempotency_key — the real client
    # behaviour that used to make this impossible.
    exec_req = _request(dry_run=False, key=str(uuid4()))
    assert store.consume_confirmation(exec_req, subject, challenge.token)


def test_typed_confirmation_requires_correct_response(tmp_path: Path) -> None:
    store = Store(tmp_path / "store.db")
    store.initialize()
    subject = Subject(name="operator", device="mac", channel="mgmt-mtls")
    request = _request()
    challenge = store.create_confirmation(request, subject, ConfirmationMode.TYPED, "Type target")
    # Wrong typed response is rejected...
    assert not store.consume_confirmation(request, subject, challenge.token, response="wrong")
    # ...correct response (the target id) is accepted.
    assert store.consume_confirmation(request, subject, challenge.token, response="example")


def test_confirmation_bound_to_control_location(tmp_path: Path) -> None:
    store = Store(tmp_path / "store.db")
    store.initialize()
    subject = Subject(name="operator", device="mac", channel="mgmt-mtls")
    issued = _request(control="main:r2c4")
    challenge = store.create_confirmation(issued, subject, ConfirmationMode.CONFIRM, "Confirm")
    # Same action/target/params but pressed from a different key → rejected.
    from_other_key = _request(control="danger:r1c1")
    assert not store.consume_confirmation(from_other_key, subject, challenge.token)
    # Same control → accepted.
    assert store.consume_confirmation(issued, subject, challenge.token)


def test_confirmation_cancel_prevents_use(tmp_path: Path) -> None:
    store = Store(tmp_path / "store.db")
    store.initialize()
    subject = Subject(name="operator", device="mac", channel="mgmt-mtls")
    request = _request()
    challenge = store.create_confirmation(request, subject, ConfirmationMode.CONFIRM, "Confirm")
    assert store.cancel_confirmation(challenge.id, subject)
    assert not store.consume_confirmation(request, subject, challenge.token)
    # Cancelling again is a no-op.
    assert not store.cancel_confirmation(challenge.id, subject)


def test_rejected_confirmations_are_audited(tmp_path: Path) -> None:
    store = Store(tmp_path / "store.db")
    store.initialize()
    subject = Subject(name="operator", device="mac", channel="mgmt-mtls")
    request = _request()
    store.create_confirmation(request, subject, ConfirmationMode.CONFIRM, "Confirm")
    # Wrong token → rejected AND recorded in the audit log (replay attempts are visible).
    assert not store.consume_confirmation(request, subject, "x" * 43)
    events = store.list_audit_events()
    assert any(e["event_type"] == "confirmation.rejected" for e in events)
    assert store.verify_audit_chain()
