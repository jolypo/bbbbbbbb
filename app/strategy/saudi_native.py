from __future__ import annotations

from dataclasses import dataclass, asdict


@dataclass
class SaudiNativeDecision:
    state: str
    total_score: float
    market_score: float
    money_flow_score: float
    leadership_score: float
    catalyst_score: float
    structure_score: float
    entry_score: float
    target_feasibility_score: float
    risk_score: float
    horizon: str
    horizon_sessions: int
    reasons: list[str]
    blockers: list[str]

    def to_dict(self):
        return asdict(self)


def _clamp(v, lo=0.0, hi=100.0):
    return max(lo, min(hi, float(v or 0.0)))


def _feature(features: dict, key: str, default=0.0):
    try:
        return float((features or {}).get(key, default) or default)
    except (TypeError, ValueError):
        return float(default)


def _market_score(market_context: dict):
    change = float((market_context or {}).get("change_percent", 0) or 0)
    breadth_available = bool((market_context or {}).get("breadth_available"))
    adv = float((market_context or {}).get("advancers", 0) or 0)
    dec = float((market_context or {}).get("decliners", 0) or 0)
    score = 50.0 + max(-15.0, min(15.0, change * 7.0))
    if breadth_available and adv + dec > 0:
        ratio = adv / (adv + dec)
        score += (ratio - 0.5) * 30.0
    return _clamp(score)


def _money_flow_score(features: dict, quote, min_traded_value: float):
    value = float(getattr(quote, "value", 0) or 0)
    rvol = _feature(features, "time_adjusted_rvol", _feature(features, "relative_volume", 0))
    vol_trend = _feature(features, "volume_trend_ratio", 1.0)
    obv = _feature(features, "obv_slope5", 0)
    ad = _feature(features, "ad_slope5", 0)
    score = 25.0
    if value >= max(1.0, min_traded_value * 5): score += 28
    elif value >= max(1.0, min_traded_value * 2): score += 22
    elif value >= max(1.0, min_traded_value): score += 15
    elif value >= max(1.0, min_traded_value * 0.5): score += 8
    if rvol >= 2.0: score += 22
    elif rvol >= 1.5: score += 16
    elif rvol >= 1.1: score += 9
    elif rvol < 0.6: score -= 8
    if vol_trend >= 1.20: score += 10
    elif vol_trend >= 1.05: score += 5
    if obv > 0: score += 5
    if ad > 0: score += 5
    return _clamp(score)


def _structure_score(features: dict, horizon: str):
    score = 35.0
    if _feature(features, "is_breakout", 0) >= 0.5: score += 20
    if _feature(features, "retest_confirmed", 0) >= 0.5: score += 15
    if _feature(features, "close_position", 0.5) >= 0.70: score += 10
    if _feature(features, "ema20_slope_pct", 0) > 0: score += 7
    if _feature(features, "macd_hist", 0) > 0: score += 5
    if str((features or {}).get("structure_state", "")) in {"HH_HL", "BULLISH"}: score += 10
    if _feature(features, "failed_breakout", 0) >= 0.5: score -= 35
    if horizon != "intraday":
        if _feature(features, "d1_close", 0) >= _feature(features, "d1_ema20", 10**9): score += 5
        if _feature(features, "d1_ema20_slope_pct", 0) > 0: score += 5
    return _clamp(score)


def _entry_score(features: dict, existing_entry_score: float, limit_state: str):
    score = float(existing_entry_score or 0)
    vwap_ext = abs(_feature(features, "vwap_distance_atr", 0))
    ema_ext = abs(_feature(features, "ema20_distance_atr", 0))
    if vwap_ext <= 0.8: score += 8
    elif vwap_ext > 2.2: score -= 20
    if ema_ext <= 1.2: score += 5
    elif ema_ext > 2.8: score -= 15
    if limit_state == "NEAR_LIMIT_UP": score -= 18
    elif limit_state in {"LIMIT_UP", "LIMIT_DOWN"}: score = 0
    return _clamp(score)


def _target_feasibility(features: dict, horizon: str):
    atr = _feature(features, "atr14", 0)
    close = _feature(features, "close", 0)
    resistance_atr = _feature(features, "resistance_distance_atr", 99)
    score = 55.0
    atr_pct = (atr / close * 100.0) if close > 0 and atr > 0 else 0.0
    if 0.7 <= atr_pct <= 4.5: score += 15
    elif atr_pct == 0: score -= 20
    if resistance_atr >= 1.0: score += 15
    elif resistance_atr >= 0.35: score += 8
    elif 0 < resistance_atr < 0.20: score -= 20
    if horizon == "two_day": score += 3
    elif horizon == "multi_session": score += 5
    return _clamp(score)


def _risk_score(features: dict, liquidity_state: str, limit_state: str):
    score = 85.0
    if liquidity_state == "LOW_LIQUIDITY": score -= 35
    if _feature(features, "failed_breakout", 0) >= 0.5: score -= 35
    if _feature(features, "price_volume_divergence", 0) >= 0.5: score -= 15
    if limit_state == "NEAR_LIMIT_UP": score -= 12
    if limit_state in {"LIMIT_UP", "LIMIT_DOWN"}: score = 0
    return _clamp(score)


