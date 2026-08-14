from pathlib import Path
from uuid import uuid4

import pytest
from deckhand.adapters import AdapterRegistry, FakeAdapter
from deckhand.catalog import Catalog
from deckhand.models import ActionRequest, JobState, RequestContext, Subject, Target
from deckhand.reconciler import Reconciler
from deckhand.store import Store


@pytest.mark.asyncio
async def test_unknown_outcome_is_observed_before_success(tmp_path: Path) -> None:
    root = Path(__file__).parents[3]
    store = Store(tmp_path / "reconcile.db")
    store.initialize()
    request = ActionRequest(
        action_id="test.resource.observe",
        action_version=1,
        target=Target(type="resource", id="example"),
        context=RequestContext(client="test"),
        idempotency_key=uuid4(),
    )
    subject = Subject(name="operator", device="mac", channel="tailscale")
    queued = store.create_job(request, subject)
    store.claim_next_job("lost-worker", lease_seconds=-1)
    assert store.expire_leases() == 1
    expired = store.get_job(queued.id)
    assert expired is not None
    assert expired.state == JobState.UNKNOWN_OUTCOME
    catalog = Catalog.from_path(root / "apps/broker/tests/fixtures/catalog")
    reconciler = Reconciler(store, catalog, AdapterRegistry({"dh-core.fake": FakeAdapter()}))
    reconciled = await reconciler.run_once()
    assert reconciled[0].state == JobState.SUCCEEDED
    assert reconciled[0].result is not None
    assert reconciled[0].result["verified"] is True
