import asyncio
from datetime import timedelta

from app.data.providers.base import Quote
from app.service import TradingService
from tests.test_free_scan import FakeBots, FakeHistorical, FakeProvider, make_settings


class RecoveringProvider(FakeProvider):
    def __init__(self, now, *, monitor_has_data=True):
        super().__init__(now)
        self.monitor_has_data = monitor_has_data
        self.monitor_requested = []

    async def quotes(self, symbols):
        self.detail_requested = list(symbols)
        stale = self.now - timedelta(hours=2)
        return {
            symbol: Quote(
                symbol=symbol,
                name=f"شركة {symbol}",
                name_en=f"Company {symbol}",
                price=100.0,
                change_percent=1.5,
                volume=2_000_000,
                value=100_000_000,
                updated_at=stale,
                is_delayed=True,
            )
            for symbol in symbols
        }

    async def monitor_quotes(self, symbols):
        self.monitor_requested = list(symbols)
        if not self.monitor_has_data:
            return {}
        return {
            symbol: Quote(
                symbol=symbol,
                name=f"شركة {symbol}",
                name_en=f"Company {symbol}",
                price=100.0,
                change_percent=1.5,
                volume=2_000_000,
                value=100_000_000,
                bid=99.9,
                ask=100.1,
                updated_at=self.now,
                is_delayed=True,
            )
            for symbol in symbols
        }


def _service(tmp_path, provider, now):
    settings = make_settings(tmp_path)
    settings.detail_quotes_per_signal = 5
    bots = FakeBots()
    service = TradingService(settings, provider, bots, historical_provider=FakeHistorical(now))
    service._utc_now = lambda: now
    # Keep scan inside Saudi session regardless of UTC date fixture.
    local = now.astimezone(service.tz).replace(hour=11, minute=30)
    service._local_now = lambda: local
    service._utc_now = lambda: local.astimezone(now.tzinfo)
    provider.now = service._utc_now()
    service.h.now = service._utc_now()
    return service


def test_v18_recovers_stale_detail_quotes_from_monitor_path(tmp_path):
    async def run():
        from datetime import datetime, timezone
        now = datetime(2026, 8, 31, 8, 30, tzinfo=timezone.utc)
        provider = RecoveringProvider(now, monitor_has_data=True)
        service = _service(tmp_path, provider, now)
        result = await service.scan_once(trade_horizon="multi_session")
        assert provider.detail_requested
        assert provider.monitor_requested == provider.detail_requested
        assert "تفاصيل السعر مفقودة/قديمة" not in result
    asyncio.run(run())


def test_v18_uses_fresh_stage1_quote_when_detail_and_monitor_fail(tmp_path):
    async def run():
        from datetime import datetime, timezone
        now = datetime(2026, 8, 31, 8, 30, tzinfo=timezone.utc)
        provider = RecoveringProvider(now, monitor_has_data=False)
        service = _service(tmp_path, provider, now)
        result = await service.scan_once(trade_horizon="multi_session")
        assert provider.detail_requested
        assert provider.monitor_requested == provider.detail_requested
        assert "تفاصيل السعر مفقودة/قديمة" not in result
    asyncio.run(run())


def test_v18_coverage_does_not_round_incomplete_scan_to_100():
    service = object.__new__(TradingService)
    diag = {"quality": "FULL", "cached": False, "sources": {}}
    text = service._stage1_diagnostics_text(
        diag, requested=272, selected=271, selection_source="full_market_provider+yahoo"
    )
    assert "DEGRADED COVERAGE" in text
    assert "Coverage: 271/272 (99.6%)" in text
    assert "100%" not in text


def test_v18_does_not_claim_no_trade_when_every_finalist_is_data_skipped(tmp_path):
    async def run():
        from datetime import datetime, timezone
        now = datetime(2026, 8, 31, 8, 30, tzinfo=timezone.utc)
        provider = FakeProvider(now)
        settings = make_settings(tmp_path)
        service = TradingService(settings, provider, FakeBots(), historical_provider=None)
        local = now.astimezone(service.tz).replace(hour=11, minute=30)
        service._local_now = lambda: local
        service._utc_now = lambda: local.astimezone(timezone.utc)
        provider.now = service._utc_now()
        result = await service.scan_once(trade_horizon="multi_session")
        assert "لا يمكن تأكيد وجود أو عدم وجود صفقة حاليًا" in result
        assert "لم توجد صفقة مستوفية لجميع شروط Paper Trading" not in result
    asyncio.run(run())
