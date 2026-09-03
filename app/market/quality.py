from __future__ import annotations
from dataclasses import dataclass, asdict


def _num(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


@dataclass
class MarketQuality:
    state: str
    required_score: float
    volatility_state: str
    participation_state: str
    data_quality: str
    reasons: list[str]
    penalties: dict[str, float]

    def to_dict(self):
        return asdict(self)


class TASIMarketQualityEngine:
    """Conservative Saudi market context engine.

    Uses only fields actually available in the market summary. Missing advanced
    fields (EMA/ADX/ATR/RVOL) never receive fabricated bonuses.
    """

    def evaluate(self, summary: dict | None) -> MarketQuality:
        if not isinstance(summary, dict) or not summary:
            return MarketQuality("NO_TRADE", 100.0, "UNKNOWN", "UNKNOWN", "STALE_OR_MISSING", ["بيانات TASI غير متاحة"], {"data": 10.0})

        raw_change = summary.get("change_percent", summary.get("index_change_percent", summary.get("change_pct")))
        change = _num(raw_change, 0.0)
        adv = _num(summary.get("advancers", summary.get("advancing")), 0.0)
        dec = _num(summary.get("decliners", summary.get("declining")), 0.0)
        total_volume = _num(summary.get("total_volume", summary.get("volume")), None)
        adx = _num(summary.get("adx14", summary.get("adx")), None)
        atr_pct = _num(summary.get("atr_pct"), None)
        rvol = _num(summary.get("relative_volume", summary.get("rvol")), None)
        ema20 = _num(summary.get("ema20"), None)
        ema50 = _num(summary.get("ema50"), None)
        value = _num(summary.get("index_value", summary.get("value")), None)

        breadth_available = (adv + dec) > 0
        breadth = (adv - dec) / (adv + dec) if breadth_available else 0.0
        reasons: list[str] = []
        penalties: dict[str, float] = {}
        # Hard safety gates do the rejecting. The score threshold is calibrated
        # for liquid TASI long setups, where 80+ was unrealistically restrictive.
        base = 68.0

        if value is None and not breadth_available and total_volume is None and raw_change is None:
            return MarketQuality("NO_TRADE", 100.0, "UNKNOWN", "UNKNOWN", "MISSING", ["ملخص TASI يفتقد السعر/التغير/الاتساع/الحجم"], {"data": 10.0})
        core_available = value is not None and value > 0 and raw_change is not None
        volume_available = total_volume is not None and total_volume > 0
        data_quality = "GOOD" if (core_available and breadth_available and volume_available) else "PARTIAL"
        if data_quality == "PARTIAL":
            penalties["partial_data"] = 1.0
            reasons.append("بيانات جودة السوق جزئية؛ لا توجد مكافأة للحقول المفقودة")
            if core_available and not breadth_available:
                penalties["missing_breadth"] = 1.5
                reasons.append("اتساع السوق (الصاعدة/الهابطة) غير متاح؛ رفع شرط القبول احترازيًا")
            if core_available and not volume_available:
                penalties["missing_market_volume"] = 0.5
                reasons.append("حجم السوق الإجمالي غير متاح")

        if atr_pct is not None and atr_pct >= 2.0:
            volatility = "HIGH"
            penalties["high_volatility"] = 5.0
            reasons.append(f"تذبذب TASI مرتفع ATR%={atr_pct:.2f}")
        else:
            volatility = "NORMAL"

        if rvol is not None and rvol < 0.70:
            participation = "LOW"
            penalties["low_participation"] = 3.0
            reasons.append(f"مشاركة السوق ضعيفة RVOL={rvol:.2f}")
        elif total_volume is not None and total_volume <= 0:
            participation = "LOW"
            penalties["low_participation"] = 3.0
        else:
            participation = "NORMAL"

        # Trend/range classification uses richer fields when supplied, breadth/change otherwise.
        trend_up = value is not None and ema20 is not None and value > ema20 and (ema50 is None or ema20 >= ema50)
        trend_down = value is not None and ema20 is not None and value < ema20 and (ema50 is None or ema20 < ema50)
        weak_adx = adx is not None and adx < 18

        if volatility == "HIGH":
            state = "HIGH_VOLATILITY"
        elif participation == "LOW":
            state = "LOW_PARTICIPATION"
        elif weak_adx or (abs(change) < 0.20 and breadth_available and abs(breadth) < 0.12):
            state = "RANGE"
            penalties["range"] = 3.0
            reasons.append("السوق عرضي/ضعيف الاتجاه")
        elif trend_up and change >= -0.20 and (not breadth_available or breadth >= -0.10):
            state = "BULL_TREND"
            reasons.append("اتجاه TASI البنيوي داعم للشراء")
        elif change >= 0.45 and (not breadth_available or breadth >= 0.05):
            state = "BULL_TREND"
            reasons.append("زخم TASI اليومي داعم للشراء")
        elif change <= -1.00 and (not breadth_available or breadth <= -0.15):
            state = "BEAR_TREND"
            penalties["bear_market"] = 8.0
            reasons.append("ضغط هابط واسع وقوي على TASI")
        elif breadth_available and breadth <= -0.35 and change <= -0.40:
            state = "BEAR_TREND"
            penalties["bear_market"] = 8.0
            reasons.append("اتساع السوق سلبي بوضوح مع هبوط TASI")
        elif trend_down:
            state = "BEAR_PRESSURE"
            penalties["bear_pressure"] = 4.0
            reasons.append("الاتجاه المتوسط لـTASI ضاغط؛ يسمح فقط للأسهم ذات القوة النسبية")
        elif breadth_available and abs(breadth) >= 0.20:
            state = "MIXED"
            penalties["mixed"] = 1.5
            reasons.append("اتساع السوق غير متجانس")
        else:
            state = "NORMAL"
            penalties["normal"] = 0.5

        required = min(99.0, base + sum(penalties.values()))
        return MarketQuality(state, required, volatility, participation, data_quality, reasons, penalties)
