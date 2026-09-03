from dataclasses import dataclass

from app.data.providers.base import Quote


@dataclass
class Candidate:
    quote: Quote
    score: float
    reasons: list[str]


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fast_score(q: Quote, regime: str, market_change_pct: float = 0.0):
    """Cheap Saudi-market ranker using fields available on delayed quote feeds.

    This score only decides who deserves deeper analysis.  It must not act as a
    trade gate.  Traded *value* is weighted more than raw share volume because
    low-priced Saudi names can show very large share counts without equivalent
    execution capacity.  Relative strength versus TASI is explicitly rewarded
    so leaders are not buried when the headline index is mildly red.
    """
    score = 42.0
    reasons = []
    raw = q.raw or {}

    change = float(q.change_percent or 0)
    market_change = float(market_change_pct or 0)
    relative = change - market_change

    if 0.30 <= change <= 3.50:
        score += 11
        reasons.append("positive_momentum")
    elif 3.50 < change <= 6.50:
        score += 9
        reasons.append("strong_momentum")
    elif 6.50 < change <= 8.50:
        score += 5
        reasons.append("extended_momentum")
    elif change > 8.50:
        score += 1
        reasons.append("near_daily_limit_extension")
    elif change < -2.0:
        score -= 8
    elif change < 0:
        score -= 4

    if relative >= 4.0:
        score += 10
        reasons.append("exceptional_relative_strength")
    elif relative >= 2.0:
        score += 7
        reasons.append("strong_relative_strength")
    elif relative >= 1.0:
        score += 4
        reasons.append("relative_strength")
    elif relative <= -2.0:
        score -= 5

    # Share volume is useful, but should not dominate a SAR-value ranking.
    volume = float(q.volume or 0)
    if volume >= 1_000_000:
        score += 4
        reasons.append("high_share_volume")
    elif volume >= 250_000:
        score += 2
    elif volume > 0:
        score += 1

    value = float(q.value or 0)
    if value >= 75_000_000:
        score += 12
        reasons.append("very_high_traded_value")
    elif value >= 25_000_000:
        score += 9
        reasons.append("high_traded_value")
    elif value >= 8_000_000:
        score += 6
    elif value >= 2_000_000:
        score += 3

    if q.bid is not None and q.ask is not None and q.price > 0:
        spread = (q.ask - q.bid) / ((q.ask + q.bid) / 2.0) * 100 if (q.ask + q.bid) > 0 else 999
        if spread <= 0.25:
            score += 8
            reasons.append("tight_spread")
        elif spread <= 0.50:
            score += 4
        elif spread > 0.80:
            score -= 10

    open_price = _num(raw.get("open"))
    high = _num(raw.get("high"))
    low = _num(raw.get("low"))

    if open_price and open_price > 0:
        if q.price >= open_price:
            score += 4
            reasons.append("above_open")
        else:
            score -= 3

    if high and low and high > low and q.price > 0:
        position = (q.price - low) / (high - low)
        if position >= 0.72:
            score += 6
            reasons.append("upper_session_range")
        elif position < 0.25:
            score -= 6

    liquidity = raw.get("liquidity") if isinstance(raw.get("liquidity"), dict) else {}
    net_value = _num(liquidity.get("net_value"))
    if net_value is not None:
        if net_value > 0:
            score += 4
            reasons.append("positive_net_liquidity")
        elif net_value < 0:
            score -= 4

    if regime == "BULLISH":
        score += 4
        reasons.append("bullish_tasi")
    elif regime == "BEARISH":
        # Bearish TASI is context, not a stage-1 veto.  Relative strength above
        # already decides whether this specific stock deserves deeper work.
        score -= 3

    return Candidate(q, max(0.0, min(100.0, score)), reasons)
