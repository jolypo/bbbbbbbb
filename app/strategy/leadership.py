from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class LeadershipMetrics:
    relative_strength: float
    leadership_score: float
    entry_quality_score: float
    persistence_score: float
    momentum_decay_score: float
    limit_state: str
    reasons: list[str]


def _cap(v: float) -> float:
    return max(0.0, min(100.0, float(v)))


def limit_state(change_pct: float, *, daily_limit_pct: float = 10.0, near_buffer_pct: float = 0.75) -> str:
    c = float(change_pct or 0.0)
    limit = max(1.0, abs(float(daily_limit_pct or 10.0)))
    buffer = max(0.10, min(limit * 0.25, abs(float(near_buffer_pct or 0.75))))
    if c >= limit - 0.15:
        return "LIMIT_UP"
    if c >= limit - buffer:
        return "NEAR_LIMIT_UP"
    if c <= -(limit - 0.15):
        return "LIMIT_DOWN"
    if c <= -(limit - buffer):
        return "NEAR_LIMIT_DOWN"
    return "NORMAL"


def entry_quality(features: dict, *, change_pct: float = 0.0, daily_limit_pct: float = 10.0,
                  near_buffer_pct: float = 0.75) -> tuple[float, list[str]]:
    f = features or {}
    score = 50.0
    reasons: list[str] = []
    if f.get("active_vwap", f.get("vwap20")) is not None and float(f.get("close", 0) or 0) >= float(f.get("active_vwap", f.get("vwap20")) or 0):
        score += 10; reasons.append("السعر فوق VWAP")
    if str(f.get("structure_state", "")) in {"HH_HL", "BULLISH"}:
        score += 10; reasons.append("Structure صاعد")
    if f.get("retest_confirmed", 0) >= .5 or (f.get("is_breakout", 0) >= .5 and f.get("close_position", 0) >= .65):
        score += 10; reasons.append("Breakout Hold/Retest مؤكد")
    vwap_ext = float(f.get("vwap_distance_atr", 0) or 0)
    ema_ext = float(f.get("ema20_distance_atr", 0) or 0)
    if vwap_ext > 1.5:
        score -= 12; reasons.append("تمدد عن VWAP")
    if ema_ext > 2.0:
        score -= 10; reasons.append("تمدد عن EMA20")
    if float(f.get("upper_wick_pct", 0) or 0) > .45:
        score -= 12; reasons.append("Upper Wick مرتفع")
    if float(f.get("failed_breakout", 0) or 0) >= .5:
        score -= 35; reasons.append("Failed Breakout")
    state = limit_state(change_pct, daily_limit_pct=daily_limit_pct, near_buffer_pct=near_buffer_pct)
    if state == "LIMIT_UP":
        score = min(score, 20.0); reasons.append("السهم عند الحد الأعلى؛ لا توجد مساحة دخول يومية")
    elif state == "NEAR_LIMIT_UP":
        score -= 18; reasons.append("السهم قريب جدًا من الحد الأعلى")
    return _cap(score), reasons


def leadership_score(*, stock_change_pct: float, market_change_pct: float, traded_value: float,
                     min_traded_value: float, sector_change_pct: float | None = None,
                     persistence_score: float = 50.0, catalyst_score: float = 0.0) -> tuple[float, list[str]]:
    rs = float(stock_change_pct or 0.0) - float(market_change_pct or 0.0)
    score = 35.0
    reasons: list[str] = []
    if rs >= 5: score += 30; reasons.append("Relative Strength استثنائية")
    elif rs >= 3: score += 24; reasons.append("Relative Strength قوية جدًا")
    elif rs >= 1.5: score += 16; reasons.append("Relative Strength قوية")
    elif rs >= .75: score += 9; reasons.append("Relative Strength إيجابية")
    elif rs < 0: score -= 12; reasons.append("السهم أضعف من TASI")
    if traded_value >= max(1.0, min_traded_value * 3):
        score += 12; reasons.append("سيولة تنفيذية مرتفعة")
    elif traded_value >= max(1.0, min_traded_value):
        score += 7; reasons.append("سيولة تنفيذية مقبولة")
    if sector_change_pct is not None:
        if sector_change_pct >= .5: score += 6; reasons.append("القطاع داعم")
        elif sector_change_pct < -1: score -= 5; reasons.append("القطاع ضاغط")
    score += (float(persistence_score) - 50.0) * .20
    score += max(-5.0, min(5.0, float(catalyst_score or 0.0)))
    return _cap(score), reasons


