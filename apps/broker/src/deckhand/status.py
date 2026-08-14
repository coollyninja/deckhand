from typing import Protocol

from .models import StatusValue


class StatusProvider(Protocol):
    async def observe(self) -> StatusValue: ...


class StatusAggregator:
    def __init__(self, providers: dict[str, StatusProvider]) -> None:
        self.providers = providers

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
        return {name: await self.domain(name) for name in sorted(self.providers)}
