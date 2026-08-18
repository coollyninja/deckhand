"""Per-caller identity tokens for the broker trust boundary.

The broker never trusts a self-declared ``X-Deckhand-Channel``/``-Subject``/``-Device``
header gated only by a single shared secret. Instead, a trusted local ingress
(Caddy/Tailscale Serve for the deck path, or the MCP server for the agent path)
mints a short-lived Ed25519-signed identity assertion that carries the channel,
subject, device, issued-at, expiry, and a nonce. The broker verifies the
signature against a configured public key and enforces freshness, so:

* a caller cannot assert ``channel=mgmt-mtls`` without a token signed for that
  channel by the trusted issuer (the channel is inside the signed payload, not a
  header the client controls), and
* a leaked assertion is bounded by the token TTL rather than valid forever.

Signature material is a domain-separated, canonical JSON encoding of the claims.
This is asymmetric on purpose: the issuer holds the private key, the broker holds
only the public key, so broker compromise does not yield an identity-minting
capability.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from .models import Subject

_DOMAIN = b"deckhand-identity-v1"
_ALLOWED_CHANNELS = frozenset({"tailscale", "mgmt-mtls", "mcp"})
# Bounds on acceptable clock skew and token lifetime, defence-in-depth against a
# misconfigured or malicious issuer handing out very long-lived assertions.
_MAX_TTL_SECONDS = 300
_MAX_CLOCK_SKEW_SECONDS = 30


class IdentityError(Exception):
    """Raised when an identity token cannot be minted or verified."""


@dataclass(frozen=True)
class IdentityClaims:
    """The verified identity a request carries."""

    subject: str
    device: str
    channel: str
    issued_at: datetime
    expires_at: datetime
    nonce: str

    def to_subject(self) -> Subject:
        return Subject(name=self.subject, device=self.device, channel=self.channel)


def _canonical_claims(
    *,
    subject: str,
    device: str,
    channel: str,
    issued_at: datetime,
    expires_at: datetime,
    nonce: str,
) -> bytes:
    payload = {
        "subject": subject,
        "device": device,
        "channel": channel,
        "issued_at": issued_at.astimezone(UTC).isoformat(),
        "expires_at": expires_at.astimezone(UTC).isoformat(),
        "nonce": nonce,
    }
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _DOMAIN + b"\x1f" + body


def _b64u_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64u_decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def load_public_key(path: Path) -> Ed25519PublicKey:
    """Load a PEM Ed25519 public key used to verify identity tokens."""
    try:
        material = path.read_bytes()
    except OSError as error:
        raise IdentityError(f"identity public key unavailable: {error}") from error
    try:
        key = serialization.load_pem_public_key(material)
    except ValueError as error:
        raise IdentityError("identity public key is not valid PEM") from error
    if not isinstance(key, Ed25519PublicKey):
        raise IdentityError("identity trust key must be an Ed25519 public key")
    return key


def load_private_key(path: Path) -> Ed25519PrivateKey:
    """Load a PEM Ed25519 private key used to mint identity tokens (issuer side)."""
    try:
        material = path.read_bytes()
    except OSError as error:
        raise IdentityError(f"identity private key unavailable: {error}") from error
    try:
        key = serialization.load_pem_private_key(material, password=None)
    except (ValueError, TypeError) as error:
        raise IdentityError("identity private key is not a valid unencrypted PEM") from error
    if not isinstance(key, Ed25519PrivateKey):
        raise IdentityError("identity signing key must be an Ed25519 private key")
    return key


def mint_token(
    private_key: Ed25519PrivateKey,
    *,
    subject: str,
    device: str,
    channel: str,
    nonce: str,
    ttl_seconds: int = 60,
    now: datetime | None = None,
) -> str:
    """Mint a signed identity token. Used by trusted issuers (proxy, MCP server)."""
    if channel not in _ALLOWED_CHANNELS:
        raise IdentityError(f"unknown identity channel: {channel!r}")
    if not subject or not device or not nonce:
        raise IdentityError("subject, device, and nonce are required")
    if ttl_seconds <= 0 or ttl_seconds > _MAX_TTL_SECONDS:
        raise IdentityError(f"ttl_seconds must be in (0, {_MAX_TTL_SECONDS}]")
    issued_at = (now or datetime.now(UTC)).astimezone(UTC)
    expires_at = issued_at + timedelta(seconds=ttl_seconds)
    material = _canonical_claims(
        subject=subject,
        device=device,
        channel=channel,
        issued_at=issued_at,
        expires_at=expires_at,
        nonce=nonce,
    )
    signature = private_key.sign(material)
    envelope = {
        "subject": subject,
        "device": device,
        "channel": channel,
        "issued_at": issued_at.isoformat(),
        "expires_at": expires_at.isoformat(),
        "nonce": nonce,
        "sig": _b64u_encode(signature),
    }
    return _b64u_encode(json.dumps(envelope, separators=(",", ":")).encode("utf-8"))


def verify_token(
    public_key: Ed25519PublicKey,
    token: str,
    *,
    now: datetime | None = None,
) -> IdentityClaims:
    """Verify a signed identity token and return its claims, or raise IdentityError."""
    try:
        envelope = json.loads(_b64u_decode(token))
    except (ValueError, json.JSONDecodeError) as error:
        raise IdentityError("identity token is not decodable") from error
    if not isinstance(envelope, dict):
        raise IdentityError("identity token envelope malformed")
    try:
        subject = envelope["subject"]
        device = envelope["device"]
        channel = envelope["channel"]
        issued_at_raw = envelope["issued_at"]
        expires_at_raw = envelope["expires_at"]
        nonce = envelope["nonce"]
        signature = _b64u_decode(envelope["sig"])
    except (KeyError, TypeError, ValueError) as error:
        raise IdentityError("identity token missing required fields") from error
    if not all(isinstance(v, str) and v for v in (subject, device, channel, nonce)):
        raise IdentityError("identity token fields must be non-empty strings")
    if channel not in _ALLOWED_CHANNELS:
        raise IdentityError("identity token names an unknown channel")

    try:
        issued_at = datetime.fromisoformat(issued_at_raw)
        expires_at = datetime.fromisoformat(expires_at_raw)
    except (TypeError, ValueError) as error:
        raise IdentityError("identity token timestamps malformed") from error
    if issued_at.tzinfo is None or expires_at.tzinfo is None:
        raise IdentityError("identity token timestamps must be timezone-aware")

    material = _canonical_claims(
        subject=subject,
        device=device,
        channel=channel,
        issued_at=issued_at,
        expires_at=expires_at,
        nonce=nonce,
    )
    try:
        public_key.verify(signature, material)
    except InvalidSignature as error:
        raise IdentityError("identity token signature invalid") from error

    current = (now or datetime.now(UTC)).astimezone(UTC)
    skew = timedelta(seconds=_MAX_CLOCK_SKEW_SECONDS)
    if issued_at - skew > current:
        raise IdentityError("identity token not yet valid")
    if expires_at + skew < current:
        raise IdentityError("identity token expired")
    if expires_at - issued_at > timedelta(seconds=_MAX_TTL_SECONDS) + skew:
        raise IdentityError("identity token lifetime exceeds policy")

    return IdentityClaims(
        subject=subject,
        device=device,
        channel=channel,
        issued_at=issued_at,
        expires_at=expires_at,
        nonce=nonce,
    )
