from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any


@dataclass
class AuctionContext:
    session: str
    indicative_price: float | None
    indicative_change_pct: float | None
    indicative_volume: float | None
    imbalance: float | None
    best_bid: float | None
    best_ask: float | None
    available_fields: list[str]
    unavailable_fields: list[str]

    def to_dict(self):
        return asdict(self)


@dataclass
class Waseem20Decision:
    state: str
    horizon: str
    horizon_sessions: int
    total_score: float
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
    reasons: list[str]
    blockers: list[str]
    auction: AuctionContext

    def to_dict(self):
        return asdict(self)


def _f(v: Any, default=0.0) -> float:
    try:
        return float(v if v is not None else default)
    except (TypeError, ValueError):
        return float(default)


def _clamp(v: float, lo=0.0, hi=100.0) -> float:
    return max(lo, min(hi, float(v)))


def _raw_first(raw: dict, keys: tuple[str, ...]) -> float | None:
    for key in keys:
        if key in raw and raw.get(key) not in (None, ""):
            try:
                return float(raw.get(key))
            except (TypeError, ValueError):
                continue
    return None


def extract_auction_context(quote, *, local_now: datetime | None = None) -> AuctionContext:
    """Best-effort pre-open auction extraction.

    Providers currently do not promise auction-specific fields. We only use
    fields that are explicitly present in the provider payload and report every
    missing auction datum instead of inferring/fabricating it.
    """
    raw = dict(getattr(quote, "raw", None) or {})
    indicative = _raw_first(raw, (
        "indicative_price", "indicativePrice", "auction_price", "auctionPrice",
        "theoretical_open_price", "theoreticalOpenPrice", "equilibrium_price",
    ))
    indic_change = _raw_first(raw, (
        "indicative_change_pct", "indicativeChangePct", "auction_change_pct",
        "auctionChangePercent", "theoretical_change_pct",
    ))
    indic_volume = _raw_first(raw, (
        "indicative_volume", "indicativeVolume", "auction_volume", "auctionVolume",
        "matched_volume", "matchedVolume",
    ))
    imbalance = _raw_first(raw, (
        "auction_imbalance", "auctionImbalance", "imbalance", "order_imbalance",
    ))
    bid = getattr(quote, "bid", None)
    ask = getattr(quote, "ask", None)

    fields = {
        "سعر المزاد الاسترشادي": indicative,
        "تغير المزاد الاسترشادي": indic_change,
        "الكمية/الحجم المتطابق في المزاد": indic_volume,
        "اختلال الطلب/العرض في المزاد": imbalance,
        "أفضل طلب": bid,
        "أفضل عرض": ask,
    }
    available = [k for k, v in fields.items() if v is not None]
    unavailable = [k for k, v in fields.items() if v is None]

    session = "CONTINUOUS"
    if local_now is not None:
        mins = local_now.hour * 60 + local_now.minute
        if 9 * 60 + 30 <= mins < 10 * 60:
            session = "OPENING_AUCTION"
        elif 15 * 60 <= mins < 15 * 60 + 10:
            session = "CLOSING_AUCTION"
        elif 15 * 60 + 10 <= mins < 15 * 60 + 20:
            session = "TRADE_AT_LAST"
        elif mins < 9 * 60 + 30:
            session = "PRE_AUCTION"
        elif mins >= 15 * 60 + 20:
            session = "CLOSED"

    return AuctionContext(
        session=session,
        indicative_price=indicative,
        indicative_change_pct=indic_change,
        indicative_volume=indic_volume,
        imbalance=imbalance,
        best_bid=_f(bid, None) if bid is not None else None,
        best_ask=_f(ask, None) if ask is not None else None,
        available_fields=available,
        unavailable_fields=unavailable,
    )


def _market_score(market: dict) -> float:
    change = _f((market or {}).get("change_percent"), 0)
    score = 50 + max(-15, min(15, change * 6.0))
    adv = _f((market or {}).get("advancers"), 0)
    dec = _f((market or {}).get("decliners"), 0)
    if bool((market or {}).get("breadth_available")) and adv + dec > 0:
        score += ((adv / (adv + dec)) - 0.5) * 24
    return _clamp(score)


