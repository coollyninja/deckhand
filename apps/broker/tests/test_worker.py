from pathlib import Path
from uuid import uuid4

import pytest
from deckhand.adapters import AdapterRegistry, FakeAdapter
from deckhand.catalog import Catalog
from deckhand.models import ActionRequest, JobState, RequestContext, Subject, Target
from deckhand.store import Store
from deckhand.worker import Worker


@pytest.mark.asyncio
async def test_worker_executes_and_verifies_job(tmp_path: Path) -> None:
    root = Path(__file__).parents[3]
    store = Store(tmp_path / "worker.db")
    store.initialize()
    catalog = Catalog.from_path(root / "apps/broker/tests/fixtures/catalog")
    request = ActionRequest(
        action_id="test.resource.observe",
        action_version=1,
        target=Target(type="resource", id="example"),
        context=RequestContext(client="test"),
        idempotency_key=uuid4(),
    )
    subject = Subject(name="operator", device="mac", channel="tailscale")
    queued = store.create_job(request, subject)
    worker = Worker("test-worker", store, catalog, AdapterRegistry({"dh-core.fake": FakeAdapter()}))
    completed = await worker.run_once()
    assert completed is not None
    assert completed.id == queued.id
    assert completed.state == JobState.SUCCEEDED
    assert completed.result is not None
    assert completed.result["verified"] is True
