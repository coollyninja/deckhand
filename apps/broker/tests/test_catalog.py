from pathlib import Path
from uuid import uuid4

import pytest
from deckhand.catalog import Catalog, CatalogError
from deckhand.models import ActionRequest, RequestContext, Target


def test_catalog_rejects_wrong_target_type() -> None:
    root = Path(__file__).parents[3]
    catalog = Catalog.from_path(root / "packages/catalog/actions")
    request = ActionRequest(
        action_id="pve.vm.ensure_running",
        action_version=1,
        target=Target(type="lab", id="210"),
        context=RequestContext(client="test"),
        idempotency_key=uuid4(),
    )
    with pytest.raises(CatalogError, match="target type"):
        catalog.validate_request(request)