def _flow_score(features: dict, quote, min_traded_value: float) -> tuple[float, list[str]]:
    reasons = []
    value = _f(getattr(quote, "value", 0), 0)
    rvol = _f(features.get("time_adjusted_rvol", features.get("relative_volume", 0)), 0)
    vol_trend = _f(features.get("volume_trend_ratio"), 1)
    obv = _f(features.get("obv_slope5"), 0)
    ad = _f(features.get("ad_slope5"), 0)
    score = 28.0
    floor = max(1.0, float(min_traded_value or 1.0))
    if value >= floor * 5:
        score += 25; reasons.append("قيمة تداول قوية جدًا")
    elif value >= floor * 2:
        score += 20; reasons.append("قيمة تداول قوية")
    elif value >= floor:
        score += 14
    elif value >= floor * .35:
        score += 8
    if rvol >= 2.5:
        score += 27; reasons.append(f"RVOL استثنائي {rvol:.2f}x")
    elif rvol >= 1.7:
        score += 20; reasons.append(f"RVOL قوي {rvol:.2f}x")
    elif rvol >= 1.15:
        score += 11
    elif 0 < rvol < .55:
        score -= 8
    if vol_trend >= 1.25:
        score += 10; reasons.append("تسارع في الحجم")
    elif vol_trend >= 1.08:
        score += 5
    if obv > 0: score += 5
    if ad > 0: score += 5
    return _clamp(score), reasons


def _structure_score(features: dict) -> tuple[float, list[str]]:
    r = []
    s = 38.0
    if _f(features.get("is_breakout")) >= .5:
        s += 18; r.append("اختراق فني قائم")
    if _f(features.get("retest_confirmed")) >= .5:
        s += 16; r.append("إعادة اختبار مؤكدة")
    if str(features.get("structure_state", "")) in {"HH_HL", "BULLISH"}:
        s += 12; r.append("بنية قمم وقيعان صاعدة")
    if _f(features.get("close_position"), .5) >= .70:
        s += 8
    if _f(features.get("ema20_slope_pct")) > 0:
        s += 5
    if _f(features.get("failed_breakout")) >= .5:
        s -= 38; r.append("اختراق فاشل")
    return _clamp(s), r


def _entry_and_pullback(features: dict, quote) -> tuple[float, float, float | None, list[str]]:
    price = _f(getattr(quote, "price", 0), 0)
    atr = _f(features.get("atr14"), 0)
    vwap = _f(features.get("vwap"), 0)
    ema20 = _f(features.get("ema20"), 0)
    support = _f(features.get("support20"), 0)
    vwap_ext = _f(features.get("vwap_distance_atr"), 0)
    ema_ext = _f(features.get("ema20_distance_atr"), 0)
    reasons = []

    entry_score = 58.0
    if abs(vwap_ext) <= .8: entry_score += 15
    elif vwap_ext > 2.3: entry_score -= 22
    elif vwap_ext > 1.4: entry_score -= 10
    if abs(ema_ext) <= 1.2: entry_score += 10
    elif ema_ext > 2.8: entry_score -= 15
    if _f(features.get("retest_confirmed")) >= .5: entry_score += 12
    if _f(features.get("failed_breakout")) >= .5: entry_score -= 35

    anchors = [x for x in (vwap, ema20, support) if x and x > 0 and x <= price * 1.01]
    anchor = max(anchors) if anchors else price
    if price > 0 and atr > 0:
        # Do not chase: if price is extended, deliberately plan a pullback entry.
        if vwap_ext > 1.2 or ema_ext > 1.8:
            anchor = min(price - atr * .45, anchor if anchor > 0 else price - atr * .45)
            reasons.append("الدخول المخطط على تراجع/إعادة اختبار بدل مطاردة السعر")
        else:
            anchor = min(price, max(anchor, price - atr * .25))
    if anchor and price and anchor < price:
        pullback_pct = (price - anchor) / price * 100.0
    else:
        pullback_pct = 0.0
    pullback_score = _clamp(100 - pullback_pct * 18)
    return _clamp(entry_score), pullback_score, (anchor if anchor and anchor > 0 else None), reasons


