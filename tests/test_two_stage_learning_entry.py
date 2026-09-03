from types import SimpleNamespace
from pathlib import Path

from app.database.json_store import JsonStore
from app.trades.manager import TradeManager
from app.market.quality import TASIMarketQualityEngine
from app.strategy.two_stage import HunterDecision, judge
from app.learning.memory import LearningMemory


def settings():
    return SimpleNamespace(
        max_open_trades=5, fee_bps=15.5, slippage_bps=5,
        tp1_percent=30, tp2_percent=30, tp3_percent=40,
        trailing_after_tp1_to_entry=True, trailing_stop_enabled=False,
        trailing_after_tp2_atr=1.0, entry_wait_expiry_minutes=1440, min_rr=1.0,
    )


def sig():
    return {
        "trade_id":"TASI-X", "symbol":"1120","name":"Test","strategy":"S",
        "direction":"BUY", "entry":100.0,"entry_low":99.8,"entry_high":100.2,
        "sl":98.0,"tp1":102.0,"tp2":103.0,"tp3":104.0,
        "market_state":"BULL_TREND","liquidity_state":"HIGH_LIQUIDITY",
        "volatility_state":"NORMAL","hunter_score":93,"judge_score":93,
    }


def test_waiting_entry_does_not_open_until_zone_touch(tmp_path):
    tm=TradeManager(JsonStore(tmp_path), settings())
    assert tm.add(sig())
    t=tm.store.state()["open_trades"][0]
    assert t["status"]=="WAITING_ENTRY"
    t,ev=tm.activate_entry("1120",101.0)
    assert ev==[] and t["status"]=="WAITING_ENTRY"
    t,ev=tm.activate_entry("1120",100.0,when="2026-08-30T08:00:00+00:00")
    assert ev==["ENTRY"] and t["status"]=="OPEN"
    assert t["actual_entry"]==100.0


def test_bar_can_activate_missed_snapshot_conservatively(tmp_path):
    tm=TradeManager(JsonStore(tmp_path), settings())
    tm.add(sig())
    t,ev=tm.activate_entry_bar("1120",100.5,99.5,100.3,"2026-08-30T08:15:00+00:00")
    assert ev==["ENTRY"]
    assert t["entry"]==100.0
    assert t["entry_activation_source"]=="completed_bar"


def test_market_quality_range_raises_required_score():
    q=TASIMarketQualityEngine().evaluate({"index_value":11000,"index_change_percent":0.05,"advancing":100,"declining":98,"adx":14})
    assert q.state=="RANGE"
    assert q.required_score>=71


def test_judge_rejects_low_liquidity_even_with_high_hunter_score():
    h=HunterDecision("BUY_CANDIDATE",95,["strong"],[],{
        "relative_volume":0.45,"adx14":30,"di_spread":8,"resistance_distance_atr":1.0,
        "structure_state":"HH_HL","vwap_distance_atr":0.5,"ema20_distance_atr":0.5,
    },"A+")
    mq=TASIMarketQualityEngine().evaluate({"index_value":11000,"index_change_percent":0.8,"advancing":150,"declining":70})
    j=judge(h,mq,traded_value=500_000,min_traded_value=2_000_000)
    assert j.decision=="REJECT"
    assert j.liquidity_state=="LOW_LIQUIDITY"


def test_judge_can_wait_for_confirmation():
    h=HunterDecision("BUY_CANDIDATE",95,["strong"],[],{
        "relative_volume":1.6,"adx14":30,"di_spread":8,"resistance_distance_atr":0.1,
        "structure_state":"MIXED","vwap_distance_atr":0.5,"ema20_distance_atr":0.5,
        "is_breakout":0,"failed_breakout":0,
    },"A+")
    mq=TASIMarketQualityEngine().evaluate({"index_value":11000,"index_change_percent":0.8,"advancing":150,"declining":70})
    j=judge(h,mq,traded_value=10_000_000,min_traded_value=2_000_000)
    assert j.decision=="WAIT"
    assert j.confirmed_setup=="WAITING_CONFIRMATION"


def test_learning_collecting_then_active_and_bounded(tmp_path):
    m=LearningMemory(Path(tmp_path)/"learning_memory.json",min_samples=12,max_adjustment=2)
    base={"strategy":"S","direction":"BUY","market_state":"BULL_TREND","liquidity_state":"HIGH_LIQUIDITY"}
    for i in range(11):
        row=dict(base,trade_id=str(i),symbol="1120",result="WIN",result_pct=2.0,entry=100,exit=102)
        assert m.record(row)
    assert m.stats(base)["status"]=="COLLECTING"
    row=dict(base,trade_id="11",symbol="1120",result="WIN",result_pct=2.0,entry=100,exit=102)
    m.record(row)
    st=m.stats(base)
    assert st["status"]=="ACTIVE"
    assert 0 < st["adjustment"] <= 2


def test_learning_import_validation(tmp_path):
    m=LearningMemory(Path(tmp_path)/"learning_memory.json")
    raw=b'{"version":1,"trades":[{"trade_id":"1","result":"WIN"},{"trade_id":"2","result":"PENDING"}]}'
    n=m.import_bytes(raw)
    assert n==1

