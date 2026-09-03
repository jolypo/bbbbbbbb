from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.data.providers.base import Quote
from app.market.quality import TASIMarketQualityEngine
from app.scanner.screener import fast_score
from app.service import TradingService
from app.strategy.two_stage import HunterDecision, judge
from app.risk.levels import saudi_tick_size


def _hunter(score=80):
    return HunterDecision(
        "BUY_CANDIDATE", score, ["ok"], [],
        {
            "relative_volume": 1.2,
            "adx14": 22,
            "di_spread": 4,
            "resistance_distance_atr": 0.8,
            "structure_state": "HH_HL",
            "vwap_distance_atr": 0.4,
            "ema20_distance_atr": 0.4,
            "is_breakout": 0,
            "failed_breakout": 0,
        },
        "A",
    )


def test_fast_ranker_rewards_saudi_relative_strength_in_red_tasi():
    q = Quote(symbol="X", name="", name_en="", price=20, change_percent=5.0,
              volume=700_000, value=20_000_000, raw={})
    strong = fast_score(q, "NEUTRAL", market_change_pct=-0.5)
    weak = fast_score(q, "NEUTRAL", market_change_pct=4.5)
    assert strong.score > weak.score
    assert "exceptional_relative_strength" in strong.reasons


def test_sector_median_weakness_is_not_hard_veto_without_broad_weakness():
    mq = TASIMarketQualityEngine().evaluate({
        "index_value": 11000, "index_change_percent": 0.2,
        "advancing": 130, "declining": 110,
    })
    j = judge(_hunter(85), mq, traded_value=8_000_000, min_traded_value=2_000_000,
              sector_strength_available=True, sector_strength_pct=-2.2,
              sector_strength_breadth=0.60, stock_change_pct=2.0, market_change_pct=0.2)
    assert not any("القطاع ضعيف على نطاق واسع" in x for x in j.blockers)


def test_sector_broad_weakness_remains_hard_veto():
    mq = TASIMarketQualityEngine().evaluate({
        "index_value": 11000, "index_change_percent": 0.2,
        "advancing": 130, "declining": 110,
    })
    j = judge(_hunter(95), mq, traded_value=8_000_000, min_traded_value=2_000_000,
              sector_strength_available=True, sector_strength_pct=-2.2,
              sector_strength_breadth=0.20, stock_change_pct=3.0, market_change_pct=0.2)
    assert j.decision == "REJECT"
    assert any("القطاع ضعيف على نطاق واسع" in x for x in j.blockers)


def test_liquidity_floor_scales_with_saudi_session_progress():
    svc = TradingService.__new__(TradingService)
    svc.s = SimpleNamespace(
        market_open="10:00", market_close="15:00",
        min_daily_traded_value=2_000_000, liquidity_progress_floor=0.25,
    )
    early = datetime(2026, 8, 30, 10, 30, tzinfo=ZoneInfo("Asia/Riyadh"))
    late = datetime(2026, 8, 30, 14, 30, tzinfo=ZoneInfo("Asia/Riyadh"))
    assert svc._effective_min_traded_value(early) == 500_000
    assert svc._effective_min_traded_value(late) == 1_800_000


def test_settings_default_entry_wait_matches_deploy_intent():
    text = Path("app/config/settings.py").read_text()
    assert "entry_wait_expiry_minutes: int = 1440" in text


def test_saudi_tick_bands_remain_exchange_compliant():
    assert saudi_tick_size(24.99) == 0.01
    assert saudi_tick_size(25.00) == 0.02
    assert saudi_tick_size(50.00) == 0.05
    assert saudi_tick_size(100.00) == 0.10
    assert saudi_tick_size(250.00) == 0.20
    assert saudi_tick_size(500.00) == 0.50
