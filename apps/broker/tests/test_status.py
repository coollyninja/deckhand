import pytest
from deckhand.status import StatusAggregator


@pytest.mark.asyncio
async def test_unconfigured_domain_is_explicit() -> None:
    aggregator = StatusAggregator({})
    value = await aggregator.domain("proxmox")
    assert value.state == "unconfigured"
    assert value.details["configuration_required"] is True


@pytest.mark.asyncio
async def test_summary_contains_required_domains() -> None:
    aggregator = StatusAggregator({})
    summary = await aggregator.summary()
    assert "proxmox" in summary
    assert "kubernetes" in summary
    assert "tailscale" in summary
