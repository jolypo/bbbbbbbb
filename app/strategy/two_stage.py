from __future__ import annotations
from dataclasses import dataclass, asdict


@dataclass
class HunterDecision:
    decision: str
    score: float
    reasons: list[str]
    warnings: list[str]
    features: dict
    grade: str
    leadership_score: float = 0.0
    entry_quality_score: float = 0.0
    persistence_score: float = 0.0
    horizon: str = "intraday"
    def to_dict(self): return asdict(self)

@dataclass
class JudgeDecision:
    decision: str
    score: float
    required_score: float
    base_required_score: float
    market_state: str
    liquidity_state: str
    volatility_state: str
    data_quality: str
    learning_adjustment: float
    reasons: list[str]
    blockers: list[str]
    confirmed_setup: str
    leadership_score: float = 0.0
    entry_quality_score: float = 0.0
    persistence_score: float = 0.0
    catalyst_score: float = 0.0
    limit_state: str = "NORMAL"
    momentum_decay: float = 0.0
    horizon: str = "intraday"
    def to_dict(self): return asdict(self)


def _build_hunter(assessment, *, leadership_score=0.0, entry_quality_score=0.0, persistence_score=0.0, horizon="intraday") -> HunterDecision:
    if assessment is None:
        return HunterDecision("REJECT", 0.0, [], ["التحليل الفني غير مكتمل"], {}, "REJECT",
                              leadership_score, entry_quality_score, persistence_score, horizon)
    warnings=list(getattr(assessment, "hard_rejects", []) or [])
    # Hunter is a discovery/ranking layer, never a publication gate.
    decision = "BUY_CANDIDATE" if float(assessment.score) >= 70 else "WATCH_CANDIDATE"
    return HunterDecision(decision, float(assessment.score), list(assessment.reasons), warnings,
                          dict(assessment.features), getattr(assessment,"grade","B"),
                          float(leadership_score or 0), float(entry_quality_score or 0),
                          float(persistence_score or 0), str(horizon))


def build_intraday_hunter(assessment, **kwargs) -> HunterDecision:
    return _build_hunter(assessment, horizon="intraday", **kwargs)


def build_multi_session_hunter(assessment, **kwargs) -> HunterDecision:
    return _build_hunter(assessment, horizon="multi_session", **kwargs)


def build_hunter(assessment) -> HunterDecision:
    # Backward-compatible alias used by older tests/callers.
    return build_intraday_hunter(assessment)


