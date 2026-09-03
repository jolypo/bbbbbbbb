from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any

from app.strategy.waseem20 import AuctionContext, extract_auction_context


def _f(v: Any, default=0.0) -> float:
    try:
        return float(v if v is not None else default)
    except (TypeError, ValueError):
        return float(default)


def _clamp(v: float, lo=0.0, hi=100.0) -> float:
    return max(lo, min(hi, float(v)))


@dataclass
class Waseem30Decision:
    state: str
    move_stage: str
    entry_type: str
    horizon: str
    horizon_sessions: int
    total_score: float
    early_score: float
    market_score: float
    money_flow_score: float
    leadership_score: float
    catalyst_score: float
    structure_score: float
    entry_score: float
    target_feasibility_score: float
    risk_score: float
    pullback_score: float
    entry_anchor: float | None
    data_completeness_score: float
    data_status: dict
    liquidity_map: dict
    early_components: dict
    reasons: list[str]
    blockers: list[str]
    auction: AuctionContext
    snapshot: dict

    def to_dict(self):
        return asdict(self)


def stage1_waseem30_score(quote, market_change_pct: float, *, previous: dict | None = None,
                          min_traded_value: float = 2_000_000.0) -> tuple[float, list[str], dict]:
    """Cheap *early* ranker. Price-change is deliberately not the main discovery signal."""
    previous = dict(previous or {})
    value = _f(getattr(quote, "value", 0), 0)
    volume = _f(getattr(quote, "volume", 0), 0)
    change = _f(getattr(quote, "change_percent", 0), 0)
    rs = change - _f(market_change_pct, 0)
    pv = _f(previous.get("value"), 0)
    pvol = _f(previous.get("volume"), 0)
    prs = _f(previous.get("rs"), rs)

    value_growth = ((value - pv) / pv) if pv > 0 and value >= pv else 0.0
    volume_growth = ((volume - pvol) / pvol) if pvol > 0 and volume >= pvol else 0.0
    rs_accel = rs - prs
    floor = max(1.0, _f(min_traded_value, 1))
    value_ratio = value / floor

    score = 28.0
    reasons: list[str] = []
    # Participation matters, but does not require a large price jump.
    if value_ratio >= 4: score += 16; reasons.append("قيمة تداول مرتفعة مبكرًا")
    elif value_ratio >= 2: score += 12
    elif value_ratio >= 0.75: score += 7
    if value_growth >= .70: score += 20; reasons.append("تسارع قوي في القيمة المتداولة بين الفحوصات")
    elif value_growth >= .35: score += 14; reasons.append("القيمة المتداولة تتسارع")
    elif value_growth >= .15: score += 8
    if volume_growth >= .60: score += 15; reasons.append("تسارع قوي في الحجم")
    elif volume_growth >= .25: score += 9
    if rs_accel >= .50: score += 14; reasons.append("القوة النسبية أمام TASI تتسارع")
    elif rs_accel >= .20: score += 8
    if rs >= 1.25: score += 10
    elif rs >= .40: score += 6
    elif rs < -.50: score -= 8
    # Small positive moves are ideal; large moves do not receive a discovery bonus.
    if .20 <= change <= 1.80: score += 8; reasons.append("الحركة ما زالت مبكرة وغير ممتدة")
    elif change > 5.0: score -= 10; reasons.append("الحركة أصبحت متأخرة نسبيًا للصيد المبكر")
    elif change < -1.5: score -= 10

    snapshot = {"value": value, "volume": volume, "change": change, "rs": rs,
                "value_growth": value_growth, "volume_growth": volume_growth, "rs_accel": rs_accel}
    return _clamp(score), reasons, snapshot


def _market_score(market: dict) -> float:
    change = _f((market or {}).get("change_percent"), 0)
    score = 50 + max(-12, min(12, change * 5.0))
    adv = _f((market or {}).get("advancers"), 0); dec = _f((market or {}).get("decliners"), 0)
    if bool((market or {}).get("breadth_available")) and adv + dec > 0:
        score += ((adv / (adv + dec)) - .5) * 20
    return _clamp(score)


def _liquidity_map(features: dict, quote) -> dict:
    price = _f(getattr(quote, "price", 0), 0)
    support = _f(features.get("support20"), 0)
    resistance = _f(features.get("resistance20"), 0)
    atr = max(_f(features.get("atr14"), 0), 1e-9)
    bid = getattr(quote, "bid", None); ask = getattr(quote, "ask", None)
    spread_pct = None
    if bid is not None and ask is not None and _f(bid) > 0 and _f(ask) >= _f(bid):
        spread_pct = (_f(ask) - _f(bid)) / ((_f(ask)+_f(bid))/2) * 100
    # Internal liquidity = nearby liquidity inside the current structure/range.
    # External liquidity = swing-boundary liquidity beyond support/resistance.
    internal_above_atr = max(0.0, (resistance - price) / atr) if resistance > price else 0.0
    internal_below_atr = max(0.0, (price - support) / atr) if support > 0 and price > support else 0.0
    external_up = resistance if resistance > 0 else None
    external_down = support if support > 0 else None
    return {
        "internal_liquidity_above_atr": round(internal_above_atr, 3),
        "internal_liquidity_below_atr": round(internal_below_atr, 3),
        "external_liquidity_up": external_up,
        "external_liquidity_down": external_down,
        "bid": bid, "ask": ask, "spread_pct": spread_pct,
        "execution_data_status": "AVAILABLE" if spread_pct is not None else "UNAVAILABLE",
    }


