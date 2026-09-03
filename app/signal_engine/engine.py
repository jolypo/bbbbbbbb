from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from app.probability.engine import ProbabilityEngine
from app.risk.levels import build_long_levels


@dataclass
class Signal:
    trade_id: str; symbol: str; name: str; name_en: str; direction: str
    entry_low: float; entry_high: float; entry: float; sl: float; tp1: float; tp2: float; tp3: float; rr_tp1: float
    score: float; probability: float; probability_status: str; probability_samples: int; probability_bucket: str
    strategy: str; trade_type: str; market_regime: str; sector: str; risk_level: str; grade: str
    discovered_at: str; expected_tp1: str; expected_tp2: str; expected_tp3: str
    reasons: list[str] = field(default_factory=list)
    target_reasons: list[str] = field(default_factory=list)
    invalidation_reasons: list[str] = field(default_factory=list)
    indicators: dict = field(default_factory=dict)
    quote_updated_at: str = ""
    historical_updated_at: str = ""
    hunter_score: float = 0.0
    judge_score: float = 0.0
    required_score: float = 0.0
    judge_decision: str = "APPROVE"
    market_state: str = "NORMAL"
    liquidity_state: str = "UNKNOWN"
    volatility_state: str = "UNKNOWN"
    data_quality: str = "UNKNOWN"
    learning_adjustment: float = 0.0
    confirmed_setup: str = "UNKNOWN"
    judge_reasons: list[str] = field(default_factory=list)
    judge_blockers: list[str] = field(default_factory=list)
    trade_horizon: str = "intraday"
    leadership_score: float = 0.0
    entry_quality_score: float = 0.0
    persistence_score: float = 0.0
    catalyst_score: float = 0.0
    catalyst_impact: str = "NONE"
    limit_state: str = "NORMAL"
    momentum_decay: float = 0.0
    decision_state: str = "TRADE_READY"
    money_flow_score: float = 0.0
    structure_score: float = 0.0
    target_feasibility_score: float = 0.0
    risk_score: float = 0.0
    market_score: float = 0.0
    horizon_sessions: int = 1
    decision_time: str = ""
    data_cutoff: str = ""

    def to_dict(self): return asdict(self)


