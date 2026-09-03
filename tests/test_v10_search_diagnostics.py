import asyncio
from types import SimpleNamespace

from app.data.provider_router import ProviderRouter
from app.data.providers.base import Quote
from app.service import TradingService


class SahmkOK:
    def stats(self):
        return {"daily_requests": 1, "daily_exhausted": False}

    async def top_volume_quotes(self, limit=50, market="TASI"):
        return [Quote(symbol=f"1{i:03d}", name="", name_en="", price=10, volume=10000-i, value=1_000_000) for i in range(limit)]

    async def top_value_quotes(self, limit=50, market="TASI"):
        return [Quote(symbol=f"2{i:03d}", name="", name_en="", price=10, volume=1000, value=50_000_000-i) for i in range(limit)]

    async def top_gainers_quotes(self, limit=50, market="TASI"):
        return [Quote(symbol=f"3{i:03d}", name="", name_en="", price=10, change_percent=5, volume=500, value=5_000_000) for i in range(limit)]


class SahmkNoGainers(SahmkOK):
    async def top_gainers_quotes(self, limit=50, market="TASI"):
        raise RuntimeError("gainers endpoint unavailable")


class TasilabStub:
    def set_universe(self, symbols):
        self.symbols = list(symbols)

    async def top_volume_quotes(self, limit=50, market="TASI"):
        return []

    async def quotes(self, symbols):
        return {s: Quote(symbol=s, name="", name_en="", price=10) for s in symbols}



def cfg(tmp_path):
    return SimpleNamespace(
        timezone="Asia/Riyadh",
        state_dir=str(tmp_path),
        sahmk_daily_switch_limit=95,
        provider_switch_on_daily_limit=True,
        provider_fallback_enabled=True,
        stage1_watchlist_limit=0,
        stage1_candidate_cache_seconds=180,
        stage1_use_top_value=True,
        stage1_use_gainers=True,
        stage1_watch_share=0.10,
        stage1_gainers_share=0.25,
        stage1_value_share=0.30,
    )


def test_candidate_pool_exposes_full_source_diagnostics(tmp_path):
    async def run():
        router = ProviderRouter(cfg(tmp_path), SahmkOK(), TasilabStub())
        rows = await router.active_candidate_quotes(10, "TASI")
        diag = router.candidate_pool_diagnostics()
        assert len(rows) == 10
        assert diag["quality"] == "FULL"
        assert diag["selected"] == 10
        assert diag["sources"]["volume"]["status"] == "ok"
        assert diag["sources"]["value"]["status"] == "ok"
        assert diag["sources"]["gainers"]["status"] == "ok"
        cached_rows = await router.active_candidate_quotes(10, "TASI")
        cached_diag = router.candidate_pool_diagnostics()
        assert [q.symbol for q in cached_rows] == [q.symbol for q in rows]
        assert cached_diag["cached"] is True
        assert cached_diag["quality"] == "FULL"
    asyncio.run(run())


def test_candidate_pool_marks_degraded_when_gainers_fail(tmp_path):
    async def run():
        router = ProviderRouter(cfg(tmp_path), SahmkNoGainers(), TasilabStub())
        rows = await router.active_candidate_quotes(10, "TASI")
        diag = router.candidate_pool_diagnostics()
        assert len(rows) == 10  # other channels can still fill the requested count
        assert diag["quality"] == "DEGRADED"
        assert diag["sources"]["gainers"]["status"] == "unavailable"
        assert "gainers endpoint unavailable" in diag["sources"]["gainers"]["error"]
    asyncio.run(run())


def test_telegram_search_diagnostics_explain_coverage_and_discovery_sources():
    service = object.__new__(TradingService)
    diag = {
        "quality": "DEGRADED",
        "cached": False,
        "sources": {
            "volume": {"enabled": True, "status": "ok", "count": 50, "provider": "SAHMK"},
            "value": {"enabled": True, "status": "ok", "count": 50, "provider": "SAHMK"},
            "gainers": {"enabled": True, "status": "unavailable", "count": 0, "provider": "SAHMK"},
            "watch": {"enabled": True, "status": "ok", "count": 4, "provider": "router"},
        },
    }
    text = service._stage1_diagnostics_text(diag, requested=100, selected=73, selection_source="volume+value+gainers+watch")
    assert "DEGRADED COVERAGE" in text
    assert "Coverage: 73/100 (73.0%)" in text
    assert "Top Gainers" in text and "غير متاح" in text
    discovery = service._candidate_discovery_text(["top_value", "top_gainers", "acceleration", "catalyst_watch"])
    assert discovery == "Top Traded Value + Top Gainers + Acceleration + Catalyst"
