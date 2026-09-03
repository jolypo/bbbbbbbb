import asyncio
import httpx
from types import SimpleNamespace

from app.data.provider_router import ProviderRouter
from app.data.providers.base import Quote
from app.data.providers.sahmk import SahmkRateLimitError


class FakeSahmk:
    def __init__(self, used=0, remaining=None):
        self.used = used
        self.remaining = remaining
        self.daily_exhausted = False
        self.mode = "ok"

    def stats(self):
        return {
            "daily_requests": self.used,
            "daily_limit": 100,
            "remaining": self.remaining,
            "rate_limits": 0,
            "errors": 0,
            "daily_exhausted": self.daily_exhausted,
            "cooldown_remaining": 0,
        }

    async def companies(self, market="TASI"):
        if self.mode == "temp429":
            raise SahmkRateLimitError("temporary", retry_after=600, daily_exhausted=False)
        if self.mode == "daily429":
            raise SahmkRateLimitError("daily", retry_after=0, daily_exhausted=True)
        return [
            {"symbol": "1120", "security_type": "equity"},
            {"symbol": "2222", "security_type": "equity"},
        ]

    async def quote(self, symbol):
        if self.mode == "temp429":
            raise SahmkRateLimitError("temporary", retry_after=600, daily_exhausted=False)
        if self.mode == "daily429":
            raise SahmkRateLimitError("daily", retry_after=0, daily_exhausted=True)
        if self.mode == "403":
            request = httpx.Request("GET", "https://example.test/quote")
            response = httpx.Response(403, request=request)
            raise httpx.HTTPStatusError("forbidden", request=request, response=response)
        return Quote(symbol=symbol, name="", name_en="", price=10)

    async def market_summary(self):
        return {"change_percent": 0}

    async def quotes(self, symbols):
        if self.mode == "temp429":
            raise SahmkRateLimitError("temporary", retry_after=600, daily_exhausted=False)
        if self.mode == "daily429":
            raise SahmkRateLimitError("daily", retry_after=0, daily_exhausted=True)
        return {s: Quote(symbol=s, name="", name_en="", price=10) for s in symbols}

    async def top_volume_quotes(self, limit=50, market="TASI"):
        if self.mode == "temp429":
            raise SahmkRateLimitError("temporary", retry_after=600, daily_exhausted=False)
        if self.mode == "daily429":
            raise SahmkRateLimitError("daily", retry_after=0, daily_exhausted=True)
        return [Quote(symbol="1120", name="", name_en="", price=10, volume=1000)]

    async def top_value_quotes(self, limit=50, market="TASI"):
        if self.mode == "temp429":
            raise SahmkRateLimitError("temporary", retry_after=600, daily_exhausted=False)
        if self.mode == "daily429":
            raise SahmkRateLimitError("daily", retry_after=0, daily_exhausted=True)
        return [Quote(symbol="3333", name="", name_en="", price=11, value=5000, volume=500)]


class FakeTasilab:
    def __init__(self):
        self.calls = 0
        self.universe = []

    def set_universe(self, symbols):
        self.universe = list(symbols)

    def stats(self):
        return {"daily_requests": self.calls, "rate_limits": 0, "errors": 0}

    async def quote(self, symbol):
        self.calls += 1
        return Quote(symbol=symbol, name="", name_en="", price=20)

    async def market_summary(self):
        self.calls += 1
        return {"change_percent": 1}

    async def quotes(self, symbols):
        self.calls += 1
        return {s: Quote(symbol=s, name="", name_en="", price=20) for s in symbols}

    async def top_volume_quotes(self, limit=50, market="TASI"):
        self.calls += 1
        return [Quote(symbol="2222", name="", name_en="", price=20, volume=2000)]


def settings(tmp_path):
    return SimpleNamespace(
        timezone="Asia/Riyadh",
        state_dir=str(tmp_path),
        sahmk_daily_switch_limit=90,
        sahmk_local_daily_limit=100,
        provider_switch_on_daily_limit=True,
        provider_fallback_enabled=True,
    )


def test_temporary_429_does_not_switch_to_tasilab(tmp_path):
    async def run():
        sahmk = FakeSahmk(used=5)
        sahmk.mode = "temp429"
        tasi = FakeTasilab()
        router = ProviderRouter(settings(tmp_path), sahmk, tasi)
        result = await router.quotes(["1120"])
        assert result == {}
        assert tasi.calls == 0
        assert router.active_provider() == "sahmk"
    asyncio.run(run())