def test_actual_entry_rr_gate_can_keep_trade_waiting(tmp_path):
    st=settings(); st.min_rr=1.8
    tm=TradeManager(JsonStore(tmp_path), st)
    s=sig(); s["tp1"]=104.0; s["sl"]=98.0
    tm.add(s)
    # 100.2 gives RR < 1.8; 100.0 gives exactly 2.0.
    t,ev=tm.activate_entry("1120",100.2)
    assert ev==[] and t["status"]=="WAITING_ENTRY"
    t,ev=tm.activate_entry("1120",100.0)
    assert ev==["ENTRY"] and t["actual_rr_tp1"]>=1.8


def test_same_bar_entry_and_stop_is_conservative_loss(tmp_path):
    st=settings(); st.min_rr=1.0
    tm=TradeManager(JsonStore(tmp_path), st)
    s=sig(); s["sl"]=98.0
    tm.add(s)
    t,ev=tm.activate_entry_bar("1120",100.5,97.5,99.0,"2026-08-30T08:15:00+00:00")
    assert ev==["ENTRY","SL"]
    assert t["status"]=="CLOSED_SL"
    assert t["bar_execution_assumption"]=="ENTRY_THEN_STOP_CONSERVATIVE"

def test_market_quality_high_volatility_raises_requirement():
    q=TASIMarketQualityEngine().evaluate({"index_value":11000,"index_change_percent":0.4,"advancing":120,"declining":100,"atr_pct":2.4})
    assert q.state=="HIGH_VOLATILITY"
    assert q.required_score>=73

def test_saudi_rvol_is_not_a_hard_liquidity_reject_when_traded_value_is_good():
    h=HunterDecision("BUY_CANDIDATE",90,["strong"],[],{
        "relative_volume":0.85,"adx14":17,"di_spread":1,"resistance_distance_atr":0.8,
        "structure_state":"HH_HL","vwap_distance_atr":0.5,"ema20_distance_atr":0.5,
        "is_breakout":0,"failed_breakout":0,
    },"A+")
    mq=TASIMarketQualityEngine().evaluate({"index_value":11000,"index_change_percent":0.5,"advancing":140,"declining":90})
    j=judge(h,mq,traded_value=12_000_000,min_traded_value=2_000_000)
    assert j.liquidity_state=="HIGH_LIQUIDITY"
    assert j.decision=="APPROVE"


def test_saudi_low_adx_and_small_positive_di_are_confirmation_not_veto():
    h=HunterDecision("BUY_CANDIDATE",90,["strong"],[],{
        "relative_volume":1.0,"adx14":16,"di_spread":1,"resistance_distance_atr":0.8,
        "structure_state":"HH_HL","vwap_distance_atr":0.4,"ema20_distance_atr":0.4,
        "is_breakout":0,"failed_breakout":0,
    },"A+")
    mq=TASIMarketQualityEngine().evaluate({"index_value":11000,"index_change_percent":0.6,"advancing":150,"declining":80})
    j=judge(h,mq,traded_value=8_000_000,min_traded_value=2_000_000)
    assert not any("ADX لا يؤكد" in x for x in j.blockers)
    assert not any("+DI لا يتفوق" in x for x in j.blockers)
    assert j.decision=="APPROVE"

def test_tasi_moderate_extension_and_divergence_are_diagnostic_not_duplicate_hard_veto():
    h=HunterDecision("BUY_CANDIDATE",90,["strong"],[],{
        "relative_volume":1.1,"adx14":19,"di_spread":2,"resistance_distance_atr":0.9,
        "structure_state":"HH_HL","vwap_distance_atr":1.6,"ema20_distance_atr":2.1,
        "price_volume_divergence":1.0,"is_breakout":0,"failed_breakout":0,
    },"A+")
    mq=TASIMarketQualityEngine().evaluate({"index_value":11000,"index_change_percent":0.6,"advancing":150,"declining":80})
    j=judge(h,mq,traded_value=9_000_000,min_traded_value=2_000_000)
    assert not any("منع Chase" in x for x in j.blockers)
    assert not any("تباعد سعر/حجم" == x for x in j.blockers)
    assert j.decision=="APPROVE"


def test_mild_red_tasi_with_positive_breadth_is_not_bear_veto():
    q=TASIMarketQualityEngine().evaluate({"index_value":11237.93,"index_change_percent":-0.20,"advancing":143,"declining":115,"ema20":11300,"ema50":11400})
    assert q.state in {"BEAR_PRESSURE","NORMAL","MIXED","RANGE"}
    assert q.state != "BEAR_TREND"

def test_relative_strength_can_lift_confirmed_saudi_setup():
    h=HunterDecision("BUY_CANDIDATE",54,["setup"],[],{
        "relative_volume":1.6,"adx14":24,"di_spread":6,"resistance_distance_atr":0.8,
        "structure_state":"HH_HL","vwap_distance_atr":0.4,"ema20_distance_atr":0.5,
        "is_breakout":0,"failed_breakout":0,
    },"B")
    mq=TASIMarketQualityEngine().evaluate({"index_value":11237.93,"index_change_percent":-0.20,"advancing":143,"declining":115})
    j=judge(h,mq,traded_value=12_000_000,min_traded_value=2_000_000,stock_change_pct=5.0,market_change_pct=-0.2)
    assert j.decision=="APPROVE"
    assert j.score>=j.required_score