def evaluate_waseem30(*, features: dict, quote, market_context: dict, catalyst_context: dict,
                      leadership_score: float, persistence_score: float, min_traded_value: float,
                      local_now: datetime, liquidity_state: str = "UNKNOWN", limit_state: str = "NORMAL",
                      previous_snapshot: dict | None = None) -> Waseem30Decision:
    previous = dict(previous_snapshot or {})
    auction = extract_auction_context(quote, local_now=local_now)
    reasons: list[str] = []; blockers: list[str] = []
    price = _f(getattr(quote, "price", 0), 0); change = _f(getattr(quote, "change_percent", 0), 0)
    tasi = _f((market_context or {}).get("change_percent"), 0); rs = change - tasi
    value = _f(getattr(quote, "value", 0), 0); volume = _f(getattr(quote, "volume", 0), 0)
    atr = _f(features.get("atr14"), 0); close = _f(features.get("close"), price)
    rvol = _f(features.get("time_adjusted_rvol", features.get("relative_volume", 0)), 0)
    vol_trend = _f(features.get("volume_trend_ratio"), 1)
    vwap_ext = _f(features.get("vwap_distance_atr"), 0); ema_ext = _f(features.get("ema20_distance_atr"), 0)
    prev_value = _f(previous.get("value"), 0); prev_volume = _f(previous.get("volume"), 0); prev_rs = _f(previous.get("rs"), rs)
    prev_value_velocity = _f(previous.get("value_velocity"), 0)
    value_velocity = max(0.0, value - prev_value) if prev_value > 0 else 0.0
    volume_velocity = max(0.0, volume - prev_volume) if prev_volume > 0 else 0.0
    value_accel = value_velocity - prev_value_velocity if prev_value_velocity > 0 else 0.0
    rs_accel = rs - prev_rs

    # Data status: missing never means bad.
    data_status = {
        "price": "AVAILABLE" if price > 0 else "UNAVAILABLE",
        "intraday_bars": "AVAILABLE" if features else "UNAVAILABLE",
        "time_adjusted_rvol": "AVAILABLE" if features.get("time_adjusted_rvol") is not None else "APPROXIMATED" if features.get("relative_volume") is not None else "UNAVAILABLE",
        "bid_ask": "AVAILABLE" if getattr(quote, "bid", None) is not None and getattr(quote, "ask", None) is not None else "UNAVAILABLE",
        "auction": "AVAILABLE" if auction.available_fields else "UNAVAILABLE",
        "catalyst": "AVAILABLE" if (catalyst_context or {}).get("available") or (catalyst_context or {}).get("items") else "UNKNOWN",
        "value_acceleration": "AVAILABLE" if prev_value > 0 else "APPROXIMATED",
        "volume_acceleration": "AVAILABLE" if prev_volume > 0 else "APPROXIMATED",
    }
    essential = ["price", "intraday_bars", "time_adjusted_rvol", "value_acceleration", "volume_acceleration", "bid_ask", "catalyst"]
    completeness = sum(1.0 if data_status[k] == "AVAILABLE" else .65 if data_status[k] == "APPROXIMATED" else .35 if data_status[k] == "UNKNOWN" else 0 for k in essential) / len(essential) * 100

    # Early-score families: avoid double-counting correlated indicators.
    flow = 35.0
    floor = max(1.0, _f(min_traded_value, 1))
    if value >= floor*3: flow += 18
    elif value >= floor: flow += 10
    if rvol >= 2.0: flow += 18
    elif rvol >= 1.3: flow += 10
    if prev_value > 0 and value_velocity/prev_value >= .30: flow += 16; reasons.append("تسارع واضح في القيمة المتداولة")
    if prev_volume > 0 and volume_velocity/prev_volume >= .25: flow += 10; reasons.append("تسارع واضح في الحجم")
    if value_accel > 0: flow += 6; reasons.append("Value Velocity تتسارع بين الفحوصات")
    if vol_trend >= 1.20: flow += 6
    flow = _clamp(flow)

    relative = 45 + max(-15, min(20, rs*6)) + max(-10, min(18, rs_accel*12))
    relative = _clamp(relative)
    if rs_accel >= .3: reasons.append("القوة النسبية أمام TASI تتسارع")

    structure = 40.0
    if _f(features.get("structure_state") in {"HH_HL", "BULLISH"}, 0): structure += 15
    if _f(features.get("retest_confirmed")) >= .5: structure += 15
    if _f(features.get("is_breakout")) >= .5: structure += 12
    if _f(features.get("close_position"), .5) >= .65: structure += 8
    if _f(features.get("failed_breakout")) >= .5: structure -= 45
    structure = _clamp(structure)

    # Compression -> expansion proxy using current ATR%, candle/body and participation.
    atr_pct = (atr/close*100) if close > 0 and atr > 0 else 0
    expansion = 45.0
    if _f(features.get("candle_body_pct")) >= .55 and vol_trend >= 1.1: expansion += 20
    if _f(features.get("macd_hist_rising2")) >= .5: expansion += 10
    if _f(features.get("momentum5_pct")) > 0: expansion += 8
    if .35 <= atr_pct <= 3.5: expansion += 7
    expansion = _clamp(expansion)

    # VWAP / execution location.
    entry = 62.0
    if abs(vwap_ext) <= .65: entry += 15
    elif vwap_ext > 1.8: entry -= 22
    if abs(ema_ext) <= 1.0: entry += 10
    elif ema_ext > 2.2: entry -= 16
    if _f(features.get("retest_confirmed")) >= .5: entry += 10
    if _f(features.get("failed_breakout")) >= .5: entry -= 40
    entry = _clamp(entry)

    opening_pressure = _clamp(45 + (_f(features.get("close_position"), .5)-.5)*60 + (10 if _f(features.get("is_breakout")) >= .5 else 0))
    momentum = _clamp(45 + (10 if _f(features.get("macd_hist_rising2")) >= .5 else 0) + max(-8,min(12,_f(features.get("momentum5_pct"))*3)) + (7 if 48 <= _f(features.get("rsi14"),50) <= 70 else 0))
    vwap_behavior = _clamp(70 if abs(vwap_ext) <= .65 else 58 if abs(vwap_ext) <= 1.2 else 35)
    catalyst_raw = _f((catalyst_context or {}).get("score"), 0)
    catalyst = _clamp(50 + catalyst_raw*8)

    early_components = {
        "flow_acceleration": round(flow,2), "relative_strength_acceleration": round(relative,2),
        "opening_range_pressure": round(opening_pressure,2), "vwap_behavior": round(vwap_behavior,2),
        "compression_expansion": round(expansion,2), "momentum_acceleration": round(momentum,2),
        "catalyst": round(catalyst,2),
    }
    early_score = (flow*.34 + relative*.20 + opening_pressure*.10 + vwap_behavior*.12 + expansion*.12 + momentum*.08 + catalyst*.04)

    leader = _clamp(leadership_score*.62 + persistence_score*.20 + relative*.18)
    target = 52.0 + (18 if .6 <= atr_pct <= 5.5 else 0) + (15 if _f(features.get("resistance_distance_atr"),99) >= 1.0 else 5)
    target = _clamp(target)
    risk = 86.0
    if liquidity_state == "LOW_LIQUIDITY": risk -= 30
    if _f(features.get("failed_breakout")) >= .5: risk -= 40
    if limit_state in {"LIMIT_UP","LIMIT_DOWN"}: risk = 0
    risk = _clamp(risk)

    liquidity_map = _liquidity_map(features, quote)
    spread = liquidity_map.get("spread_pct")
    if spread is not None and spread > .8: entry = _clamp(entry-12); blockers.append(f"السبريد واسع نسبيًا ({spread:.2f}%)")

    # Move stage / anti-chase. Large change is used to *penalise lateness*, not discover it.
    if _f(features.get("failed_breakout")) >= .5:
        move_stage = "EXHAUSTION_RISK"
    elif change >= 6 or vwap_ext > 2.0 or ema_ext > 2.5 or limit_state == "NEAR_LIMIT_UP":
        move_stage = "EXTENDED"
    elif change >= 2.0 or _f(features.get("is_breakout")) >= .5:
        move_stage = "ACTIVE_MOVE"
    elif change >= .35 or early_score >= 62:
        move_stage = "EARLY_MOVE"
    else:
        move_stage = "PRE_MOVE"

    anchors = [x for x in (_f(features.get("vwap"),0), _f(features.get("ema20"),0), _f(features.get("support20"),0)) if x>0 and x <= price*1.01]
    anchor = max(anchors) if anchors else price
    if move_stage == "EXTENDED" and atr > 0:
        anchor = min(anchor, price-atr*.45)
    pullback_pct = ((price-anchor)/price*100) if price>0 and anchor>0 and anchor<price else 0
    pullback = _clamp(100-pullback_pct*18)

    # Horizon uses persistence / higher-timeframe structure, not price jump alone.
    d1_up = _f(features.get("d1_ema20_slope_pct")) > 0 and _f(features.get("d1_close")) >= _f(features.get("d1_ema20"),1e12)
    h1_up = _f(features.get("h1_ema20_slope_pct")) > 0 and _f(features.get("h1_close")) >= _f(features.get("h1_ema20"),1e12)
    if d1_up and persistence_score >= 68 and leader >= 68:
        horizon, sessions = "multi_session", 5
    elif h1_up and persistence_score >= 58:
        horizon, sessions = "two_day", 2
    else:
        horizon, sessions = "intraday", 1

    # Ranking score only; NOT the trade gate.
    total = _clamp(early_score*.28 + leader*.18 + structure*.14 + entry*.16 + target*.10 + risk*.08 + _market_score(market_context)*.06)

    hard_invalid = price <= 0 or risk < 42 or target < 45 or _f(features.get("failed_breakout")) >= .5
    if price <= 0: blockers.append("السعر غير صالح")
    if liquidity_state == "LOW_LIQUIDITY": blockers.append("سيولة التنفيذ الفعلية منخفضة")
    if target < 45: blockers.append("Target Feasibility منخفضة")
    if _f(features.get("failed_breakout")) >= .5: blockers.append("اختراق فاشل يلغي الفكرة")

    core_ready = (
        flow >= 58 and leader >= 62 and structure >= 55 and entry >= 62 and target >= 58 and risk >= 60
        and early_score >= 60 and move_stage in {"EARLY_MOVE","ACTIVE_MOVE"}
        and liquidity_state != "LOW_LIQUIDITY" and not hard_invalid
    )
    if auction.session == "OPENING_AUCTION":
        state = "EARLY_RADAR"; blockers.append("مزاد الافتتاح: مراقبة فقط حتى بدء التداول المستمر")
    elif hard_invalid:
        state = "INVALIDATED"
    elif move_stage == "EXTENDED":
        state = "WAIT_PULLBACK"; blockers.append("السعر ممدود عن منطقة التنفيذ؛ انتظار Pullback بدل المطاردة")
    elif core_ready:
        state = "TRADE_READY"
    elif early_score >= 72 and structure >= 58:
        state = "SETUP"; blockers.append("الفرصة قوية مبكرًا لكن شروط التنفيذ لم تكتمل بعد")
    elif early_score >= 62 or flow >= 70 or relative >= 68:
        state = "BUILDING"; blockers.append("الحركة تتطور وتحتاج تأكيد بنية/دخول قبل الصفقة")
    else:
        state = "EARLY_RADAR"; blockers.append("نشاط مبكر تحت المراقبة ولم يكتمل Setup بعد")

    entry_type = "NONE"
    if state == "TRADE_READY":
        entry_type = "EARLY_MOMENTUM" if move_stage == "EARLY_MOVE" and abs(vwap_ext) <= 1.0 else "PULLBACK"
    elif state == "WAIT_PULLBACK":
        entry_type = "PULLBACK"

    if data_status["bid_ask"] != "AVAILABLE": reasons.append("Bid/Ask غير متاح — لم يُعامل كإشارة سلبية")
    if auction.session == "CONTINUOUS" and data_status["auction"] != "AVAILABLE": reasons.append("بيانات المزاد غير متاحة — خارج Gate التداول المستمر")
    if data_status["catalyst"] != "AVAILABLE" and abs(change) >= 1.5: reasons.append("UNEXPLAINED_ACTIVITY — نشاط دون محفز مؤكد")

    snapshot = {
        "value": value, "volume": volume, "change": change, "rs": rs,
        "value_velocity": value_velocity, "volume_velocity": volume_velocity,
        "value_acceleration": value_accel, "rs_acceleration": rs_accel,
        "early_score": early_score, "state": state, "move_stage": move_stage,
        "price": price, "decision_time": local_now.isoformat(),
    }
    return Waseem30Decision(
        state=state, move_stage=move_stage, entry_type=entry_type, horizon=horizon, horizon_sessions=sessions,
        total_score=round(total,2), early_score=round(_clamp(early_score),2), market_score=round(_market_score(market_context),2),
        money_flow_score=round(flow,2), leadership_score=round(leader,2), catalyst_score=round(catalyst,2),
        structure_score=round(structure,2), entry_score=round(entry,2), target_feasibility_score=round(target,2),
        risk_score=round(risk,2), pullback_score=round(pullback,2), entry_anchor=anchor,
        data_completeness_score=round(completeness,2), data_status=data_status, liquidity_map=liquidity_map,
        early_components=early_components, reasons=reasons, blockers=list(dict.fromkeys(blockers)), auction=auction,
        snapshot=snapshot,
    )