def test_daily_threshold_switches_to_tasilab(tmp_path):
    async def run():
        sahmk = FakeSahmk(used=90)
        tasi = FakeTasilab()
        router = ProviderRouter(settings(tmp_path), sahmk, tasi)
        quote = await router.quote("1120")
        assert quote.price == 20
        assert tasi.calls == 1
        assert router.active_provider() == "tasilab"
    asyncio.run(run())


def test_daily_429_switches_same_request_to_tasilab(tmp_path):
    async def run():
        sahmk = FakeSahmk(used=40)
        sahmk.mode = "daily429"
        tasi = FakeTasilab()
        router = ProviderRouter(settings(tmp_path), sahmk, tasi)
        quote = await router.quote("1120")
        assert quote.price == 20
        assert tasi.calls == 1
        assert router.active_provider() == "tasilab"
    asyncio.run(run())


def test_universe_cached_for_tasilab(tmp_path):
    async def run():
        sahmk = FakeSahmk(used=1)
        tasi = FakeTasilab()
        router = ProviderRouter(settings(tmp_path), sahmk, tasi)
        await router.companies("TASI")
        assert tasi.universe == ["1120", "2222"]
        assert (tmp_path / "universe_cache.json").exists()
    asyncio.run(run())


def test_fresh_deploy_daily_429_uses_bundled_universe(tmp_path):
    async def run():
        sahmk = FakeSahmk(used=10)
        sahmk.mode = "daily429"
        tasi = FakeTasilab()
        router = ProviderRouter(settings(tmp_path), sahmk, tasi)

        companies = await router.companies("TASI")

        assert len(companies) >= 200
        assert len(tasi.universe) >= 200
        assert router.active_provider() == "tasilab"
        assert router.stats()["universe_source"] == "bundled_bootstrap"

    asyncio.run(run())


def test_temporary_429_universe_falls_back_without_switch(tmp_path):
    async def run():
        sahmk = FakeSahmk(used=10)
        sahmk.mode = "temp429"
        tasi = FakeTasilab()
        router = ProviderRouter(settings(tmp_path), sahmk, tasi)

        companies = await router.companies("TASI")

        assert len(companies) >= 200
        assert router.active_provider() == "sahmk"
        assert tasi.calls == 0

    asyncio.run(run())


def test_daily_ip_like_failure_can_continue_with_tasilab_top_volume(tmp_path):
    async def run():
        sahmk = FakeSahmk(used=10)
        sahmk.mode = "daily429"
        tasi = FakeTasilab()
        router = ProviderRouter(settings(tmp_path), sahmk, tasi)

        companies = await router.companies("TASI")
        rows = await router.top_volume_quotes(50, "TASI")

        assert len(companies) >= 200
        assert tasi.universe
        assert rows and rows[0].symbol == "2222"
        assert router.active_provider() == "tasilab"

    asyncio.run(run())


def test_sahmk_403_one_call_falls_back_to_tasilab(tmp_path):
    async def run():
        sahmk = FakeSahmk(used=2)
        sahmk.mode = "403"
        tasi = FakeTasilab()
        router = ProviderRouter(settings(tmp_path), sahmk, tasi)
        quote = await router.quote("1120")
        assert quote.price == 20
        assert tasi.calls == 1
        # 403 fallback is per-call; it does not falsely mark the daily quota exhausted.
        assert router.active_provider() == "sahmk"
    asyncio.run(run())


def test_wide_scan_merges_sahmk_rankings_and_secondary_fill(tmp_path):
    async def run():
        class WideSahmk(FakeSahmk):
            async def top_volume_quotes(self, limit=50, market="TASI"):
                return [Quote(symbol=str(1000+i), name="", name_en="", price=10, volume=1000-i, value=1000-i) for i in range(50)]
            async def top_value_quotes(self, limit=50, market="TASI"):
                return [Quote(symbol=str(1025+i), name="", name_en="", price=10, volume=500-i, value=5000-i) for i in range(50)]
        class WideTasi(FakeTasilab):
            async def top_volume_quotes(self, limit=50, market="TASI"):
                self.calls += 1
                return [Quote(symbol=str(1075+i), name="", name_en="", price=20, volume=400-i, value=4000-i) for i in range(25)]
        sahmk=WideSahmk(used=1); tasi=WideTasi()
        router=ProviderRouter(settings(tmp_path), sahmk, tasi)
        rows=await router.top_volume_quotes(100,"TASI")
        assert len(rows)==100
        assert len({r.symbol for r in rows})==100
        assert tasi.calls==1
    asyncio.run(run())


