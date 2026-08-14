from pathlib import Path
from uuid import uuid4

import pytest
from deckhand.adapters import (
    AdapterCancellation,
    AdapterExecution,
    AdapterRegistry,
    CancellationDisposition,
    FakeAdapter,
)
from deckhand.cancellation import CancellationError, Canceller
from deckhand.catalog import Catalog
from deckhand.models import (
    ActionDefinition,
    ActionRequest,
    JobState,
    RequestContext,
    Subject,
    Target,
)
from deckhand.store import Store


class CancellableAdapter(FakeAdapter):
    async def cancel(
        self,
        action: ActionDefinition,
        request: ActionRequest,
        execution: AdapterExecution | None,
    ) -> AdapterCancellation:
        return AdapterCancellation(
            disposition=CancellationDisposition.CANCELLED,
            details={"had_execution_reference": execution is not None},
        )


def setup_job(tmp_path: Path) -> tuple[Store, Catalog, Subject, str]:
    root = Path(__file__).parents[3]
    store = Store(tmp_path / "cancel.db")
    store.initialize()
    catalog = Catalog.from_path(root / "apps/broker/tests/fixtures/catalog")
    subject = Subject(name="operator", device="mac", channel="tailscale")
    request = ActionRequest(
        action_id="test.resource.observe",
        action_version=1,
        target=Target(type="resource", id="example"),
        context=RequestContext(client="test"),
        idempotency_key=uuid4(),
    )
    job = store.create_job(request, subject)
    return store, catalog, subject, job.id


@pytest.mark.asyncio
async def test_running_job_uses_adapter_cancellation(tmp_path: Path) -> None:
    store, catalog, subject, job_id = setup_job(tmp_path)
    store.claim_next_job("worker")
    canceller = Canceller(store, catalog, AdapterRegistry({"dh-core.fake": CancellableAdapter()}))
    cancelled = await canceller.cancel(job_id, subject)
    assert cancelled.state == JobState.CANCELLED
    assert cancelled.result is not None
    assert cancelled.result["cancellation"]["disposition"] == "cancelled"


@pytest.mark.asyncio
async def test_job_cancellation_is_bound_to_subject_and_device(tmp_path: Path) -> None:
    store, catalog, _, job_id = setup_job(tmp_path)
    canceller = Canceller(store, catalog, AdapterRegistry({"dh-core.fake": FakeAdapter()}))
    with pytest.raises(CancellationError, match="another subject"):
        await canceller.cancel(
            job_id, Subject(name="other", device="other-device", channel="tailscale")
        )
