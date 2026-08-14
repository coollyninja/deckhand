import asyncio
from datetime import UTC, datetime
from typing import Protocol

import httpx

from .inventory import Inventory, StatusEndpoint
from .models import StatusValue

DEFAULT_DOMAINS = (
    "internet",
    "tailscale",
    "lab",
    "proxmox",
    "kubernetes",
    "home_assistant",
    "unifi",
    "truenas",
    "prometheus",
    "grafana",
    "loki",
    "openfang",
    "ruflo",
    "github",
)


class StatusProvider(Protocol):
    async def observe(self) -> StatusValue: ...


class HttpStatusProvider:
    def __init__(self, endpoint: StatusEndpoint) -> None:
        self.endpoint = endpoint

    async def observe(self) -> StatusValue:
        headers: dict[str, str] = {}
        if self.endpoint.authorization_file is not None:
            token = self.endpoint.authorization_file.read_text(encoding="utf-8").strip()
            headers["Authorization"] = token
        started = datetime.now(UTC)
        try:
            async with httpx.AsyncClient(
                timeout=self.endpoint.timeout_seconds,
                verify=self.endpoint.verify_tls,
                follow_redirects=False,
            ) as client:
                response = await client.get(
                    f"{self.endpoint.base_url}{self.endpoint.health_path}", headers=headers
                )
            elapsed_ms = int((datetime.now(UTC) - started).total_seconds() * 1000)
            healthy = 200 <= response.status_code < 400
            return StatusValue(
                state="healthy" if healthy else "degraded",
                stale_after_seconds=self.endpoint.stale_after_seconds,
                details={"status_code": response.status_code, "latency_ms": elapsed_ms},
            )
        except (httpx.HTTPError, OSError) as error:
            return StatusValue(
                state="unavailable",
                stale_after_seconds=self.endpoint.stale_after_seconds,
                details={"error_class": type(error).__name__},
            )


class StatusAggregator:
    def __init__(self, providers: dict[str, StatusProvider]) -> None:
        self.providers = providers

    @classmethod
    def from_inventory(cls, inventory: Inventory) -> "StatusAggregator":
        return cls(
            {
                name: HttpStatusProvider(endpoint)
                for name, endpoint in inventory.status_endpoints.items()
            }
        )

    async def domain(self, name: str) -> StatusValue:
        provider = self.providers.get(name)
        if provider is None:
            return StatusValue(
                state="unconfigured",
                stale_after_seconds=1,
                details={"configuration_required": True},
            )
        return await provider.observe()

    async def summary(self) -> dict[str, StatusValue]:
        names = sorted(set(DEFAULT_DOMAINS) | set(self.providers))
        values = await asyncio.gather(*(self.domain(name) for name in names))
        return dict(zip(names, values, strict=True))
