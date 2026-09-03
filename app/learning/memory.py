from __future__ import annotations
import json
from collections import defaultdict
from pathlib import Path


class LearningMemory:
    """Local, bounded performance learning. Never replaces strategy rules."""
    def __init__(self, path, min_samples=12, max_adjustment=2.0):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.min_samples = max(1, int(min_samples))
        self.max_adjustment = abs(float(max_adjustment))

    def _default(self):
        return {"version": 1, "trades": []}

    def load(self):
        if not self.path.exists():
            return self._default()
        try:
            obj = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(obj, dict) or not isinstance(obj.get("trades", []), list):
                return self._default()
            return {"version": int(obj.get("version", 1)), "trades": obj.get("trades", [])}
        except Exception:
            return self._default()

    def save(self, data):
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    @staticmethod
    def bucket(trade):
        return "|".join([
            str(trade.get("strategy", "UNKNOWN")),
            str(trade.get("direction", "BUY")),
            str(trade.get("market_state", trade.get("market_regime", "UNKNOWN"))),
            str(trade.get("liquidity_state", "UNKNOWN")),
        ])

    def record(self, trade):
        if trade.get("result") not in {"WIN", "LOSS"}:
            return False
        data = self.load()
        trade_id = str(trade.get("trade_id", ""))
        if trade_id and any(str(x.get("trade_id")) == trade_id for x in data["trades"]):
            return False
        row = {
            "trade_id": trade_id,
            "symbol": trade.get("symbol"), "name": trade.get("name"),
            "strategy": trade.get("strategy"), "direction": trade.get("direction", "BUY"),
            "market_state": trade.get("market_state", trade.get("market_regime")),
            "liquidity_state": trade.get("liquidity_state", "UNKNOWN"),
            "volatility_state": trade.get("volatility_state", "UNKNOWN"),
            "signal_score": float(trade.get("hunter_score", trade.get("score", 0)) or 0),
            "judge_score": float(trade.get("judge_score", trade.get("score", 0)) or 0),
            "entry": trade.get("entry"), "exit": trade.get("exit"),
            "pnl_percent": float(trade.get("result_pct", 0) or 0), "result": trade.get("result"),
        }
        data["trades"].append(row)
        self.save(data)
        return True

    def stats(self, context=None):
        trades = self.load()["trades"]
        groups = defaultdict(list)
        for t in trades:
            groups[self.bucket(t)].append(t)
        selected = trades
        key = self.bucket(context or {}) if context else None
        if key and groups.get(key):
            selected = groups[key]
        wins = sum(1 for x in selected if x.get("result") == "WIN")
        n = len(selected)
        wr = (wins / n * 100.0) if n else 0.0
        avg = sum(float(x.get("pnl_percent", 0) or 0) for x in selected) / n if n else 0.0
        gross_win = sum(max(0.0, float(x.get("pnl_percent", 0) or 0)) for x in selected)
        gross_loss = abs(sum(min(0.0, float(x.get("pnl_percent", 0) or 0)) for x in selected))
        pf = gross_win / gross_loss if gross_loss else (999.0 if gross_win > 0 else 0.0)
        equity=0.0; peak=0.0; max_dd=0.0
        for x in selected:
            equity += float(x.get("pnl_percent",0) or 0)
            peak=max(peak,equity)
            max_dd=min(max_dd,equity-peak)
        active = n >= self.min_samples
        adjustment = 0.0
        if active:
            # conservative blend; bounded and never overrides hard gates
            adjustment = (wr - 50.0) / 20.0
            if avg < 0:
                adjustment -= 0.5
            if pf < 1.0:
                adjustment -= 0.5
            adjustment = max(-self.max_adjustment, min(self.max_adjustment, adjustment))
        return {"status": "ACTIVE" if active else "COLLECTING", "samples": n, "min_samples": self.min_samples,
                "wins": wins, "losses": n-wins, "win_rate": round(wr,1), "avg_return": round(avg,2),
                "profit_factor": round(pf,2), "expectancy": round(avg,2), "max_drawdown_pct": round(max_dd,2),
                "adjustment": round(adjustment,2), "bucket": key}

    def group_summaries(self, limit=6):
        trades=self.load()["trades"]
        groups=defaultdict(list)
        for t in trades: groups[self.bucket(t)].append(t)
        rows=[]
        for key,items in groups.items():
            n=len(items); wins=sum(1 for x in items if x.get("result")=="WIN")
            wr=wins/n*100 if n else 0.0
            avg=sum(float(x.get("pnl_percent",0) or 0) for x in items)/n if n else 0.0
            rows.append({"bucket":key,"samples":n,"wins":wins,"losses":n-wins,"win_rate":round(wr,1),"avg_return":round(avg,2)})
        rows.sort(key=lambda x:(x["samples"],x["win_rate"]),reverse=True)
        return rows[:max(1,int(limit))]

    def reset(self):
        self.save(self._default())

    def validate_import(self, obj):
        if not isinstance(obj, dict) or not isinstance(obj.get("trades"), list):
            raise ValueError("invalid learning file")
        clean=[]
        for row in obj["trades"]:
            if not isinstance(row, dict):
                continue
            if row.get("result") not in {"WIN","LOSS"}:
                continue
            clean.append(row)
        return {"version": 1, "trades": clean}

    def import_bytes(self, raw):
        obj=json.loads(raw.decode("utf-8"))
        clean=self.validate_import(obj)
        self.save(clean)
        return len(clean["trades"])
