"""A thin typed client for the Deckhand broker HTTP API.

Every non-broker client surface (the MCP server, sdctl, future clients) submits
typed intent through this client so it goes through the identical broker path:
identity → policy → confirmation → durable job → observed postcondition → audit.
Nothing here executes anything itself; it only forwards typed requests to the
broker and returns typed responses. This is what keeps the MCP surface from
becoming a second, unaudited execution path.
"""

from __future__ import annotations

from typing import Any

import httpx

from .identity import mint_token
from .models import JobView, PlanView


class BrokerClientError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class BrokerClient:
    """Forwards typed intent to the broker, authenticating each request with a
    freshly minted, short-lived signed identity token for the calling subject."""

    def __init__(
        self,
        base_url: str,
        *,
        signing_key: Any,
        channel: str = "mcp",
        token_ttl_seconds: int = 60,
        timeout_seconds: float = 10.0,
        verify: bool | str = True,
        transport: httpx.BaseTransport | httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._signing_key = signing_key
        self._channel = channel
        self._token_ttl = token_ttl_seconds
        self._timeout = timeout_seconds
        self._verify = verify
        # An injected transport (e.g. httpx.ASGITransport) lets tests drive the
        # broker in-process without a network listener.
        self._transport = transport

    def _headers(self, *, subject: str, device: str, nonce: str) -> dict[str, str]:
        token = mint_token(
            self._signing_key,
            subject=subject,
            device=device,
            channel=self._channel,
            nonce=nonce,
            ttl_seconds=self._token_ttl,
        )
        return {"X-Deckhand-Identity": token, "Content-Type": "application/json"}

    async def _request(
        self,
        method: str,
        path: str,
        *,
        subject: str,
        device: str,
        nonce: str,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        headers = self._headers(subject=subject, device=device, nonce=nonce)
        client_kwargs: dict[str, Any] = {"timeout": self._timeout}
        if self._transport is not None:
            client_kwargs["transport"] = self._transport
        else:
            client_kwargs["verify"] = self._verify
        try:
            async with httpx.AsyncClient(**client_kwargs) as client:
                response = await client.request(
                    method, f"{self._base_url}{path}", headers=headers, json=json_body
                )
        except httpx.HTTPError as error:
            raise BrokerClientError(f"broker unreachable: {error}") from error
        if response.status_code >= 400:
            detail = _safe_detail(response)
            raise BrokerClientError(
                f"broker returned {response.status_code}: {detail}",
                status_code=response.status_code,
            )
        return response.json()

    async def list_actions(self, *, subject: str, device: str, nonce: str) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = await self._request(
            "GET", "/v1/actions", subject=subject, device=device, nonce=nonce
        )
        return result

    async def status_summary(self, *, subject: str, device: str, nonce: str) -> dict[str, Any]:
        result: dict[str, Any] = await self._request(
            "GET", "/v1/status/summary", subject=subject, device=device, nonce=nonce
        )
        return result

    async def plan(
        self, action_id: str, request_body: dict[str, Any], *, subject: str, device: str, nonce: str
    ) -> PlanView:
        raw = await self._request(
            "POST",
            f"/v1/actions/{action_id}:plan",
            subject=subject,
            device=device,
            nonce=nonce,
            json_body=request_body,
        )
        return PlanView.model_validate(raw)

    async def execute(
        self, action_id: str, request_body: dict[str, Any], *, subject: str, device: str, nonce: str
    ) -> JobView:
        raw = await self._request(
            "POST",
            f"/v1/actions/{action_id}:execute",
            subject=subject,
            device=device,
            nonce=nonce,
            json_body=request_body,
        )
        return JobView.model_validate(raw)

    async def job(self, job_id: str, *, subject: str, device: str, nonce: str) -> JobView:
        raw = await self._request(
            "GET", f"/v1/jobs/{job_id}", subject=subject, device=device, nonce=nonce
        )
        return JobView.model_validate(raw)


def _safe_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:200]
    if isinstance(body, dict) and "detail" in body:
        return str(body["detail"])[:200]
    return str(body)[:200]
