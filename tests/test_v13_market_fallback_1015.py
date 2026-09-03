from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.market.mubasher import MubasherMarketTotals, MubasherMarketTotalsClient
from app.service import TradingService


def test_mubasher_parser_extracts_user_snapshot_tasi_change_volume_value():
    html = """
    <html><body>
    <h1>مؤشر السوق الرئيسية (تاسي) (TASI)</h1>
    <div>آخر تحديث: الأحد, أغسطس 30</div>
    <div>11,158.54</div>
    <div>-79.39</div>
    <div>-0.71%</div>
    <div>فتح 11,241.66</div>
    <div>إغلاق سابق 11,237.93</div>
    <div>أعلى 11,256.68</div>
    <div>أدنى 11,149.17</div>
    <div>إجماليات المؤشر</div>
    <div>حجم التداول 192,331,188</div>
    <div>قيمة التداول 3,512,717,290.23</div>
    </body></html>
    """
    result = MubasherMarketTotalsClient.parse_from_html(html)
    assert result.ok is True
    assert result.index_value == 11158.54
    assert result.change_percent == -0.71
    assert result.volume == 192_331_188
    assert result.trading_value == 3_512_717_290.23


@pytest.mark.asyncio
async def test_invalid_zero_primary_market_summary_uses_mubasher_core_fallback():
    service = object.__new__(TradingService)
    service.s = SimpleNamespace(
        market_totals_use_mubasher=True,
        market_totals_cache_seconds=600,
    )
    service.p = SimpleNamespace(active_provider=lambda: "sahmk")
    service.last_market_totals = None
    service.last_market_totals_at = None
    service._utc_now = lambda: datetime(2026, 8, 31, 7, 20, tzinfo=timezone.utc)

    class Client:
        async def fetch(self):
            return MubasherMarketTotals(
                index_value=11158.54,
                change_percent=-0.71,
                volume=192_331_188,
                trading_value=3_512_717_290.23,
                ok=True,
                reason="ok",
            )

    service.market_totals_client = Client()
    data = {
        "index_value": 0,
        "change_percent": 0,
        "advancers": 0,
        "decliners": 0,
        "total_volume": 0,
        "trading_value": 0,
    }
    out = await service._augment_market_totals(data, force=True)
    assert out["index_value"] == 11158.54
    assert out["change_percent"] == -0.71
    assert out["total_volume"] == 192_331_188
    assert out["trading_value"] == 3_512_717_290.23
    assert out["advancers"] is None
    assert out["decliners"] is None
    assert out["market_core_source"] == "MUBASHER"
    assert out["market_totals_source"] == "MUBASHER"


@pytest.mark.asyncio
async def test_primary_market_exception_still_allows_mubasher_market_snapshot():
    service = object.__new__(TradingService)
    service.s = SimpleNamespace(
        market_cache_seconds=600,
        market_totals_use_mubasher=True,
        market_totals_cache_seconds=600,
    )
    service.last_market_summary = None
    service.last_market_summary_at = None
    service.last_market_totals = None
    service.last_market_totals_at = None
    service._utc_now = lambda: datetime(2026, 8, 31, 7, 20, tzinfo=timezone.utc)

    class Provider:
        async def market_summary(self):
            raise RuntimeError("SAHMK summary unavailable")
        def active_provider(self):
            return "sahmk"

    class Client:
        async def fetch(self):
            return MubasherMarketTotals(
                index_value=11158.54,
                change_percent=-0.71,
                volume=192_331_188,
                trading_value=3_512_717_290.23,
                ok=True,
                reason="ok",
            )

    service.p = Provider()
    service.market_totals_client = Client()
    out = await service._market(force=True)
    assert out is not None
    assert out["index_value"] == 11158.54
    assert out["change_percent"] == -0.71
    assert out["market_core_source"] == "MUBASHER"


def test_v13_market_data_warmup_starts_1015_signal_window_stays_1030():
    source = open("app/config/settings.py", "r", encoding="utf-8").read()
    assert 'market_data_start: str = "10:15"' in source
    assert 'signal_window_start: str = "10:30"' in source
