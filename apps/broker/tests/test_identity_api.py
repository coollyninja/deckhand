"""Signed-identity-token path at the API boundary (the primary auth model)."""

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from deckhand.identity import mint_token
from fastapi.testclient import TestClient


def _token(key: Ed25519PrivateKey, *, channel: str = "mgmt-mtls", **overrides: str) -> str:
    fields = {"subject": "bobby", "device": "macbook-air-m2", "channel": channel, "nonce": "n1"}
    fields.update(overrides)
    return mint_token(key, **fields)  # type: ignore[arg-type]


def test_signed_token_authenticates(
    signed_client: TestClient, identity_key: Ed25519PrivateKey
) -> None:
    response = signed_client.get(
        "/v1/actions", headers={"X-Deckhand-Identity": _token(identity_key)}
    )
    assert response.status_code == 200


def test_missing_identity_is_rejected(signed_client: TestClient) -> None:
    assert signed_client.get("/v1/actions").status_code == 401


def test_forged_token_is_rejected(signed_client: TestClient) -> None:
    # A token signed by a DIFFERENT key must not authenticate.
    attacker = Ed25519PrivateKey.generate()
    response = signed_client.get("/v1/actions", headers={"X-Deckhand-Identity": _token(attacker)})
    assert response.status_code == 401


def test_legacy_headers_ignored_when_signed_tokens_required(signed_client: TestClient) -> None:
    # With the legacy path disabled, self-declared identity headers cannot
    # authenticate even with a plausible-looking assertion — the whole point of
    # the fix: no shared secret mints identity.
    response = signed_client.get(
        "/v1/actions",
        headers={
            "X-Deckhand-Subject": "bobby",
            "X-Deckhand-Device": "macbook-air-m2",
            "X-Deckhand-Channel": "mgmt-mtls",
            "X-Deckhand-Proxy-Assertion": "guessed-or-leaked",
        },
    )
    assert response.status_code == 401


def test_channel_is_bound_into_the_signed_token(
    signed_client: TestClient, identity_key: Ed25519PrivateKey
) -> None:
    # The channel travels inside the signed payload; a client cannot override it
    # with a header. A token minted for channel=mcp authenticates AS mcp
    # regardless of any X-Deckhand-Channel header the client also sends.
    token = _token(identity_key, channel="mcp")
    response = signed_client.get(
        "/v1/actions",
        headers={"X-Deckhand-Identity": token, "X-Deckhand-Channel": "mgmt-mtls"},
    )
    assert response.status_code == 200