class LeadershipTracker:
    """Stores scan-to-scan RS snapshots and measures whether leadership persists."""
    def __init__(self, store, *, max_points: int = 24):
        self.store = store
        self.max_points = max(6, int(max_points))

    @staticmethod
    def _now_iso(now=None):
        return (now or datetime.now(timezone.utc)).isoformat()

    def update(self, quotes, market_change_pct: float, *, now=None):
        state = self.store.state()
        meta = state.setdefault("meta", {})
        history = meta.setdefault("leadership_history", {})
        stamp = self._now_iso(now)
        for q in quotes or []:
            symbol = str(getattr(q, "symbol", "") or "").strip()
            if not symbol:
                continue
            chg = float(getattr(q, "change_percent", 0) or 0)
            row = {"time": stamp, "change": chg, "market": float(market_change_pct or 0), "rs": chg - float(market_change_pct or 0)}
            points = list(history.get(symbol, []))
            points.append(row)
            history[symbol] = points[-self.max_points:]
        # Bound stale symbols too.
        if len(history) > 400:
            history = dict(list(history.items())[-400:])
            meta["leadership_history"] = history
        self.store.save_state(state)

    def persistence(self, symbol: str) -> tuple[float, float, list[str]]:
        state = self.store.state()
        points = list(state.get("meta", {}).get("leadership_history", {}).get(str(symbol), []))
        if len(points) < 2:
            return 50.0, 0.0, ["لا توجد بعد عينات زمنية كافية للاستمرارية"]
        rs = [float(x.get("rs", 0) or 0) for x in points[-6:]]
        latest = rs[-1]
        peak = max(rs)
        delta = latest - rs[0]
        positive_ratio = sum(1 for v in rs if v >= .75) / len(rs)
        score = 45.0 + positive_ratio * 35.0 + max(-15.0, min(15.0, delta * 5.0))
        decay = max(0.0, peak - latest)
        if decay >= 3.0:
            score -= 25.0
        elif decay >= 1.5:
            score -= 12.0
        reasons = [f"استمرارية RS عبر {len(rs)} فحوص: {positive_ratio:.0%}"]
        if delta > .5: reasons.append("القوة النسبية تتحسن بين الفحوص")
        if decay >= 1.5: reasons.append(f"Momentum Decay: فقد {decay:.2f} نقطة RS من القمة")
        return _cap(score), decay, reasons

    def acceleration(self, symbol: str) -> tuple[float, list[str]]:
        """Return change/RS acceleration between the latest two observed scans.

        This is a ranking aid only. It is intentionally bounded and never
        overrides liquidity, structure, failed-breakout or risk gates.
        """
        state = self.store.state()
        points = list(state.get("meta", {}).get("leadership_history", {}).get(str(symbol), []))
        if len(points) < 2:
            return 0.0, ["لا توجد عينتان بعد لقياس التسارع"]
        prev, latest = points[-2], points[-1]
        change_delta = float(latest.get("change", 0) or 0) - float(prev.get("change", 0) or 0)
        rs_delta = float(latest.get("rs", 0) or 0) - float(prev.get("rs", 0) or 0)
        # Use the more stock-specific price acceleration but retain RS context.
        acceleration = change_delta * 0.65 + rs_delta * 0.35
        reasons = [f"Acceleration Δ={acceleration:+.2f} نقطة بين آخر فحصين"]
        return float(acceleration), reasons

    def leader_symbols(self, limit: int = 8) -> list[str]:
        """Return recent persistent/strong RS symbols for the next Stage-1 pool.

        The list is built only from observations the service has already seen;
        it does not create a new market-data request by itself.
        """
        limit = max(1, min(25, int(limit or 8)))
        state = self.store.state()
        history = state.get("meta", {}).get("leadership_history", {})
        ranked = []
        for symbol, points in (history or {}).items():
            points = list(points or [])
            if not points:
                continue
            latest = float(points[-1].get("rs", 0) or 0)
            if latest < 0.75:
                continue
            score, decay, _ = self.persistence(str(symbol))
            # A previous leader that has already collapsed should not consume
            # scarce watchlist quote capacity.
            if decay >= 4.0 and score < 45:
                continue
            ranked.append((latest + (score - 50.0) / 20.0, str(symbol)))
        ranked.sort(reverse=True)
        return [symbol for _, symbol in ranked[:limit]]
