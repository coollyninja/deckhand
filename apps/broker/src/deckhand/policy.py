from typing import Any, Protocol

import httpx

from .models import ConfirmationMode, PolicyDecision


class PolicyUnavailable(RuntimeError):
    pass


class PolicyEngine(Protocol):
    async def decide(self, policy_input: dict[str, Any]) -> PolicyDecision: ...

    async def ready(self) -> bool: ...


class OpaPolicyEngine:
    def __init__(self, base_url: str, decision_path: str, timeout_seconds: float = 2.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.decision_path = decision_path
        self.timeout_seconds = timeout_seconds

    async def decide(self, policy_input: dict[str, Any]) -> PolicyDecision:
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(
                    f"{self.base_url}{self.decision_path}",
                    json={"input": policy_input},
                )
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise PolicyUnavailable("policy decision unavailable") from error
        result = payload.get("result")
        if not isinstance(result, dict):
            raise PolicyUnavailable("policy returned no structured decision")
        result["decision_id"] = payload.get("decision_id")
        return PolicyDecision.model_validate(result)

    async def ready(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.get(f"{self.base_url}/health?bundles=true")
            return response.status_code == 200
        except httpx.HTTPError:
            return False


class DevelopmentPolicyEngine:
    """Explicit test/development policy; never selected from environment settings."""

    async def decide(self, policy_input: dict[str, Any]) -> PolicyDecision:
        action = policy_input["action"]
        if action["mutation"] and not policy_input["runtime"]["mutations_enabled"]:
            return PolicyDecision(allow=False, reason="mutations are disabled")
        required = (
            ConfirmationMode(action["confirmation"])
            if action["mutation"]
            else ConfirmationMode.NONE
        )
        phase = policy_input["request"]["phase"]
        confirmed = policy_input["confirmation"]["valid"]
        if phase == "execute" and required != ConfirmationMode.NONE and not confirmed:
            return PolicyDecision(
                allow=False,
                reason="valid confirmation required",
                required_confirmation=required,
            )
        return PolicyDecision(allow=True, required_confirmation=required)

    async def ready(self) -> bool:
        return True