def evaluate_saudi_opportunity(*, horizon: str, features: dict, quote, market_context: dict,
                               min_traded_value: float, leadership_score: float,
                               entry_quality_score: float, persistence_score: float,
                               catalyst_context: dict, liquidity_state: str,
                               limit_state: str, judge_blockers=None):
    """Saudi-native final decision layer.

    Discovery/leadership and trade execution are intentionally separated.  Legacy
    indicators remain evidence, but TRADE_READY is decided from money-flow,
    leadership, structure, entry quality, target feasibility and risk.
    """
    horizon = str(horizon or "intraday")
    if horizon not in {"intraday", "two_day", "multi_session"}:
        horizon = "intraday"
    sessions = 1 if horizon == "intraday" else 2 if horizon == "two_day" else 5
    reasons: list[str] = []
    blockers: list[str] = []

    mkt = _market_score(market_context)
    flow = _money_flow_score(features, quote, min_traded_value)
    leader = _clamp(float(leadership_score or 0) * 0.75 + float(persistence_score or 0) * 0.25)
    cat_raw = float((catalyst_context or {}).get("score", 0) or 0)
    catalyst = _clamp(50.0 + cat_raw * 8.0)
    structure = _structure_score(features, horizon)
    entry = _entry_score(features, entry_quality_score, limit_state)
    target = _target_feasibility(features, horizon)
    risk = _risk_score(features, liquidity_state, limit_state)

    # Horizon-specific weights. Intraday prioritises entry; multi-session shifts
    # weight to persistence/structure/catalyst while keeping execution quality.
    if horizon == "intraday":
        weights = (0.08, 0.22, 0.22, 0.06, 0.16, 0.16, 0.06, 0.04)
        threshold = 70.0
    elif horizon == "two_day":
        weights = (0.08, 0.20, 0.23, 0.09, 0.17, 0.10, 0.08, 0.05)
        threshold = 71.0
    else:
        weights = (0.07, 0.17, 0.24, 0.12, 0.19, 0.08, 0.09, 0.04)
        threshold = 72.0
    comps = (mkt, flow, leader, catalyst, structure, entry, target, risk)
    total = sum(a*b for a,b in zip(comps, weights))

    if float(getattr(quote, "price", 0) or 0) <= 0:
        blockers.append("السعر غير صالح")
    if limit_state in {"LIMIT_UP", "LIMIT_DOWN"}:
        blockers.append("لا توجد منطقة دخول قابلة للتنفيذ عند الحد السعري")
    if liquidity_state == "LOW_LIQUIDITY":
        blockers.append("سيولة التنفيذ أقل من الحد؛ يبقى السهم في الرادار ولا يصبح صفقة")
    if _feature(features, "failed_breakout", 0) >= 0.5:
        blockers.append("اختراق فاشل واضح")
    if target < 45:
        blockers.append("الهدف غير واقعي بما يكفي ضمن الأفق الحالي")

    if flow >= 70: reasons.append("تدفق المال/المشاركة أعلى بوضوح من المعتاد")
    if leader >= 70: reasons.append("قيادة وقوة نسبية مستمرة مقابل السوق")
    if structure >= 70: reasons.append("البنية السعرية تؤكد استمرار الحركة")
    if catalyst >= 60: reasons.append("Catalyst داعم للحركة")
    elif not (catalyst_context or {}).get("available") and leader >= 70 and flow >= 65:
        reasons.append("حركة غير طبيعية قوية بدون Catalyst عام موثق حتى الآن")
    if entry >= 65: reasons.append("منطقة الدخول ما زالت قابلة للتنفيذ دون مطاردة كبيرة")
    if target >= 65: reasons.append("الهدف قابل للتحقق إحصائيًا/فنيًا ضمن الأفق")

    severe_extension = abs(_feature(features, "vwap_distance_atr", 0)) > 2.2 or abs(_feature(features, "ema20_distance_atr", 0)) > 2.8
    if limit_state == "NEAR_LIMIT_UP" or severe_extension:
        state = "NO_CHASE" if leader >= 60 else "WAIT_PULLBACK"
    elif blockers:
        state = "LEADER" if leader >= 70 or flow >= 75 else "SETUP" if total >= 60 else "RADAR"
    elif total >= threshold and leader >= 58 and flow >= 50 and structure >= 55 and entry >= 50 and target >= 50 and risk >= 55:
        state = "TRADE_READY"
    elif leader >= 72 and entry < 50:
        state = "WAIT_PULLBACK"
    elif total >= threshold - 4 and structure >= 55:
        state = "SETUP"
    elif leader >= 65 or flow >= 70:
        state = "LEADER"
    else:
        state = "RADAR"

    return SaudiNativeDecision(
        state=state,
        total_score=round(_clamp(total), 2), market_score=round(mkt, 2),
        money_flow_score=round(flow, 2), leadership_score=round(leader, 2),
        catalyst_score=round(catalyst, 2), structure_score=round(structure, 2),
        entry_score=round(entry, 2), target_feasibility_score=round(target, 2),
        risk_score=round(risk, 2), horizon=horizon, horizon_sessions=sessions,
        reasons=reasons, blockers=blockers,
    )
