from dataclasses import dataclass
from typing import Any, Protocol

from .models import ActionDefinition, ActionRequest


class AdapterError(RuntimeError):
    pass


class UnknownOutcome(AdapterError):
    pass


@dataclass(frozen=True)
class AdapterPlan:
    steps: list[str]


class Adapter(Protocol):
    async def plan(self, action: ActionDefinition, request: ActionRequest) -> AdapterPlan: ...

    async def execute(self, action: ActionDefinition, request: ActionRequest) -> dict[str, Any]: ...

    async def verify(
        self, action: ActionDefinition, request: ActionRequest, result: dict[str, Any]
    ) -> dict[str, Any]: ...


class FakeAdapter:
    async def plan(self, action: ActionDefinition, request: ActionRequest) -> AdapterPlan:
        return AdapterPlan(steps=[f"observe {request.target.type}:{request.target.id}"])

    async def execute(self, action: ActionDefinition, request: ActionRequest) -> dict[str, Any]:
        return {"state": "healthy", "target": request.target.model_dump(), "source": "fake"}

    async def verify(
        self, action: ActionDefinition, request: ActionRequest, result: dict[str, Any]
    ) -> dict[str, Any]:
        return {**result, "verified": True}


class DisabledMutationAdapter:
    def __init__(self, name: str) -> None:
        self.name = name

    async def plan(self, action: ActionDefinition, request: ActionRequest) -> AdapterPlan:
        return AdapterPlan(
            steps=[f"validate {self.name} target", f"execute {action.id}", "verify postcondition"]
        )

    async def execute(self, action: ActionDefinition, request: ActionRequest) -> dict[str, Any]:
        raise AdapterError(f"{self.name} adapter requires approved production inventory")

    async def verify(
        self, action: ActionDefinition, request: ActionRequest, result: dict[str, Any]
    ) -> dict[str, Any]:
        raise AdapterError(f"{self.name} adapter cannot verify an unconfigured target")


class AdapterRegistry:
    def __init__(self, adapters: dict[str, Adapter] | None = None) -> None:
        self._adapters: dict[str, Adapter] = adapters or {}

    def register(self, name: str, adapter: Adapter) -> None:
        if name in self._adapters:
            raise ValueError(f"adapter {name!r} is already registered")
        self._adapters[name] = adapter

    def get(self, name: str) -> Adapter:
        try:
            return self._adapters[name]
        except KeyError as error:
            raise AdapterError(f"adapter {name!r} is not registered") from error