def judge(hunter: HunterDecision, market_quality, *, traded_value=0.0, min_traded_value=0.0,
          sector_strength_available=False, sector_strength_pct=0.0, sector_strength_breadth=0.5, learning_stats=None,
          sector_exposure_count=0, bid=None, ask=None, stock_change_pct=0.0,
          market_change_pct=0.0, leadership_score=None, entry_quality_score=None,
          persistence_score=None, catalyst_context=None, limit_state="NORMAL",
          momentum_decay=0.0, horizon="intraday") -> JudgeDecision:
    f=hunter.features
    blockers=[]; reasons=[]
    # Only true safety gates arrive as Hunter warnings. ADX/DI/RVOL weakness
    # is scored diagnostically in the Saudi calibration, not duplicated here.
    blockers.extend(list(hunter.warnings or []))
    learning_stats=learning_stats or {}
    adj=float(learning_stats.get("adjustment",0) or 0)
    leadership = float(hunter.leadership_score if leadership_score is None else leadership_score or 0.0)
    entry_quality = float(hunter.entry_quality_score if entry_quality_score is None else entry_quality_score or 0.0)
    persistence = float(hunter.persistence_score if persistence_score is None else persistence_score or 0.0)
    catalyst_context = catalyst_context or {}
    catalyst_score = float(catalyst_context.get("score", 0.0) or 0.0)
    horizon = str(horizon or hunter.horizon or "intraday")
    limit_state = str(limit_state or "NORMAL")
    momentum_decay = float(momentum_decay or 0.0)

    if leadership_score is not None:
        reasons.append(f"Leadership Score={leadership:.1f}/100")
    if persistence_score is not None:
        reasons.append(f"Leadership Persistence={persistence:.1f}/100")
    if entry_quality_score is not None:
        reasons.append(f"Entry Quality={entry_quality:.1f}/100")
    if catalyst_context.get("available"):
        reasons.append(f"Catalyst {catalyst_context.get('impact','MEDIUM')} score={catalyst_score:+.1f}")
    if momentum_decay >= 1.5:
        reasons.append(f"Momentum Decay: فقد السهم {momentum_decay:.2f} نقطة قوة نسبية من قمته")

    if limit_state == "LIMIT_UP":
        blockers.append("NO_EXECUTABLE_ENTRY: السهم عند الحد الأعلى؛ Leadership قوي لكن لا توجد مساحة دخول جديدة")
    elif limit_state == "LIMIT_DOWN":
        blockers.append("السهم عند الحد الأدنى؛ لا شراء جديد")
    elif limit_state == "NEAR_LIMIT_UP":
        reasons.append("السهم قريب من الحد الأعلى؛ يلزم R/R ومساحة حركة استثنائية")

    # Liquidity is a hard gate, not just score decoration.
    rv=float(f.get("time_adjusted_rvol", f.get("relative_volume",0)) or 0)
    # Liquidity != RVOL. Execution liquidity is proven primarily by traded value.
    # RVOL describes participation and adjusts quality, but a liquid Saudi name
    # is not rejected merely because today's relative volume is below 1.10.
    if traded_value >= max(1.0, min_traded_value * 3.0):
        liquidity="HIGH_LIQUIDITY"; reasons.append("القيمة المتداولة تؤكد سيولة تنفيذية مرتفعة")
    elif traded_value >= max(1.0,min_traded_value):
        liquidity="NORMAL_LIQUIDITY"; reasons.append("القيمة المتداولة تؤكد سيولة تنفيذية مقبولة")
    else:
        liquidity="LOW_LIQUIDITY"; blockers.append(
            f"القيمة المتداولة غير كافية لإثبات سيولة التنفيذ ({traded_value:,.0f} < {min_traded_value:,.0f} ر.س)"
        )
    if rv >= 1.5: reasons.append(f"نشاط الحجم النسبي قوي RVOL={rv:.2f}×")
    elif rv < 0.75: reasons.append(f"تنبيه: المشاركة الحالية دون المعتاد RVOL={rv:.2f}×")

    # Confirmed setup: breakout+hold OR healthy approach with room. Retest is only
    # marked confirmed when price revisited the prior resistance and closed above it.
    breakout = f.get("is_breakout",0) >= .5
    retest = f.get("retest_confirmed",0) >= .5
    hold = breakout and f.get("close_position",0) >= .65 and f.get("failed_breakout",0) < .5
    structure = str(f.get("structure_state", "UNKNOWN"))
    if retest:
        confirmed="RETEST_CONFIRMED"; reasons.append("إعادة الاختبار ثبتت أعلى المقاومة")
    elif hold:
        confirmed="BREAKOUT_HOLD_CONFIRMED"; reasons.append("الاختراق ثبت بإغلاق قوي")
    elif not breakout and f.get("resistance_distance_atr",99) >= .25 and structure in {"HH_HL","BULLISH"}:
        confirmed="STRUCTURE_CONFIRMED"; reasons.append("هيكل HH/HL مع مساحة حركة قبل المقاومة")
    else:
        confirmed="WAITING_CONFIRMATION"; blockers.append("الـSetup لم يحصل على Breakout/Hold/Retest أو Structure كافٍ")

    # Do not duplicate Hunter hard gates at stricter thresholds. Moderate
    # extension/divergence is diagnostic in TASI; only the analyzer's severe
    # trap/overextension rules arrive through hunter.warnings as hard blockers.
    if f.get("failed_breakout",0) >= .5 and not any("اختراق فاشل" in x for x in blockers):
        blockers.append("اختراق فاشل")
    vwap_ext=float(f.get("vwap_distance_atr",0) or 0); ema_ext=float(f.get("ema20_distance_atr",0) or 0)
    if vwap_ext > 1.5 or ema_ext > 2.0:
        reasons.append("تنبيه: السعر ممتد نسبيًا؛ لا تتم مطاردته خارج منطقة الدخول")
    if f.get("price_volume_divergence",0) >= .5:
        reasons.append("تنبيه: تباعد سعر/حجم يحتاج تأكيدًا من الحركة والسعر")
    adx=float(f.get("adx14",0) or 0); di=float(f.get("di_spread",0) or 0)
    if adx < 18: reasons.append(f"تنبيه: قوة الاتجاه ما زالت مبكرة ADX={adx:.1f}")
    if di < 0: reasons.append("تنبيه: -DI ما زال متفوقًا؛ يلزم تأكيد سعري أقوى")
    if bid is not None and ask is not None:
        try:
            bid=float(bid); ask=float(ask)
            if bid>0 and ask>=bid:
                spread_pct=(ask-bid)/((ask+bid)/2.0)*100.0
                if spread_pct > 0.80:
                    blockers.append(f"Bid/Ask واسع ({spread_pct:.2f}%)")
                elif spread_pct <= 0.30:
                    reasons.append(f"Bid/Ask ضيق ({spread_pct:.2f}%)")
        except Exception:
            pass
    if int(sector_exposure_count or 0) >= 2:
        blockers.append("تعرض قطاعي مرتفع: توجد صفقتان أو أكثر في نفس القطاع")
    elif int(sector_exposure_count or 0) == 1:
        reasons.append("يوجد تعرض قائم لنفس القطاع؛ تم رفع شرط الجودة")

    if market_quality.state == "NO_TRADE": blockers.append("جودة السوق تمنع التداول")
    sector_breadth = float(sector_strength_breadth if sector_strength_breadth is not None else 0.5)
    if sector_strength_available and float(sector_strength_pct) <= -2.0 and sector_breadth < 0.35:
        blockers.append("القطاع ضعيف على نطاق واسع؛ أغلب مكوناته تحت ضغط")
    elif sector_strength_available and float(sector_strength_pct) < 0:
        reasons.append("القطاع أضعف من السوق؛ رُفع شرط الجودة")

    # Saudi relative-strength overlay: a stock that materially outperforms TASI
    # may be tradable even while the broad index is mildly/medium-term weak.
    stock_change = float(stock_change_pct or 0.0)
    market_change = float(market_change_pct or 0.0)
    relative_strength = stock_change - market_change
    rs_bonus = 0.0
    if relative_strength >= 4.0:
        rs_bonus = 8.0; reasons.append(f"قوة نسبية استثنائية مقابل TASI (+{relative_strength:.2f} نقطة)")
    elif relative_strength >= 2.5:
        rs_bonus = 6.0; reasons.append(f"قوة نسبية قوية مقابل TASI (+{relative_strength:.2f} نقطة)")
    elif relative_strength >= 1.5:
        rs_bonus = 4.0; reasons.append(f"السهم متفوق بوضوح على TASI (+{relative_strength:.2f} نقطة)")
    elif relative_strength >= 0.75:
        rs_bonus = 2.0; reasons.append(f"السهم أقوى من TASI (+{relative_strength:.2f} نقطة)")

    # In a genuinely broad bearish session, ordinary longs are rejected, but an
    # exceptional Saudi stock may still be considered when it is materially
    # outperforming the index and has a confirmed setup. This avoids vetoing
    # +4/+5% leaders merely because TASI itself is red.
    severe_bear_exception = (
        market_quality.state == "BEAR_TREND"
        and relative_strength >= 3.0
        and stock_change >= 1.5
        and confirmed != "WAITING_CONFIRMATION"
        and liquidity != "LOW_LIQUIDITY"
        and not (sector_strength_available and float(sector_strength_pct) <= -2.0 and sector_breadth < 0.35)
    )
    if market_quality.state == "BEAR_TREND":
        if severe_bear_exception:
            reasons.append("استثناء قوة نسبية: السهم قائد رغم ضغط TASI الواسع")
        else:
            blockers.append("ضغط TASI واسع وقوي ولا توجد قوة نسبية/بنية كافية للاستثناء")

    setup_bonus = 0.0
    if confirmed == "BREAKOUT_HOLD_CONFIRMED": setup_bonus = 6.0
    elif confirmed == "RETEST_CONFIRMED": setup_bonus = 5.0
    elif confirmed == "STRUCTURE_CONFIRMED": setup_bonus = 4.0
    liquidity_bonus = 2.0 if liquidity == "HIGH_LIQUIDITY" else 0.0
    participation_bonus = 2.0 if rv >= 1.5 else 0.0

    overlay = 0.0
    if leadership_score is not None:
        overlay += max(-4.0, min(4.0, (leadership - 50.0) * 0.08))
    if entry_quality_score is not None:
        overlay += max(-6.0, min(6.0, (entry_quality - 50.0) * 0.12))
    if persistence_score is not None:
        overlay += max(-4.0, min(4.0, (persistence - 50.0) * 0.08))
    catalyst_weight = 0.90 if horizon == "multi_session" else 0.60
    overlay += max(-4.0 if horizon == "multi_session" else -3.0,
                   min(4.0 if horizon == "multi_session" else 3.0, catalyst_score * catalyst_weight))
    if momentum_decay >= 3.0:
        overlay -= 5.0
    elif momentum_decay >= 1.5:
        overlay -= 2.5
    score=max(0.0,min(100.0,hunter.score + adj + rs_bonus + setup_bonus + liquidity_bonus + participation_bonus + overlay))
    required=float(market_quality.required_score)
    if severe_bear_exception:
        required = min(required, 72.0)
    if sector_strength_available and sector_strength_pct < 0: required=min(99.0, required+1)
    if int(sector_exposure_count or 0) == 1: required=min(99.0, required+2)
    if liquidity == "LOW_LIQUIDITY": required=99.0
    if limit_state == "NEAR_LIMIT_UP": required=min(99.0, required+4)
    if entry_quality_score is not None and entry_quality < 35:
        blockers.append("جودة الدخول الحالية منخفضة جدًا؛ السهم قد يكون قائدًا لكن نقطة الدخول غير صالحة")
    if horizon == "multi_session":
        # Multi-session is allowed to tolerate a less-perfect intraday candle, but
        # requires a slightly stronger overall thesis because of overnight gap risk.
        required=min(99.0, required+2)

    if blockers:
        # Confirmation-only issues are WAIT; hard quality/liquidity/data issues reject.
        hard = bool(hunter.warnings) or any(x for x in blockers if any(k in x for k in ["السيولة","القيمة المتداولة","جودة السوق","ضغط TASI واسع","اختراق فاشل","تباعد","القطاع ضعيف","تعرض قطاعي","Bid/Ask واسع","NO_EXECUTABLE_ENTRY","الحد الأدنى","جودة الدخول الحالية"]))
        decision="REJECT" if hard else "WAIT"
    elif score >= required:
        decision="APPROVE"
    else:
        decision="WAIT"
        reasons.append(f"الدرجة {score:.1f} أقل من المطلوب {required:.1f}")

    return JudgeDecision(decision, round(score,2), round(required,2), 68.0, market_quality.state,
                         liquidity, market_quality.volatility_state, market_quality.data_quality,
                         round(adj,2), reasons + list(market_quality.reasons), blockers, confirmed,
                         round(leadership,2), round(entry_quality,2), round(persistence,2),
                         round(catalyst_score,2), limit_state, round(momentum_decay,2), horizon)
