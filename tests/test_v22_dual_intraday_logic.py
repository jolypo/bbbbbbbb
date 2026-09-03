from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

import pytest

from app.data.providers.base import Quote
from app.strategy.emerging_leader import stage1_emerging_score, mtf_consensus_score, execution_state
from app.service import TradingService


def test_emerging_leader_stage1_surfaces_strong_relative_mover():
    q = Quote(
        symbol="1200", name="Leader", name_en="Leader", price=50.0,
        change_percent=8.8, volume=2_000_000, value=100_000_000,
        updated_at=datetime.now(timezone.utc), raw={},
    )
    snap = stage1_emerging_score(
        q, market_change_pct=-0.4, acceleration=1.2, persistence=72,
        min_traded_value=2_000_000, daily_limit_pct=10, near_limit_buffer_pct=0.75,
    )
    assert snap.score >= 80
    assert snap.relative_strength > 9
    assert snap.state in {"WAIT_PULLBACK", "NO_CHASE"}


def test_emerging_leader_does_not_turn_limit_up_into_executable_entry():
    state = execution_state(
        leadership_score=96, entry_quality_score=80, mtf_score=90,
        limit_state="LIMIT_UP", features={"vwap_distance_atr": 0.5, "ema20_distance_atr": 0.5},
    )
    assert state == "NO_CHASE"


def test_emerging_leader_mtf_consensus_rewards_alignment():
    score, reasons = mtf_consensus_score({
        "ema9": 11, "ema20": 10, "ema20_slope_pct": 0.3, "macd_hist": 0.2,
        "momentum5_pct": 1.0, "close_position": 0.8,
        "h1_close": 12, "h1_ema9": 11.5, "h1_ema20": 11, "h1_ema20_slope_pct": 0.2, "h1_macd_hist": 0.1,
        "d1_close": 13, "d1_ema20": 12, "d1_ema20_slope_pct": 0.1, "d1_macd_hist": 0.2, "d1_rsi14": 61,
    })
    assert score >= 90
    assert reasons


def test_telegram_intraday_menu_exposes_two_named_logic_modes():
    from pathlib import Path
    text = (Path(__file__).resolve().parents[1] / "app/telegram/bots.py").read_text(encoding="utf-8")
    assert "🛡️ الجودة الأساسية" in text
    assert "🚀 صائد القادة" in text
    assert 'intraday_logic="emerging"' in text
    assert 'async def _run_search(self, update, screen_limit, detail_limit, label, *, full_market=False, trade_horizon="intraday"):' in text


@pytest.mark.asyncio
async def test_breadth_final_fallback_uses_yahoo_when_tasilab_and_cache_fail():
    now = datetime.now(timezone.utc)
    quotes = [
        Quote(str(i), "", "", 10, change_percent=(1 if i % 3 == 0 else -1 if i % 3 == 1 else 0),
              volume=1000, value=10000, updated_at=now, raw={})
        for i in range(1, 101)
    ]

    class BadTasilab:
        async def market_summary(self):
            raise RuntimeError("502")
        async def quotes(self, symbols):
            raise RuntimeError("502")

    class Hist:
        async def market_snapshots(self, symbols, concurrency=10):
            return quotes

    svc = object.__new__(TradingService)
    svc.s = SimpleNamespace(
        market_breadth_tasilab_enabled=True,
        market_breadth_cache_seconds=900,
        market_breadth_min_samples=80,
        market_breadth_min_coverage=0.65,
        market_breadth_yahoo_fallback_enabled=True,
        market_breadth_yahoo_retry_seconds=900,
        data_max_delay_minutes=30,
    )
    svc.p = SimpleNamespace(tasilab=BadTasilab())
    svc.h = Hist()
    svc.universe = [{"symbol": str(i)} for i in range(1, 101)]
    svc.last_market_breadth = None
    svc.last_market_breadth_at = None
    svc.last_market_breadth_yahoo_attempt_at = None
    svc._utc_now = lambda: now
    svc._quote_freshness = lambda q: (True, "fresh", 0.0)
    stored = {}
    svc._store_market_breadth = lambda snap: stored.update(snap)

    out = await TradingService._recover_market_breadth(svc, {}, force=True)
    assert out["breadth_source"] == "YAHOO_FULL_MARKET"
    assert out["advancers"] > 0
    assert out["decliners"] > 0
    assert stored["breadth_source"] == "YAHOO_FULL_MARKET"
