from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.strategy.waseem30 import evaluate_waseem30, stage1_waseem30_score


def _features(**overrides):
    f = {
        "time_adjusted_rvol": 2.0, "relative_volume": 2.0, "volume_trend_ratio": 1.4,
        "atr14": 1.0, "close": 100.5, "vwap": 100.2, "ema20": 100.1,
        "vwap_distance_atr": .3, "ema20_distance_atr": .4,
        "structure_state": "HH_HL", "retest_confirmed": 1.0, "is_breakout": 1.0,
        "failed_breakout": 0.0, "close_position": .8, "candle_body_pct": .6,
        "macd_hist_rising2": 1.0, "momentum5_pct": 1.0, "rsi14": 60,
        "resistance_distance_atr": 1.5, "support20": 99.5, "resistance20": 101.5,
        "h1_close": 100, "h1_ema20": 99, "h1_ema20_slope_pct": .2,
        "d1_close": 100, "d1_ema20": 99, "d1_ema20_slope_pct": .2,
    }
    f.update(overrides)
    return f


def _quote(change=.5, price=100.5, bid=100.4, ask=100.5, value=8_000_000, volume=500_000):
    return SimpleNamespace(price=price, change_percent=change, value=value, volume=volume, bid=bid, ask=ask, raw={})


def _eval(q=None, f=None, previous=None, leadership=70, persistence=70, liquidity="NORMAL_LIQUIDITY"):
    return evaluate_waseem30(
        features=f or _features(), quote=q or _quote(), market_context={"change_percent": 0.0},
        catalyst_context={}, leadership_score=leadership, persistence_score=persistence,
        min_traded_value=2_000_000, local_now=datetime(2026, 9, 2, 10, 30, tzinfo=ZoneInfo("Asia/Riyadh")),
        liquidity_state=liquidity, previous_snapshot=previous or {"value": 4_000_000, "volume": 250_000, "rs": 0.0, "value_velocity": 1_000_000},
    )


def test_detects_early_half_percent_with_flow_acceleration():
    d = _eval()
    assert d.early_score >= 60
    assert d.state in {"BUILDING", "SETUP", "TRADE_READY"}
    assert d.move_stage in {"EARLY_MOVE", "ACTIVE_MOVE"}


def test_stage1_does_not_require_big_price_jump():
    early, _, _ = stage1_waseem30_score(_quote(change=.6), 0.0, previous={"value": 4_000_000,"volume":250_000,"rs":0}, min_traded_value=2_000_000)
    late, _, _ = stage1_waseem30_score(_quote(change=6.0), 0.0, previous={"value": 4_000_000,"volume":250_000,"rs":5.8}, min_traded_value=2_000_000)
    assert early >= late - 10  # large move is not the discovery requirement


def test_extended_five_plus_waits_for_pullback():
    q = _quote(change=6.0, price=106, bid=None, ask=None, value=20_000_000, volume=1_000_000)
    f = _features(close=106, vwap=102, ema20=101, vwap_distance_atr=2.4, ema20_distance_atr=2.8, resistance20=107)
    d = _eval(q=q, f=f, leadership=82, persistence=82)
    assert d.state == "WAIT_PULLBACK"
    assert d.move_stage == "EXTENDED"


def test_missing_bid_ask_is_unknown_not_zero_or_reject():
    d = _eval(q=_quote(bid=None, ask=None))
    assert d.data_status["bid_ask"] == "UNAVAILABLE"
    assert d.entry_score > 0
    assert not any("Bid/Ask" in b for b in d.blockers)


def test_missing_auction_after_open_does_not_block_trade():
    d = _eval()
    assert d.auction.session == "CONTINUOUS"
    assert not any("المزاد" in b for b in d.blockers)


def test_wait_like_states_always_have_explicit_reason():
    d = _eval(f=_features(structure_state="RANGE", retest_confirmed=0, is_breakout=0, close_position=.5), leadership=55, persistence=52)
    if d.state != "TRADE_READY":
        assert d.blockers


def test_internal_external_liquidity_map_is_exposed():
    d = _eval()
    assert "internal_liquidity_above_atr" in d.liquidity_map
    assert "external_liquidity_up" in d.liquidity_map
    assert d.liquidity_map["execution_data_status"] == "AVAILABLE"


def test_composite_is_not_hard_72_gate():
    # Readiness is core-condition based; total is only a priority rank.
    d = _eval()
    assert d.state == "TRADE_READY"
    assert d.early_score >= 60


def test_data_completeness_is_reported():
    d = _eval(q=_quote(bid=None, ask=None))
    assert 0 < d.data_completeness_score < 100


def test_failed_breakout_invalidates():
    d = _eval(f=_features(failed_breakout=1.0))
    assert d.state == "INVALIDATED"
    assert d.blockers