def test_runtime_universe_excludes_non_equity_instruments(tmp_path):
    async def run():
        class MixedSahmk(FakeSahmk):
            async def companies(self, market="TASI"):
                return [
                    {"symbol": "1120", "security_type": "equity", "sector": "Banks"},
                    {"symbol": "4330", "security_type": "reit", "sector": "REITs"},
                    {"symbol": "9400", "security_type": "etf", "sector": "ETFs"},
                ]
        router = ProviderRouter(settings(tmp_path), MixedSahmk(used=1), FakeTasilab())
        rows = await router.companies("TASI")
        assert [r["symbol"] for r in rows] == ["1120"]
        assert [r["symbol"] for r in router.cached_companies()] == ["1120"]
    asyncio.run(run())


def test_candidate_pool_combines_volume_value_gainers_and_watch(tmp_path):
    async def run():
        class ChannelSahmk(FakeSahmk):
            async def top_volume_quotes(self, limit=50, market="TASI"):
                return [Quote(symbol=f"1{i:03d}", name="", name_en="", price=10, volume=10_000-i, value=1_000_000) for i in range(limit)]
            async def top_value_quotes(self, limit=50, market="TASI"):
                return [Quote(symbol=f"2{i:03d}", name="", name_en="", price=11, volume=1_000, value=100_000_000-i) for i in range(limit)]
            async def top_gainers_quotes(self, limit=50, market="TASI"):
                return [Quote(symbol=f"3{i:03d}", name="", name_en="", price=12, change_percent=9-i/10, volume=500, value=5_000_000) for i in range(limit)]

        class WatchTasi(FakeTasilab):
            async def quotes(self, symbols):
                self.calls += 1
                return {s: Quote(symbol=s, name="", name_en="", price=20, change_percent=4, value=4_000_000) for s in symbols}

        cfg = settings(tmp_path)
        cfg.stage1_watchlist_limit = 6
        cfg.stage1_watch_share = 0.10
        cfg.stage1_gainers_share = 0.25
        cfg.stage1_value_share = 0.30
        cfg.stage1_use_top_value = True
        cfg.stage1_use_gainers = True
        router = ProviderRouter(cfg, ChannelSahmk(used=1), WatchTasi())
        rows = await router.active_candidate_quotes(20, "TASI", watch_symbols=["4000", "4001"])
        symbols = [r.symbol for r in rows]
        assert len(rows) == 20
        assert len(set(symbols)) == 20
        assert "4000" in symbols and "4001" in symbols
        assert any(s.startswith("3") for s in symbols)  # gainers reserved
        assert any(s.startswith("2") for s in symbols)  # traded-value reserved
        assert any(s.startswith("1") for s in symbols)  # share-volume baseline

    asyncio.run(run())


def test_candidate_pool_short_cache_protects_sahmk_quota(tmp_path):
    async def run():
        class CountingSahmk(FakeSahmk):
            def __init__(self):
                super().__init__(used=1)
                self.volume_calls = self.value_calls = self.gainer_calls = 0
            async def top_volume_quotes(self, limit=50, market="TASI"):
                self.volume_calls += 1
                return [Quote(symbol="1120", name="", name_en="", price=10, volume=1000, value=10000)]
            async def top_value_quotes(self, limit=50, market="TASI"):
                self.value_calls += 1
                return [Quote(symbol="2222", name="", name_en="", price=10, volume=900, value=20000)]
            async def top_gainers_quotes(self, limit=50, market="TASI"):
                self.gainer_calls += 1
                return [Quote(symbol="3333", name="", name_en="", price=10, change_percent=4, volume=800, value=15000)]

        cfg = settings(tmp_path)
        cfg.stage1_watchlist_limit = 0
        cfg.stage1_candidate_cache_seconds = 180
        cfg.stage1_use_top_value = True
        cfg.stage1_use_gainers = True
        sahmk = CountingSahmk()
        router = ProviderRouter(cfg, sahmk, FakeTasilab())
        first = await router.active_candidate_quotes(3, "TASI")
        second = await router.active_candidate_quotes(3, "TASI")
        assert [q.symbol for q in first] == [q.symbol for q in second]
        assert sahmk.volume_calls == sahmk.value_calls == sahmk.gainer_calls == 1

    asyncio.run(run())
