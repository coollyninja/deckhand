from pathlib import Path
from uuid import uuid4

from deckhand.models import (
    ActionRequest,
    ConfirmationMode,
    RequestContext,
    Subject,
    Target,
)
from deckhand.store import Store


def action_request() -> ActionRequest:
    return ActionRequest(
        action_id="pve.vm.ensure_running",
        action_version=1,
        target=Target(type="pve_vm", id="210"),
        context=RequestContext(client="test"),
        idempotency_key=uuid4(),
    )


def test_confirmation_is_exact_single_use(tmp_path: Path) -> None:
    store = Store(tmp_path / "store.db")
    store.initialize()
    request = action_request()
    subject = Subject(name="operator", device="mac", channel="tailscale")
    challenge = store.create_confirmation(
        request, subject, ConfirmationMode.CONFIRM, "Confirm target"
    )
    assert store.consume_confirmation(request, subject, challenge.token)
    assert not store.consume_confirmation(request, subject, challenge.token)


def test_confirmation_is_bound_to_device(tmp_path: Path) -> None:
    store = Store(tmp_path / "store.db")
    store.initialize()
    request = action_request()
    subject = Subject(name="operator", device="mac", channel="tailscale")
    challenge = store.create_confirmation(
        request, subject, ConfirmationMode.CONFIRM, "Confirm target"
    )
    other_device = Subject(name="operator", device="other", channel="tailscale")
    assert not store.consume_confirmation(request, other_device, challenge.token)


def test_audit_chain_verifies(tmp_path: Path) -> None:
    store = Store(tmp_path / "store.db")
    store.initialize()
    request = action_request()
    subject = Subject(name="operator", device="mac", channel="tailscale")
    store.create_job(request, subject)
    assert store.verify_audit_chain()
