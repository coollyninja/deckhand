from pathlib import Path
from uuid import uuid4

import pytest
from deckhand.catalog import Catalog, CatalogError
from deckhand.models import ActionRequest, RequestContext, Target


def test_catalog_rejects_wrong_target_type() -> None:
    root = Path(__file__).parents[3]
    catalog = Catalog.from_path(root / "apps/broker/tests/fixtures/catalog")
    request = ActionRequest(
        action_id="test.resource.ensure_active",
        action_version=1,
        target=Target(type="other", id="example"),
        context=RequestContext(client="test"),
        idempotency_key=uuid4(),
    )
    with pytest.raises(CatalogError, match="target type"):
        catalog.validate_request(request)
