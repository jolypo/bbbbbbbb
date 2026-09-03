from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.strategy.waseem20 import evaluate_waseem20, extract_auction_context, build_wait_plan


def base_features():
    return {
        "time_adjusted_rvol": 2.2,
        "volume_trend_ratio": 1.3,
        "obv_slope5": 1,
        "ad_slope5": 1,
        "is_breakout": 1,
        "retest_confirmed": 1,
        "close_position": 0.80,
        "ema20_slope_pct": 0.2,
        "failed_breakout": 0,
        "vwap": 99.3,
        "ema20": 98.8,
        "support20": 97.5,
        "vwap_distance_atr": 0.6,
        "ema20_distance_atr": 0.9,
        "atr14": 2.0,
        "close": 100.0,
        "resistance_distance_atr": 2.0,
        "h1_ema20_slope_pct": 0.2,
        "h1_close": 100,
        "h1_ema20": 98,
        "d1_ema20_slope_pct": 0.2,
        "d1_close": 100,
        "d1_ema20": 95,
    }


def quote(**kwargs):
    data = dict(price=100.0, change_percent=5.0, value=8_000_000.0, bid=99.8, ask=100.1, raw={})
    data.update(kwargs)
    return SimpleNamespace(**data)


def test_auction_missing_fields_are_explicit_not_fabricated():
    a = extract_auction_context(quote(), local_now=datetime(2026, 9, 1, 9, 45, tzinfo=ZoneInfo("Asia/Riyadh")))
    assert a.session == "OPENING_AUCTION"
    assert a.indicative_price is None
    assert "سعر المزاد الاسترشادي" in a.unavailable_fields
    assert "أفضل طلب" in a.available_fields


def test_auction_fields_used_only_when_provider_returns_them():
    q = quote(raw={"indicative_price": 103.5, "matched_volume": 25000, "auction_imbalance": 0.72})
    a = extract_auction_context(q, local_now=datetime(2026, 9, 1, 9, 45, tzinfo=ZoneInfo("Asia/Riyadh")))
    assert a.indicative_price == 103.5
    assert a.indicative_volume == 25000
    assert a.imbalance == 0.72


def test_preopen_never_becomes_trade_ready_even_with_strong_catalyst():
    d = evaluate_waseem20(
        features=base_features(), quote=quote(),
        market_context={"change_percent": -0.5, "breadth_available": False},
        catalyst_context={"score": 5, "impact": "HIGH", "items": [{"headline": "material"}]},
        leadership_score=95, persistence_score=85, min_traded_value=2_000_000,
        local_now=datetime(2026, 9, 1, 9, 45, tzinfo=ZoneInfo("Asia/Riyadh")),
        liquidity_state="NORMAL", limit_state="NORMAL",
    )
    assert d.state == "WAIT"
    assert any("مزاد الافتتاح" in x for x in d.blockers)


def test_strong_continuous_setup_can_be_trade_ready():
    d = evaluate_waseem20(
        features=base_features(), quote=quote(),
        market_context={"change_percent": -0.5, "breadth_available": True, "advancers": 100, "decliners": 150},
        catalyst_context={"score": 2.5, "impact": "HIGH", "items": [{"headline": "contract"}]},
        leadership_score=85, persistence_score=75, min_traded_value=2_000_000,
        local_now=datetime(2026, 9, 1, 11, 0, tzinfo=ZoneInfo("Asia/Riyadh")),
        liquidity_state="NORMAL", limit_state="NORMAL",
    )
    assert d.state == "TRADE_READY"
    assert d.horizon in {"intraday", "two_day", "multi_session"}
    assert d.total_score >= 72


def test_extended_leader_is_wait_with_lower_pullback_anchor_not_hidden():
    f = base_features()
    f.update({"vwap": 100, "ema20": 99, "vwap_distance_atr": 5.0, "ema20_distance_atr": 5.0,
              "atr14": 2.0, "close": 110, "retest_confirmed": 0})
    q = quote(price=110, change_percent=8.5, value=10_000_000, bid=109.8, ask=110.2)
    d = evaluate_waseem20(
        features=f, quote=q, market_context={"change_percent": 0},
        catalyst_context={"score": 0, "impact": "NONE", "items": []},
        leadership_score=90, persistence_score=70, min_traded_value=2_000_000,
        local_now=datetime(2026, 9, 1, 11, 0, tzinfo=ZoneInfo("Asia/Riyadh")),
        liquidity_state="NORMAL", limit_state="NEAR_LIMIT_UP",
    )
    assert d.state == "WAIT"
    assert d.entry_anchor < q.price
    plan = build_wait_plan(f, q, d, min_rr=1.8)
    assert plan["available"] is True
    assert plan["entry"] < q.price
    assert plan["tp1"] > plan["entry"] > plan["sl"]


def test_unified_engine_selects_multi_session_when_daily_persistence_is_strong():
    d = evaluate_waseem20(
        features=base_features(), quote=quote(), market_context={"change_percent": 0},
        catalyst_context={"score": 2, "impact": "MEDIUM", "items": [{"headline": "news"}]},
        leadership_score=80, persistence_score=80, min_traded_value=2_000_000,
        local_now=datetime(2026, 9, 1, 12, 0, tzinfo=ZoneInfo("Asia/Riyadh")),
        liquidity_state="NORMAL", limit_state="NORMAL",
    )
    assert d.horizon == "multi_session"
    assert d.horizon_sessions == 5
