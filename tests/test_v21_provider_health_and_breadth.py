from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest

from app.data.provider_router import ProviderRouter
from app.data.tasilab import TasilabProvider
from app.service import TradingService


def _tasilab_settings():
    return SimpleNamespace(
        tasilab_base_url="https://api.tasilab.com",
        tasilab_api_key="test",
        tasilab_timeout_seconds=5,
        tasilab_min_request_interval=0.1,
        tasilab_bulk_chunk_size=20,
        tasilab_single_fallback_scan_limit=60,
        tasilab_bulk_cooldown_seconds=300,
        tasilab_circuit_failure_threshold=3,
        tasilab_circuit_cooldown_seconds=300,
        tasilab_market_status_cache_seconds=60,
        timezone="Asia/Riyadh",
    )


@pytest.mark.asyncio
async def test_tasilab_market_status_bypasses_single_quote_circuit_and_parses_breadth():
    provider = TasilabProvider(_tasilab_settings())
    calls = []

    async def handler(request):
        calls.append(request.url.path)
        return httpx.Response(
            200,
            request=request,
            json={
                "data": {
                    "index_value": 11140.11,
                    "change_percent": -0.17,
                    "advancing": 91,
                    "declining": 154,
                    "unchanged": 17,
                }
            },
        )

    await provider.client.aclose()
    provider.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider._circuit_until = __import__("time").monotonic() + 300

    summary = await provider.market_summary()
    assert summary["advancers"] == 91
    assert summary["decliners"] == 154
    assert summary["unchanged"] == 17
    assert summary["market_status_source"] == "TASILAB_MARKET_STATUS"
    assert calls == ["/v1/market/status"]

    # Cached repeat should not spend another request.
    again = await provider.market_summary()
    assert again["advancers"] == 91
    assert calls == ["/v1/market/status"]
    await provider.close()


@pytest.mark.asyncio
async def test_breadth_recovery_prefers_market_status_before_full_market_quotes():
    svc = object.__new__(TradingService)
    now = datetime(2026, 8, 31, 11, 0, tzinfo=timezone.utc)
    svc._utc_now = lambda: now
    svc.s = SimpleNamespace(
        data_max_delay_minutes=30,
        market_breadth_tasilab_enabled=True,
        market_breadth_cache_seconds=900,
        market_breadth_min_coverage=0.65,
        market_breadth_min_samples=80,
    )
    svc.universe = [{"symbol": str(1000+i)} for i in range(100)]
    svc.last_market_breadth = None
    svc.last_market_breadth_at = None
    svc.last_market_summary = None
    svc.last_market_summary_at = None

    class TasiLab:
        quote_calls = 0
        async def market_summary(self):
            return {"advancers": 100, "decliners": 140, "unchanged": 20}
        async def quotes(self, symbols):
            self.quote_calls += 1
            raise AssertionError("full-market quotes must not run when market/status has breadth")

    t = TasiLab()
    svc.p = SimpleNamespace(tasilab=t)
    out = await svc._recover_market_breadth({"index_value": 11140})
    assert out["advancers"] == 100
    assert out["decliners"] == 140
    assert out["breadth_source"] == "TASILAB_MARKET_STATUS"
    assert t.quote_calls == 0


def _router_settings():
    return SimpleNamespace(
        timezone="Asia/Riyadh",
        provider_switch_on_daily_limit=True,
        sahmk_daily_switch_limit=95,
        sahmk_local_daily_limit=100,
        provider_fallback_enabled=True,
        provider_switch_on_429=False,
        bootstrap_universe_file="app/data/tasi_universe.json",
        state_dir="data",
        stage1_candidate_cache_seconds=180,
    )


class DummySahmk:
    def stats(self):
        return {
            "daily_requests": 1,
            "daily_limit": 100,
            "daily_exhausted": True,
            "cooldown_remaining": 0,
            "recent_requests": [],
        }


class DummyTasilab:
    def set_universe(self, symbols):
        pass
    def stats(self):
        return {
            "daily_requests": 13,
            "daily_successful_requests": 7,
            "requests_last_minute": 2,
            "errors": 6,
            "rate_limits": 0,
            "circuit_open": False,
            "market_status_last_success": "14:42:00",
            "market_status_last_error": None,
            "recent_requests": [],
        }


def test_router_exposes_real_sahmk_ip_daily_block_state_and_tasilab_usage():
    router = ProviderRouter(_router_settings(), DummySahmk(), DummyTasilab())
    router._activate_daily_switch(
        "SAHMK 429 (daily): IP daily rate limit exceeded (100 requests/day). Resets at midnight Arabia Standard Time (UTC+3)."
    )
    stats = router.stats()
    assert stats["sahmk_blocked_for_day"] is True
    assert stats["sahmk_block_type"] == "IP_DAILY"
    assert stats["sahmk_runtime_state"] == "BLOCKED_IP_DAILY"
    assert stats["tasilab_requests"] == 13
    assert stats["tasilab_successful_requests"] == 7
    assert stats["tasilab_requests_last_minute"] == 2


def test_api_usage_does_not_call_sahmk_normal_when_router_has_ip_daily_block():
    svc = object.__new__(TradingService)
    svc.s = SimpleNamespace(sahmk_daily_switch_limit=95, sahmk_local_daily_limit=100)
    svc.p = SimpleNamespace(stats=lambda: {
        "sahmk_daily_requests": 1,
        "sahmk_blocked_for_day": True,
        "sahmk_block_type": "IP_DAILY",
        "sahmk_block_reason": "IP daily rate limit exceeded",
        "tasilab_requests": 13,
        "tasilab_successful_requests": 7,
        "tasilab_requests_last_minute": 2,
        "active_provider_detail": "TASILAB",
        "provider_order": "SAHMK → Tasilab",
        "last_switch_at": "14:40:57",
        "last_switch_reason": "IP daily rate limit exceeded",
    })
    text = svc.api_usage_text("all")
    assert "محظور على IP" in text
    assert "1/95" in text
    assert "13 محاولة اليوم | 7 ناجحة" in text
    assert "2/120" in text
