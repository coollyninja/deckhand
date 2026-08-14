import pytest
from deckhand.adapters import (
    AdapterError,
    AdapterErrorKind,
    AdapterHealth,
    AdapterHealthState,
    AdapterRegistry,
    FakeAdapter,
)
from deckhand.models import RetryDisposition


class UnavailableAdapter(FakeAdapter):
    async def health(self) -> AdapterHealth:
        raise AdapterError(
            "upstream unavailable",
            kind=AdapterErrorKind.UNAVAILABLE,
            retry=RetryDisposition.SAFE,
        )


@pytest.mark.asyncio
async def test_registry_normalizes_adapter_health_errors() -> None:
    health = await AdapterRegistry({"dh-test.adapter": UnavailableAdapter()}).health()
    assert health["dh-test.adapter"].state == AdapterHealthState.UNAVAILABLE
    assert health["dh-test.adapter"].details == {
        "error_code": "unavailable",
        "retry": "safe",
    }
