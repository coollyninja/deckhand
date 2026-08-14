from pathlib import Path

import pytest
from deckhand.inventory import Inventory, StatusEndpoint, load_inventory


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://user:password@example.invalid",
        "relative/path",
        "https://example.invalid/path?secret=value",
    ],
)
def test_status_endpoint_rejects_unsafe_base_urls(url: str) -> None:
    with pytest.raises(ValueError):
        StatusEndpoint(base_url=url)


def test_missing_inventory_is_empty(tmp_path: Path) -> None:
    assert load_inventory(tmp_path / "missing.yaml") == Inventory()