class SignalEngine:
    def __init__(self, settings, history):
        self.s=settings; self.p=ProbabilityEngine(history)

    def build_assessment_with_diagnostics(self, candidate, regime, sector, assessment, quote_updated_at="", historical_updated_at="", judge_decision=None, native_decision=None):
        if not assessment or not self.s.allow_long:
            return None, "التحليل غير مكتمل أو الشراء غير مسموح"
        if native_decision is not None:
            if getattr(native_decision, "state", "RADAR") != "TRADE_READY":
                return None, f"الحالة النهائية {getattr(native_decision, 'state', 'RADAR')} وليست TRADE_READY"
        elif judge_decision is not None and getattr(judge_decision, "decision", "REJECT") != "APPROVE":
            return None, "Judge لم يوافق"
        f=assessment.features
        price=float(candidate.quote.price)

        # Execution liquidity has already been validated by Judge using the
        # session-progress-adjusted Saudi traded-value floor. Do not apply a
        # second static full-day threshold here; that previously re-rejected
        # valid morning setups after Judge had approved them.

        atr=float(f.get("atr14") or 0)
        support=f.get("support20")
        if price<=0 or atr<=0:return None, "ATR أو السعر غير صالح لبناء المستويات"
        # Entry zone normally stays near the current delayed quote. WASEEM 20 can
        # deliberately plan a pullback/retest anchor so a strong Saudi leader is
        # not chased after an extended move.
        entry_anchor = float(f.get("waseem_entry_anchor") or 0) if str(getattr(assessment, "strategy", "")) == "WASEEM20_UNIFIED" else 0.0
        anchor = entry_anchor if entry_anchor > 0 else price
        zone=min(max(atr*0.12, anchor*0.002), anchor*0.006)
        levels=build_long_levels(anchor-zone, anchor+zone, atr, support, self.s.min_rr, assessment.trade_type)
        if not levels:return None, "تعذر بناء Entry/SL/Targets مع R/R المطلوب"
        prob,samples,status,bucket=self.p.estimate(assessment.strategy,regime,assessment.score,levels["rr_tp1"])
        if status=="VALIDATED" and prob<self.s.min_probability:return None, f"Probability {prob:.1f}% أقل من الحد {self.s.min_probability:.1f}%"
        risk_pct=(levels["entry"]-levels["sl"])/levels["entry"]*100
        risk_level="منخفضة" if risk_pct<=1.5 else "متوسطة" if risk_pct<=3 else "مرتفعة"
        if risk_level=="مرتفعة": return None, f"مخاطرة الوقف مرتفعة ({risk_pct:.2f}%)"

        trade_horizon = str(getattr(native_decision, "horizon", getattr(judge_decision, "horizon", "intraday")) or "intraday")
        if trade_horizon == "intraday":
            expected=("نفس الجلسة","نفس الجلسة","نفس الجلسة")
        elif trade_horizon == "two_day":
            expected=("1–2 جلسة","1–2 جلسة","1–2 جلسة")
        else:
            expected=("1–2 جلسة","2–4 جلسات","3–5 جلسات")
        target_reasons=["الهدف الأول مبني على مسافة الوقف وبحد أدنى للعائد مقابل المخاطرة", "الهدف الثاني امتداد محسوب بوحدات R بعد الهدف الأول", "الهدف الثالث امتداد أكبر بوحدات R إذا استمر الاتجاه"]
        now=datetime.now(timezone.utc)
        signal = Signal(
            trade_id=f"TASI-{now.strftime('%Y%m%d-%H%M%S')}-{candidate.quote.symbol}",
            symbol=candidate.quote.symbol,name=candidate.quote.name,name_en=candidate.quote.name_en,direction="BUY",
            **levels,score=round(assessment.score,2),probability=prob,probability_status=status,probability_samples=samples,
            probability_bucket=bucket,strategy=assessment.strategy,trade_type=assessment.trade_type,market_regime=regime,
            sector=sector or "غير متاح",risk_level=risk_level,grade=getattr(assessment, "grade", "A"),discovered_at=now.isoformat(),
            expected_tp1=expected[0],expected_tp2=expected[1],expected_tp3=expected[2],reasons=assessment.reasons,
            target_reasons=target_reasons,invalidation_reasons=assessment.invalidation_reasons,
            indicators={k:round(float(v),3) for k,v in f.items() if isinstance(v,(int,float))},
            quote_updated_at=quote_updated_at or "",historical_updated_at=historical_updated_at or "",
            hunter_score=round(float(assessment.score),2),
            judge_score=round(float(getattr(judge_decision,"score",assessment.score)),2),
            required_score=round(float(getattr(judge_decision,"required_score",self.s.min_score)),2),
            judge_decision=str(getattr(judge_decision,"decision","APPROVE")),
            market_state=str(getattr(judge_decision,"market_state",regime)),
            liquidity_state=str(getattr(judge_decision,"liquidity_state","UNKNOWN")),
            volatility_state=str(getattr(judge_decision,"volatility_state","UNKNOWN")),
            data_quality=str(getattr(judge_decision,"data_quality","UNKNOWN")),
            learning_adjustment=round(float(getattr(judge_decision,"learning_adjustment",0)),2),
            confirmed_setup=str(getattr(judge_decision,"confirmed_setup","UNKNOWN")),
            judge_reasons=list(getattr(judge_decision,"reasons",[]) or []),
            judge_blockers=list(getattr(judge_decision,"blockers",[]) or []),
            trade_horizon=trade_horizon,
            leadership_score=round(float(getattr(judge_decision,"leadership_score",0) or 0),2),
            entry_quality_score=round(float(getattr(judge_decision,"entry_quality_score",0) or 0),2),
            persistence_score=round(float(getattr(judge_decision,"persistence_score",0) or 0),2),
            catalyst_score=round(float(getattr(judge_decision,"catalyst_score",0) or 0),2),
            catalyst_impact=("HIGH" if abs(float(getattr(judge_decision,"catalyst_score",0) or 0)) >= 3 else
                             "MEDIUM" if abs(float(getattr(judge_decision,"catalyst_score",0) or 0)) > 0 else "NONE"),
            limit_state=str(getattr(judge_decision,"limit_state","NORMAL")),
            momentum_decay=round(float(getattr(judge_decision,"momentum_decay",0) or 0),2),
            decision_state=str(getattr(native_decision, "state", "TRADE_READY")),
            money_flow_score=round(float(getattr(native_decision, "money_flow_score", 0) or 0),2),
            structure_score=round(float(getattr(native_decision, "structure_score", 0) or 0),2),
            target_feasibility_score=round(float(getattr(native_decision, "target_feasibility_score", 0) or 0),2),
            risk_score=round(float(getattr(native_decision, "risk_score", 0) or 0),2),
            market_score=round(float(getattr(native_decision, "market_score", 0) or 0),2),
            horizon_sessions=int(getattr(native_decision, "horizon_sessions", 1) or 1),
            decision_time=now.isoformat(),
            data_cutoff=historical_updated_at or quote_updated_at or "",
        )
        return signal, "TRADE_READY"

    def build_assessment(self, candidate, regime, sector, assessment, quote_updated_at="", historical_updated_at="", judge_decision=None, native_decision=None):
        signal, _ = self.build_assessment_with_diagnostics(
            candidate, regime, sector, assessment, quote_updated_at=quote_updated_at,
            historical_updated_at=historical_updated_at, judge_decision=judge_decision, native_decision=native_decision
        )
        return signal
