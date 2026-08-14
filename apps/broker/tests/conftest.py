from collections.abc import Iterator
from pathlib import Path

import pytest
from deckhand.api import create_app
from deckhand.config import Settings
from deckhand.policy import DevelopmentPolicyEngine
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    root = Path(__file__).parents[3]
    assertion_file = tmp_path / "proxy-assertion"
    assertion_file.write_text("test-proxy-assertion", encoding="utf-8")
    settings = Settings(
        database_path=tmp_path / "deckhand.db",
        catalog_path=root / "packages/catalog/actions",
        trusted_proxy=True,
        proxy_assertion_file=assertion_file,
        allow_mutations=False,
    )
    with TestClient(create_app(settings, policy=DevelopmentPolicyEngine())) as test_client:
        yield test_client


@pytest.fixture
def headers() -> dict[str, str]:
    return {
        "X-Deckhand-Subject": "bobby",
        "X-Deckhand-Device": "macbook-air-m2",
        "X-Deckhand-Channel": "mgmt-mtls",
        "X-Deckhand-Proxy-Assertion": "test-proxy-assertion",
    }
