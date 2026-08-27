"""Local identity issuer.

A tiny loopback service that mints short-lived Ed25519-signed Deckhand identity
tokens for the Stream Deck plugin (and any local client). It holds the signing
private key; the broker holds only the matching public key and verifies each
token. This keeps the plugin free of any signing key and gives the broker a real
per-request identity instead of a shared secret.

Binds to 127.0.0.1 only. The token's subject/device/channel come from
configuration, not the caller — a local client cannot ask for an identity other
than the one this issuer is configured to mint.

The token wire format matches deckhand.identity.verify_token exactly:
a base64url JSON envelope over an Ed25519 signature of the domain-separated,
canonical claims. Implemented here without importing the broker package so the
agent stays independently installable.
"""

from __future__ import annotations

import base64
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI, Header, HTTPException, status
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DOMAIN = b"deckhand-identity-v1"


class IssuerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DECKHAND_ISSUER_", extra="forbid")

    bind_host: str = "127.0.0.1"
    bind_port: int = Field(default=19472, ge=1024, le=65535)
    signing_key_file: Path
    subject: str
    device: str
    channel: str = "mgmt-mtls"
    token_ttl_seconds: int = Field(default=60, ge=10, le=300)
    # Optional local bearer that a caller must present, so only the plugin (which
    # reads the same token file) can request identities. Loopback-only already, but
    # this adds defence in depth against other local processes.
    caller_token_file: Path | None = None

    @field_validator("signing_key_file", "caller_token_file")
    @classmethod
    def expand_paths(cls, value: Path | None) -> Path | None:
        # Same reason as MacSettings: ~ must be expanded or every read fails.
        return value.expanduser() if value is not None else None


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def mint(key: Ed25519PrivateKey, settings: IssuerSettings) -> dict[str, str]:
    issued = datetime.now(UTC)
    expires = issued + timedelta(seconds=settings.token_ttl_seconds)
    claims = {
        "subject": settings.subject,
        "device": settings.device,
        "channel": settings.channel,
        "issued_at": issued.isoformat(),
        "expires_at": expires.isoformat(),
        "nonce": uuid.uuid4().hex,
    }
    body = json.dumps(claims, sort_keys=True, separators=(",", ":")).encode()
    signature = key.sign(_DOMAIN + b"\x1f" + body)
    envelope = {**claims, "sig": _b64u(signature)}
    token = _b64u(json.dumps(envelope, separators=(",", ":")).encode())
    return {"token": token, "expires_at": expires.isoformat()}


def _load_key(path: Path) -> Ed25519PrivateKey:
    material = path.read_bytes()
    key = serialization.load_pem_private_key(material, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("issuer signing key must be an Ed25519 private key")
    return key


def create_app(settings: IssuerSettings | None = None) -> FastAPI:
    configured = settings or IssuerSettings()  # type: ignore[call-arg]
    key = _load_key(configured.signing_key_file)
    app = FastAPI(title="Deckhand Identity Issuer", version="0.1.0")

    def authorize(authorization: str | None) -> None:
        if configured.caller_token_file is None:
            return
        import hmac

        try:
            expected = configured.caller_token_file.read_text(encoding="utf-8").strip()
        except OSError as error:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, "issuer credential unavailable"
            ) from error
        supplied = authorization.removeprefix("Bearer ") if authorization else ""
        if not supplied or not hmac.compare_digest(supplied, expected):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "local authorization required")

    @app.get("/healthz")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/token")
    async def token(
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, str]:
        authorize(authorization)
        return mint(key, configured)

    return app


def run() -> None:  # pragma: no cover - process entry point
    import uvicorn

    settings = IssuerSettings()  # type: ignore[call-arg]
    uvicorn.run(create_app(settings), host=settings.bind_host, port=settings.bind_port)
