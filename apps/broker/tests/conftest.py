from collections.abc import Iterator
from pathlib import Path

import pytest
from deckhand.api import create_app
from deckhand.config import Settings
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    root = Path(__file__).parents[3]
    settings = Settings(
        database_path=tmp_path / "deckhand.db",
        catalog_path=root / "packages/catalog/actions",
        trusted_proxy=True,
        allow_mutations=False,
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def headers() -> dict[str, str]:
    return {
        "X-Deckhand-Subject": "bobby",
        "X-Deckhand-Device": "macbook-air-m2",
        "X-Deckhand-Channel": "tailscale",
    }
