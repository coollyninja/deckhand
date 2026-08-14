import pytest
from deckhand.models import StatusValue
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
