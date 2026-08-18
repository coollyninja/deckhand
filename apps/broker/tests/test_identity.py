from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from deckhand.identity import IdentityError, mint_token, verify_token


def _key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


def test_mint_and_verify_round_trip() -> None:
    key = _key()
    token = mint_token(key, subject="bobby", device="macbook-air-m2", channel="mcp", nonce="n1")
    claims = verify_token(key.public_key(), token)
    assert claims.subject == "bobby"
    assert claims.device == "macbook-air-m2"
    assert claims.channel == "mcp"
    assert claims.to_subject().name == "bobby"


def test_verify_rejects_wrong_key() -> None:
    token = mint_token(_key(), subject="bobby", device="d", channel="mcp", nonce="n")
    with pytest.raises(IdentityError):
        verify_token(_key().public_key(), token)


def test_verify_rejects_tampered_channel() -> None:
    key = _key()
    token = mint_token(key, subject="bobby", device="d", channel="mcp", nonce="n")
    # Flip the channel in the envelope; the signature no longer matches.
    import base64
    import json

    raw = json.loads(base64.urlsafe_b64decode(token + "=="))
    raw["channel"] = "mgmt-mtls"
    forged = base64.urlsafe_b64encode(json.dumps(raw).encode()).rstrip(b"=").decode()
    with pytest.raises(IdentityError):
        verify_token(key.public_key(), forged)


def test_verify_rejects_expired_token() -> None:
    key = _key()
    past = datetime.now(UTC) - timedelta(minutes=10)
    token = mint_token(
        key, subject="bobby", device="d", channel="mcp", nonce="n", ttl_seconds=60, now=past
    )
    with pytest.raises(IdentityError):
        verify_token(key.public_key(), token)


def test_verify_rejects_not_yet_valid() -> None:
    key = _key()
    future = datetime.now(UTC) + timedelta(minutes=10)
    token = mint_token(
        key, subject="bobby", device="d", channel="mcp", nonce="n", ttl_seconds=60, now=future
    )
    with pytest.raises(IdentityError):
        verify_token(key.public_key(), token)


def test_mint_rejects_unknown_channel() -> None:
    with pytest.raises(IdentityError):
        mint_token(_key(), subject="a", device="b", channel="not-a-channel", nonce="n")


def test_mint_rejects_excessive_ttl() -> None:
    with pytest.raises(IdentityError):
        mint_token(_key(), subject="a", device="b", channel="mcp", nonce="n", ttl_seconds=99999)


def test_verify_rejects_garbage_token() -> None:
    with pytest.raises(IdentityError):
        verify_token(_key().public_key(), "not-a-real-token")
