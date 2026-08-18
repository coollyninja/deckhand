from collections.abc import Iterator
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from deckhand.api import create_app
from deckhand.config import Settings
from deckhand.policy import DevelopmentPolicyEngine
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    """Client on the legacy shared-assertion path (kept green during migration)."""
    root = Path(__file__).parents[3]
    assertion_file = tmp_path / "proxy-assertion"
    assertion_file.write_text("test-proxy-assertion", encoding="utf-8")
    settings = Settings(
        database_path=tmp_path / "deckhand.db",
        catalog_path=root / "apps/broker/tests/fixtures/catalog",
        trusted_proxy=True,
        proxy_assertion_file=assertion_file,
        allow_legacy_proxy_assertion=True,
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


@pytest.fixture
def identity_key(tmp_path: Path) -> Ed25519PrivateKey:
    """A signing key whose public half the signed-identity client trusts."""
    key = Ed25519PrivateKey.generate()
    public_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    (tmp_path / "identity.pub.pem").write_bytes(public_pem)
    return key


@pytest.fixture
def signed_client(
    tmp_path: Path, identity_key: Ed25519PrivateKey
) -> Iterator[TestClient]:
    """Client on the primary signed-identity-token path (legacy path disabled)."""
    root = Path(__file__).parents[3]
    settings = Settings(
        database_path=tmp_path / "deckhand.db",
        catalog_path=root / "apps/broker/tests/fixtures/catalog",
        trusted_proxy=True,
        identity_public_key_file=tmp_path / "identity.pub.pem",
        allow_legacy_proxy_assertion=False,
        allow_mutations=False,
    )
    with TestClient(create_app(settings, policy=DevelopmentPolicyEngine())) as test_client:
        yield test_client
