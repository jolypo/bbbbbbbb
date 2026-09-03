from datetime import datetime, timezone
from pathlib import Path

from app.data.providers.base import Quote
from app.strategy.saudi_native import evaluate_saudi_opportunity
from app.telegram.messages import signal_message


def _features(**overrides):
    base = {
        "close": 50.0, "atr14": 1.1, "time_adjusted_rvol": 1.8,
        "relative_volume": 1.8, "volume_trend_ratio": 1.25,
        "obv_slope5": 1.0, "ad_slope5": 1.0,
        "is_breakout": 1.0, "retest_confirmed": 0.0,
        "close_position": 0.82, "ema20_slope_pct": 0.4,
        "macd_hist": 0.2, "structure_state": "HH_HL",
        "failed_breakout": 0.0, "vwap_distance_atr": 0.5,
        "ema20_distance_atr": 0.8, "resistance_distance_atr": 1.4,
        "price_volume_divergence": 0.0,
    }
    base.update(overrides)
    return base


def _quote(value=20_000_000, change=4.0):
    return Quote("1234", "اختبار", "Test", 50.0, change_percent=change,
                 volume=700_000, value=value, updated_at=datetime.now(timezone.utc))


def test_strong_saudi_setup_can_be_trade_ready_without_legacy_approve_semantics():
    d = evaluate_saudi_opportunity(
        horizon="intraday", features=_features(), quote=_quote(),
        market_context={"change_percent": -0.4, "breadth_available": False},
        min_traded_value=2_000_000, leadership_score=86, entry_quality_score=78,
        persistence_score=80, catalyst_context={"available": True, "score": 2.0},
        liquidity_state="HIGH_LIQUIDITY", limit_state="NORMAL",
    )
    assert d.state == "TRADE_READY"
    assert d.money_flow_score >= 70
    assert d.target_feasibility_score >= 50


def test_low_liquidity_exception_stays_visible_but_not_trade_ready():
    d = evaluate_saudi_opportunity(
        horizon="intraday", features=_features(), quote=_quote(value=800_000, change=9.5),
        market_context={"change_percent": -0.3, "breadth_available": False},
        min_traded_value=2_000_000, leadership_score=94, entry_quality_score=72,
        persistence_score=88, catalyst_context={"available": False, "score": 0},
        liquidity_state="LOW_LIQUIDITY", limit_state="NEAR_LIMIT_UP",
    )
    assert d.state in {"NO_CHASE", "WAIT_PULLBACK", "LEADER", "SETUP"}
    assert d.state != "TRADE_READY"
    assert any("سيولة التنفيذ" in x for x in d.blockers)


def test_two_day_engine_has_two_session_horizon():
    d = evaluate_saudi_opportunity(
        horizon="two_day", features=_features(), quote=_quote(),
        market_context={"change_percent": 0.2, "breadth_available": True, "advancers": 150, "decliners": 90},
        min_traded_value=2_000_000, leadership_score=82, entry_quality_score=70,
        persistence_score=84, catalyst_context={"available": True, "score": 1.5},
        liquidity_state="HIGH_LIQUIDITY", limit_state="NORMAL",
    )
    assert d.horizon == "two_day"
    assert d.horizon_sessions == 2
    assert d.state in {"TRADE_READY", "SETUP", "LEADER"}


def test_public_signal_is_rtl_marked():
    text = signal_message({
        "name": "اختبار", "symbol": "1234", "trade_type": "تداول يومي",
        "trade_horizon": "intraday", "entry_low": 10, "entry_high": 10.1,
        "sl": 9.8, "tp1": 10.4, "tp2": 10.6, "tp3": 10.8, "rr_tp1": 2,
        "leadership_score": 80, "entry_quality_score": 75, "persistence_score": 70,
        "hunter_score": 80, "judge_score": 80, "required_score": 70,
        "judge_decision": "APPROVE", "market_state": "NORMAL", "liquidity_state": "HIGH_LIQUIDITY",
        "volatility_state": "NORMAL", "sector": "اختبار", "catalyst_score": 0,
        "limit_state": "NORMAL", "learning_adjustment": 0,
    })
    assert text.startswith("\u200f")


def test_v24_menu_and_scanner_source_contracts():
    root = Path(__file__).resolve().parents[1]
    bots = (root / "app/telegram/bots.py").read_text(encoding="utf-8")
    service = (root / "app/service.py").read_text(encoding="utf-8")
    assert "⏭️ فرص 1–2 جلسة" in bots
    assert "🛰️ تشغيل السكان السعودي" in bots
    assert "run_saudi_scanner" in service
    assert '"TRADE_READY"' in service
    assert '"POST_BUILD_DROP"' in service
