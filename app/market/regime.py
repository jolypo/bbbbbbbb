from __future__ import annotations


def _num(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def tasi_context(summary):
    """Return regime plus breadth diagnostics without inventing missing breadth."""
    if not isinstance(summary, dict):
        return {
            "regime": "UNKNOWN",
            "change_percent": 0.0,
            "breadth": 0.0,
            "breadth_available": False,
            "advancers": 0.0,
            "decliners": 0.0,
        }

    change = _num(summary.get("change_percent", summary.get("index_change_percent", summary.get("change_pct", 0))))
    adv = _num(summary.get("advancers", summary.get("advancing", 0)))
    dec = _num(summary.get("decliners", summary.get("declining", 0)))
    available = adv + dec > 0
    breadth = (adv - dec) / (adv + dec) if available else 0.0

    # Saudi tactical regime: a mildly red index must not veto strong stocks.
    # BEARISH is reserved for broad, material market pressure.
    if change >= 0.75 and (not available or breadth >= 0.05):
        regime = "BULLISH"
    elif available and breadth >= 0.30 and change >= 0.10:
        regime = "BULLISH"
    elif change <= -1.00 and (not available or breadth <= -0.15):
        regime = "BEARISH"
    elif available and breadth <= -0.35 and change <= -0.40:
        regime = "BEARISH"
    elif abs(change) >= 1.5 and (not available or abs(breadth) < 0.15):
        regime = "HIGH_VOL"
    else:
        regime = "NEUTRAL"

    return {
        "regime": regime,
        "change_percent": change,
        "breadth": breadth,
        "breadth_available": available,
        "advancers": adv,
        "decliners": dec,
    }


def classify_tasi(summary):
    return tasi_context(summary)["regime"]