def _target_score(features: dict, horizon: str) -> float:
    atr = _f(features.get("atr14"), 0)
    close = _f(features.get("close"), 0)
    resistance_atr = _f(features.get("resistance_distance_atr"), 99)
    atr_pct = (atr / close * 100) if close > 0 and atr > 0 else 0
    score = 52.0
    if .6 <= atr_pct <= 5.5: score += 18
    elif atr_pct <= 0: score -= 20
    if resistance_atr >= 1.25: score += 16
    elif resistance_atr >= .5: score += 9
    elif 0 < resistance_atr < .20: score -= 18
    if horizon == "two_day": score += 4
    if horizon == "multi_session": score += 7
    return _clamp(score)


def _choose_horizon(features: dict, leadership: float, persistence: float, catalyst_score: float) -> tuple[str, int, list[str]]:
    d1_up = _f(features.get("d1_ema20_slope_pct"), 0) > 0 and _f(features.get("d1_close"), 0) >= _f(features.get("d1_ema20"), 10**9)
    h1_up = _f(features.get("h1_ema20_slope_pct"), 0) > 0 and _f(features.get("h1_close"), 0) >= _f(features.get("h1_ema20"), 10**9)
    reasons = []
    if d1_up and persistence >= 66 and leadership >= 68:
        reasons.append("استمرارية يومية تسمح بأفق 2–5 جلسات")
        return "multi_session", 5, reasons
    if h1_up and (persistence >= 56 or catalyst_score >= 60):
        reasons.append("استمرار 60m/المحفز يدعم 1–2 جلسة")
        return "two_day", 2, reasons
    reasons.append("الأفضلية الحالية أقرب للتداول داخل الجلسة")
    return "intraday", 1, reasons


def evaluate_waseem20(*, features: dict, quote, market_context: dict, catalyst_context: dict,
                      leadership_score: float, persistence_score: float,
                      min_traded_value: float, local_now: datetime,
                      liquidity_state: str = "UNKNOWN", limit_state: str = "NORMAL") -> Waseem20Decision:
    """Unified Saudi-market opportunity engine.

    One pass chooses the best horizon (intraday / 1-2 sessions / 2-5 sessions),
    keeps WAIT ideas visible with planned pullback entry, and never invents
    auction fields that the active provider did not return.
    """
    auction = extract_auction_context(quote, local_now=local_now)
    reasons: list[str] = []
    blockers: list[str] = []

    market = _market_score(market_context)
    flow, flow_reasons = _flow_score(features, quote, min_traded_value)
    reasons.extend(flow_reasons)

    change = _f(getattr(quote, "change_percent", 0), 0)
    tasi_change = _f((market_context or {}).get("change_percent"), 0)
    rs = change - tasi_change
    leader = _clamp(leadership_score * .72 + persistence_score * .18 + max(0, min(10, rs * 2.0)))
    if rs >= 3: reasons.append(f"قوة نسبية واضحة أمام TASI (+{rs:.2f} نقطة)")

    catalyst_raw = _f((catalyst_context or {}).get("score"), 0)
    impact = str((catalyst_context or {}).get("impact", "") or "").upper()
    catalyst = _clamp(50 + catalyst_raw * 8 + (8 if impact == "HIGH" else 3 if impact == "MEDIUM" else 0))
    if (catalyst_context or {}).get("items"):
        reasons.append("يوجد محفز/إعلان معروف للمحرك")
    elif abs(change) >= 3 or rs >= 3:
        reasons.append("حركة غير طبيعية بلا محفز عام مؤكد حتى الآن")

    structure, structure_reasons = _structure_score(features)
    reasons.extend(structure_reasons)
    entry, pullback, anchor, entry_reasons = _entry_and_pullback(features, quote)
    reasons.extend(entry_reasons)

    horizon, sessions, horizon_reasons = _choose_horizon(features, leader, persistence_score, catalyst)
    reasons.extend(horizon_reasons)
    target = _target_score(features, horizon)

    risk = 84.0
    if liquidity_state == "LOW_LIQUIDITY": risk -= 28
    if _f(features.get("failed_breakout")) >= .5: risk -= 35
    if _f(features.get("price_volume_divergence")) >= .5: risk -= 15
    if limit_state == "NEAR_LIMIT_UP": risk -= 12
    if limit_state in {"LIMIT_UP", "LIMIT_DOWN"}: risk = 0
    risk = _clamp(risk)

    weights = (.06, .22, .22, .10, .16, .10, .08, .06)
    comps = (market, flow, leader, catalyst, structure, entry, target, risk)
    total = sum(c * w for c, w in zip(comps, weights))

    mins = local_now.hour * 60 + local_now.minute
    in_preopen = 9 * 60 + 30 <= mins < 10 * 60
    if in_preopen:
        # Pre-open is intelligence/planning only. No paper entry before the market opens.
        state = "WAIT"
        blockers.append("السوق في مزاد الافتتاح؛ لا تتحول الفرصة إلى TRADE_READY قبل بدء التداول المستمر")
        if not auction.available_fields:
            blockers.append("بيانات المزاد التفصيلية غير متاحة من المزود الحالي؛ تم الاعتماد على الأخبار/المحفزات وسجل السهم فقط")
    else:
        hard_bad = risk < 42 or target < 48 or _f(features.get("failed_breakout")) >= .5
        if hard_bad:
            state = "INVALIDATED" if _f(features.get("failed_breakout")) >= .5 else "WAIT"
        elif limit_state in {"LIMIT_UP", "NEAR_LIMIT_UP"}:
            state = "WAIT"
            blockers.append("السهم قريب من الحد/ممدود؛ الخطة انتظار تراجع منطقي بدل المطاردة")
        elif total >= 72 and flow >= 52 and leader >= 58 and entry >= 52 and target >= 55 and risk >= 55:
            state = "TRADE_READY"
        elif total >= 63 or leader >= 68 or flow >= 70:
            state = "WAIT"
        elif total >= 56:
            state = "LEADER"
        else:
            state = "RADAR"

    if state == "WAIT" and anchor is not None:
        reasons.append(f"منطقة انتظار مقترحة حول {anchor:.2f} إذا أكد السعر الارتداد")

    return Waseem20Decision(
        state=state,
        horizon=horizon,
        horizon_sessions=sessions,
        total_score=_clamp(total),
        market_score=market,
        money_flow_score=flow,
        leadership_score=leader,
        catalyst_score=catalyst,
        structure_score=structure,
        entry_score=entry,
        target_feasibility_score=target,
        risk_score=risk,
        pullback_score=pullback,
        entry_anchor=anchor,
        reasons=reasons,
        blockers=blockers,
        auction=auction,
    )


