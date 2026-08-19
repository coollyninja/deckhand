"""The local issuer must mint tokens the BROKER's verify_token accepts — the
cross-component contract that makes the whole plugin→broker path work."""

from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from deckhand.identity import verify_token  # broker-side verifier
from deckhand_macos.issuer import IssuerSettings, create_app, mint
from fastapi.testclient import TestClient


def _keypair(tmp_path: Path) -> Path:
    key = Ed25519PrivateKey.generate()
    priv = tmp_path / "id.key"
    priv.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    (tmp_path / "id.pub.pem").write_bytes(
        key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    return priv


def _settings(tmp_path: Path, **overrides: object) -> IssuerSettings:
    priv = _keypair(tmp_path)
    base = {
        "signing_key_file": priv,
        "subject": "bobby",
        "device": "macbook-air-m2",
        "channel": "mgmt-mtls",
    }
    base.update(overrides)
    return IssuerSettings(**base)  # type: ignore[arg-type]


def test_issued_token_verifies_with_broker_public_key(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    public_key = serialization.load_pem_public_key((tmp_path / "id.pub.pem").read_bytes())
    key = serialization.load_pem_private_key(settings.signing_key_file.read_bytes(), password=None)
    minted = mint(key, settings)  # type: ignore[arg-type]
    claims = verify_token(public_key, minted["token"])  # type: ignore[arg-type]
    assert claims.subject == "bobby"
    assert claims.device == "macbook-air-m2"
    assert claims.channel == "mgmt-mtls"


def test_issuer_endpoint_serves_a_token(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = TestClient(create_app(settings))
    response = client.get("/token")
    assert response.status_code == 200
    assert response.json()["token"]


def test_issuer_requires_caller_token_when_configured(tmp_path: Path) -> None:
    caller = tmp_path / "caller"
    caller.write_text("local-secret", encoding="utf-8")
    settings = _settings(tmp_path, caller_token_file=caller)
    client = TestClient(create_app(settings))
    assert client.get("/token").status_code == 401
    ok = client.get("/token", headers={"Authorization": "Bearer local-secret"})
    assert ok.status_code == 200
