from __future__ import annotations

from dataclasses import dataclass, asdict


@dataclass
class EmergingLeaderSnapshot:
    score: float
    relative_strength: float
    acceleration: float
    persistence: float
    state: str
    reasons: list[str]

    def to_dict(self):
        return asdict(self)


def _cap(v: float) -> float:
    return max(0.0, min(100.0, float(v)))


def stage1_emerging_score(quote, market_change_pct: float, *, acceleration: float = 0.0,
                          persistence: float = 50.0, min_traded_value: float = 2_000_000.0,
                          daily_limit_pct: float = 10.0, near_limit_buffer_pct: float = 0.75) -> EmergingLeaderSnapshot:
    """Cheap intraday leader detector.

    This is deliberately a *discovery* score, not an entry permission. A stock can
    be an exceptional leader and still be WAIT_PULLBACK/NO_CHASE later.
    """
    change = float(getattr(quote, "change_percent", 0.0) or 0.0)
    value = float(getattr(quote, "value", 0.0) or 0.0)
    rs = change - float(market_change_pct or 0.0)
    acc = float(acceleration or 0.0)
    persist = float(persistence or 50.0)
    reasons: list[str] = []

    score = 35.0
    # Relative-strength transition is the primary signal for Saudi intraday leaders.
    if rs >= 6.0:
        score += 25; reasons.append(f"RS استثنائية مقابل TASI (+{rs:.2f})")
    elif rs >= 4.0:
        score += 21; reasons.append(f"RS قوية جدًا مقابل TASI (+{rs:.2f})")
    elif rs >= 2.5:
        score += 16; reasons.append(f"RS قوية مقابل TASI (+{rs:.2f})")
    elif rs >= 1.25:
        score += 10; reasons.append(f"RS إيجابية (+{rs:.2f})")
    elif rs < 0:
        score -= 8

    # Scan-to-scan acceleration catches names that become leaders after a quiet open.
    if acc >= 1.5:
        score += 18; reasons.append(f"تسارع قوي بين الفحوصات ({acc:+.2f})")
    elif acc >= 0.75:
        score += 12; reasons.append(f"تسارع واضح ({acc:+.2f})")
    elif acc >= 0.25:
        score += 6; reasons.append(f"بداية تسارع ({acc:+.2f})")
    elif acc <= -1.0:
        score -= 10; reasons.append("الزخم يفقد سرعته")

    # Persistence prevents one-tick/one-scan spikes from dominating the radar.
    score += max(-8.0, min(10.0, (persist - 50.0) * 0.20))
    if persist >= 70:
        reasons.append(f"قيادة مستمرة P={persist:.0f}")

    # Liquidity belongs to execution, but discovery must not hide an exceptional
    # leader solely because absolute traded value is still low.  This is a radar
    # override only: Judge keeps the hard execution-liquidity gate unchanged.
    exceptional_leader = rs >= 6.0 and change >= 8.0
    if min_traded_value > 0:
        ratio = value / float(min_traded_value)
        if ratio >= 8:
            score += 12; reasons.append("قيمة تداول استثنائية")
        elif ratio >= 4:
            score += 9; reasons.append("قيمة تداول قوية")
        elif ratio >= 1:
            score += 4
        elif exceptional_leader:
            reasons.append("استثناء اكتشاف: قائد استثنائي بسيولة تنفيذ منخفضة")
        else:
            score -= 10; reasons.append("السيولة أقل من الحد المفضل")

    # Large movers belong on the leader radar even if entry may already be too late.
    if change >= 8.0:
        score += 10; reasons.append(f"قائد يومي قرب الحد الأعلى ({change:+.2f}%)")
    elif change >= 5.0:
        score += 7; reasons.append(f"حركة يومية قوية ({change:+.2f}%)")
    elif change >= 3.0:
        score += 4

    near_limit = change >= max(0.0, float(daily_limit_pct) - float(near_limit_buffer_pct))
    state = "LEADER_RADAR"
    if near_limit:
        state = "NO_CHASE"
    elif change >= 6.0:
        state = "WAIT_PULLBACK"

    return EmergingLeaderSnapshot(_cap(score), rs, acc, persist, state, reasons)


def mtf_consensus_score(features: dict) -> tuple[float, list[str]]:
    """Score 15m/60m/Daily alignment from the already-computed assessment features."""
    f = dict(features or {})
    reasons: list[str] = []
    points = 0.0
    total = 0.0

    # 15m execution frame.
    total += 40
    if float(f.get("ema9", 0) or 0) > float(f.get("ema20", 0) or 0):
        points += 12
    if float(f.get("ema20_slope_pct", 0) or 0) > 0:
        points += 8
    if float(f.get("macd_hist", 0) or 0) > 0:
        points += 8
    if float(f.get("momentum5_pct", 0) or 0) > 0:
        points += 7
    if float(f.get("close_position", 0.5) or 0.5) >= 0.60:
        points += 5

    # 60m confirmation.
    h1_close = f.get("h1_close")
    h1_ema20 = f.get("h1_ema20")
    if h1_close is not None and h1_ema20 is not None:
        total += 30
        if float(h1_close) > float(h1_ema20): points += 12
        if float(f.get("h1_ema20_slope_pct", 0) or 0) > 0: points += 8
        if float(f.get("h1_macd_hist", 0) or 0) > 0: points += 6
        if float(f.get("h1_ema9", 0) or 0) > float(h1_ema20): points += 4

    # Daily context. It is context, not a hard veto for an intraday leader.
    d1_close = f.get("d1_close")
    d1_ema20 = f.get("d1_ema20")
    if d1_close is not None and d1_ema20 is not None:
        total += 30
        if float(d1_close) > float(d1_ema20): points += 11
        if float(f.get("d1_ema20_slope_pct", 0) or 0) > 0: points += 8
        if float(f.get("d1_macd_hist", 0) or 0) > 0: points += 6
        rsi = float(f.get("d1_rsi14", 50) or 50)
        if 45 <= rsi <= 75: points += 5

    score = 50.0 if total <= 0 else (points / total) * 100.0
    if score >= 75: reasons.append("توافق قوي بين 15m/60m/Daily")
    elif score >= 60: reasons.append("توافق زمني جيد")
    elif score < 45: reasons.append("توافق الفترات ضعيف؛ يحتاج تأكيد")
    return _cap(score), reasons


def execution_state(*, leadership_score: float, entry_quality_score: float, mtf_score: float,
                    limit_state: str, features: dict) -> str:
    """Separate leadership from entry quality: leaders must not disappear just because they are extended."""
    f = dict(features or {})
    vwap_ext = float(f.get("vwap_distance_atr", 0) or 0)
    ema_ext = float(f.get("ema20_distance_atr", 0) or 0)
    failed = float(f.get("failed_breakout", 0) or 0) >= 0.5
    if str(limit_state) == "LIMIT_UP":
        return "NO_CHASE"
    if failed:
        return "REJECT"
    if str(limit_state) == "NEAR_LIMIT_UP" or vwap_ext > 1.5 or ema_ext > 2.0:
        return "WAIT_PULLBACK" if leadership_score >= 70 else "NO_CHASE"
    if leadership_score >= 70 and entry_quality_score >= 55 and mtf_score >= 55:
        return "EXECUTABLE"
    if leadership_score >= 65:
        return "WAIT_PULLBACK"
    return "WATCH"