def build_wait_plan(features: dict, quote, decision: Waseem20Decision, *, min_rr: float = 1.8) -> dict:
    """Create a transparent non-executable plan for WAIT notifications.

    The plan is intentionally not an order. It gives the user the pullback area,
    invalidation and feasible targets to watch while the setup is still WAIT.
    """
    price = _f(getattr(quote, "price", 0), 0)
    atr = _f(features.get("atr14"), 0)
    anchor = _f(decision.entry_anchor, price)
    if price <= 0 or atr <= 0 or anchor <= 0:
        return {"available": False, "reason": "ATR/سعر غير كافٍ لبناء خطة WAIT"}
    zone = min(max(atr * 0.12, anchor * 0.002), anchor * 0.006)
    entry_low = max(0.01, anchor - zone)
    entry_high = anchor + zone
    entry = (entry_low + entry_high) / 2.0
    support = _f(features.get("support20"), 0)
    sl_by_atr = entry - atr * (0.85 if decision.horizon == "intraday" else 1.05 if decision.horizon == "two_day" else 1.20)
    sl = max(0.01, min(sl_by_atr, support * 0.995) if support > 0 and support < entry else sl_by_atr)
    risk = max(0.01, entry - sl)
    rr1 = max(float(min_rr or 1.8), 1.5)
    if decision.horizon == "intraday":
        mults = (rr1, rr1 + .65, rr1 + 1.25)
    elif decision.horizon == "two_day":
        mults = (rr1, rr1 + .9, rr1 + 1.7)
    else:
        mults = (rr1, rr1 + 1.2, rr1 + 2.2)
    return {
        "available": True,
        "entry_low": entry_low,
        "entry_high": entry_high,
        "entry": entry,
        "sl": sl,
        "tp1": entry + risk * mults[0],
        "tp2": entry + risk * mults[1],
        "tp3": entry + risk * mults[2],
        "rr_tp1": mults[0],
    }
