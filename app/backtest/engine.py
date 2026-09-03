from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime
from math import prod
from typing import Iterable

import pandas as pd


@dataclass
class BacktestTradeResult:
    symbol: str
    entered_at: str
    exited_at: str | None
    result: str
    net_result_pct: float
    exit_reason: str
    bars_held: int
    mae_pct: float
    mfe_pct: float
    ambiguous_bar: bool = False

    def to_dict(self):
        return asdict(self)


def _cost_pct(fee_bps: float, slippage_bps: float) -> float:
    return (2.0 * float(fee_bps) + 2.0 * float(slippage_bps)) / 100.0


def evaluate_long_signal(
    signal: dict,
    bars: pd.DataFrame,
    *,
    fee_bps: float = 15.5,
    slippage_bps: float = 5.0,
    tp_allocations=(30.0, 30.0, 40.0),
) -> BacktestTradeResult:
    """Conservative OHLC execution backtest for an already-generated signal.

    This intentionally does not generate signals or invent historical TASI
    context. It evaluates a historical signal against future completed bars.
    When stop and target are touched in the same bar, stop-first is assumed.
    """
    required = {"datetime", "high", "low", "close"}
    if bars is None or bars.empty or not required.issubset(bars.columns):
        raise ValueError("bars must contain datetime/high/low/close")

    entry = float(signal["entry"])
    stop = float(signal["sl"])
    targets = [float(signal["tp1"]), float(signal["tp2"]), float(signal["tp3"])]
    entered = pd.Timestamp(signal["discovered_at"])
    if entered.tzinfo is None:
        entered = entered.tz_localize("UTC")
    entered = entered.tz_convert("UTC")

    df = bars.copy().sort_values("datetime")
    dt = pd.to_datetime(df["datetime"], utc=True)
    df = df.loc[dt > entered].copy()
    if df.empty:
        raise ValueError("no future completed bars after signal discovery")

    remaining = 100.0
    realized = 0.0
    hit = [False, False, False]
    active_stop = stop
    cost = _cost_pct(fee_bps, slippage_bps)
    mae = 0.0
    mfe = 0.0
    ambiguous = False

    def leg_net(exit_price: float) -> float:
        return (float(exit_price) - entry) / entry * 100.0 - cost

    for idx, row in df.reset_index(drop=True).iterrows():
        high = float(row["high"])
        low = float(row["low"])
        close = float(row["close"])
        bar_time = pd.Timestamp(row["datetime"])
        if bar_time.tzinfo is None:
            bar_time = bar_time.tz_localize("UTC")
        bar_time = bar_time.tz_convert("UTC")

        mfe = max(mfe, (high - entry) / entry * 100.0)
        mae = min(mae, (low - entry) / entry * 100.0)
        pending_targets = [i for i, target in enumerate(targets) if not hit[i] and high >= target]

        if low <= active_stop:
            if pending_targets:
                ambiguous = True
            realized += leg_net(active_stop) * remaining / 100.0
            result = "WIN" if realized > 0 else "LOSS"
            return BacktestTradeResult(
                str(signal.get("symbol", "")), entered.isoformat(), bar_time.isoformat(), result,
                round(realized, 4), "SL_CONSERVATIVE" if ambiguous else "SL", idx + 1,
                round(mae, 4), round(mfe, 4), ambiguous,
            )

        for i, target in enumerate(targets):
            if not hit[i] and high >= target:
                hit[i] = True
                alloc = float(tp_allocations[i])
                if i == 2:
                    alloc = remaining
                alloc = min(alloc, remaining)
                realized += leg_net(target) * alloc / 100.0
                remaining -= alloc

        # Break-even protection only becomes active for subsequent bars because
        # OHLC cannot prove whether TP1 occurred before the same bar's low.
        if hit[0]:
            active_stop = max(active_stop, entry)

        if hit[2] or remaining <= 0:
            result = "WIN" if realized > 0 else "LOSS"
            return BacktestTradeResult(
                str(signal.get("symbol", "")), entered.isoformat(), bar_time.isoformat(), result,
                round(realized, 4), "TP3", idx + 1, round(mae, 4), round(mfe, 4), ambiguous,
            )

    last = df.iloc[-1]
    close = float(last["close"])
    realized += leg_net(close) * remaining / 100.0
    last_time = pd.Timestamp(last["datetime"])
    if last_time.tzinfo is None:
        last_time = last_time.tz_localize("UTC")
    result = "WIN" if realized > 0 else "LOSS"
    return BacktestTradeResult(
        str(signal.get("symbol", "")), entered.isoformat(), last_time.tz_convert("UTC").isoformat(), result,
        round(realized, 4), "END_OF_DATA", len(df), round(mae, 4), round(mfe, 4), ambiguous,
    )


def summarize(results: Iterable[BacktestTradeResult | dict]) -> dict:
    rows = [r.to_dict() if hasattr(r, "to_dict") else dict(r) for r in results]
    if not rows:
        return {
            "trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
            "expectancy_pct": 0.0, "profit_factor": 0.0, "max_drawdown_pct": 0.0,
        }
    vals = [float(r.get("net_result_pct", 0.0)) for r in rows]
    wins = [v for v in vals if v > 0]
    losses = [v for v in vals if v <= 0]
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for v in vals:
        equity *= 1.0 + v / 100.0
        peak = max(peak, equity)
        if peak > 0:
            max_dd = min(max_dd, (equity - peak) / peak * 100.0)
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    return {
        "trades": len(vals),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(vals) * 100.0, 2),
        "expectancy_pct": round(sum(vals) / len(vals), 4),
        "profit_factor": round(gross_win / gross_loss, 4) if gross_loss else (999.0 if gross_win else 0.0),
        "max_drawdown_pct": round(max_dd, 4),
    }


def walk_forward_slices(length: int, train: int, test: int, step: int | None = None):
    """Yield deterministic train/test index windows for out-of-sample validation."""
    train, test = int(train), int(test)
    step = int(step or test)
    if min(length, train, test, step) <= 0:
        return
    start = 0
    while start + train + test <= length:
        yield (start, start + train), (start + train, start + train + test)
        start += step
