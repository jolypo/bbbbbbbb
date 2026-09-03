from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.market.mubasher import MubasherMarketTotals, MubasherMarketTotalsClient
from app.service import TradingService


def test_mubasher_market_totals_parser_extracts_arabic_volume_and_value():
    html = """
    <html><body>
      <div>حجم التداول</div><div>192,331,188</div>
      <div>قيمة التداول</div><div>3,512,717,290.23</div>
    </body></html>
    """
    result = MubasherMarketTotalsClient.parse_from_html(html)
    assert result.ok is True
    assert result.volume == 192_331_188
    assert result.trading_value == 3_512_717_290.23


@pytest.mark.asyncio
async def test_market_augment_uses_mubasher_for_volume_and_value(monkeypatch):
    service = object.__new__(TradingService)
    service.s = SimpleNamespace(
        market_totals_use_mubasher=True,
        market_totals_cache_seconds=600,
    )
    service.p = SimpleNamespace(active_provider=lambda: "sahmk")
    service.last_market_totals = None
    service.last_market_totals_at = None
    service._utc_now = lambda: datetime(2026, 8, 30, 9, 0, tzinfo=timezone.utc)

    class Client:
        async def fetch(self):
            return MubasherMarketTotals(
                volume=192_331_188,
                trading_value=3_512_717_290.23,
                ok=True,
                reason="ok",
            )

    service.market_totals_client = Client()
    data = {
        "value": 11158.54,
        "change_percent": -0.71,
        "advancers": 89,
        "decliners": 172,
        "total_volume": 191_862_211,
        "trading_value": None,
    }
    out = await service._augment_market_totals(data, force=True)
    assert out["total_volume"] == 192_331_188
    assert out["trading_value"] == 3_512_717_290.23
    assert out["market_totals_source"] == "MUBASHER"
    assert out["market_totals_status"] == "ok"


@pytest.mark.asyncio
async def test_market_augment_preserves_provider_values_when_mubasher_fails():
    service = object.__new__(TradingService)
    service.s = SimpleNamespace(
        market_totals_use_mubasher=True,
        market_totals_cache_seconds=600,
    )
    service.p = SimpleNamespace(active_provider=lambda: "sahmk")
    service.last_market_totals = None
    service.last_market_totals_at = None
    service._utc_now = lambda: datetime(2026, 8, 30, 9, 0, tzinfo=timezone.utc)

    class Client:
        async def fetch(self):
            return MubasherMarketTotals(
                volume=None,
                trading_value=None,
                ok=False,
                reason="502 Bad Gateway",
            )

    service.market_totals_client = Client()
    data = {
        "value": 11158.54,
        "change_percent": -0.71,
        "advancers": 89,
        "decliners": 172,
        "total_volume": 191_862_211,
        "trading_value": None,
    }
    out = await service._augment_market_totals(data, force=True)
    assert out["total_volume"] == 191_862_211
    assert out["trading_value"] is None
    assert out["market_totals_status"] == "fallback"
    assert "502" in out["market_totals_reason"]


@pytest.mark.asyncio
async def test_market_text_mentions_mubasher_as_volume_value_source():
    service = object.__new__(TradingService)
    service.p = SimpleNamespace(active_provider=lambda: "sahmk")

    async def fake_market():
        return {
            "index_value": 11158.54,
            "change_percent": -0.71,
            "advancers": 89,
            "decliners": 172,
            "total_volume": 192_331_188,
            "trading_value": 3_512_717_290.23,
            "market_totals_source": "MUBASHER",
        }

    service._market = fake_market
    text = await TradingService.market_text(service)
    assert "192,331,188 سهم" in text
    assert "3,512,717,290 ر.س" in text
    assert "📊 مصدر الحجم/القيمة: MUBASHER" in text
