import asyncio

import pytest
from deckhand.adapters import AdapterError, AdapterErrorKind
from deckhand.models import RetryDisposition, StatusValue
from deckhand.plugin_api import StaticStatusProvider
from deckhand.status import StatusAggregator


@pytest.mark.asyncio
async def test_unconfigured_domain_is_explicit() -> None:
    aggregator = StatusAggregator({})
    value = await aggregator.domain("not-configured")
    assert value.state == "unconfigured"
    assert value.details["configuration_required"] is True


@pytest.mark.asyncio
async def test_summary_contains_only_plugin_provided_domains() -> None:
    aggregator = StatusAggregator({"example": StaticStatusProvider(StatusValue(state="healthy"))})
    summary = await aggregator.summary()
    assert list(summary) == ["example"]
    assert summary["example"].state == "healthy"


class FailingProvider:
    async def observe(self) -> StatusValue:
        raise AdapterError(
            "upstream unavailable",
            kind=AdapterErrorKind.UNAVAILABLE,
            retry=RetryDisposition.SAFE,
        )


@pytest.mark.asyncio
async def test_provider_error_is_normalized_without_leaking_message() -> None:
    aggregator = StatusAggregator({"example": FailingProvider()})
    value = await aggregator.domain("example")
    assert value.state == "unavailable"
    assert value.details == {"error_code": "unavailable", "retry": "safe"}


class DelayedProvider:
    async def observe(self) -> StatusValue:
        await asyncio.sleep(0.02)
        return StatusValue(state="healthy")


@pytest.mark.asyncio
async def test_summary_observes_independent_domains_concurrently() -> None:
    aggregator = StatusAggregator(
        {"one": DelayedProvider(), "two": DelayedProvider(), "three": DelayedProvider()}
    )
    started = asyncio.get_running_loop().time()
    summary = await aggregator.summary()
    assert asyncio.get_running_loop().time() - started < 0.05
    assert set(summary) == {"one", "two", "three"}
