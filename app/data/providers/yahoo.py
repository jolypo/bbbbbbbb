from datetime import datetime, timezone
import asyncio
import httpx
import pandas as pd

from .base import Quote


class YahooHistoricalProvider:
    """Free research-only historical provider for Saudi stocks via Yahoo chart data.

    Saudi Exchange symbols are mapped as 2140 -> 2140.SR. This provider is
    deliberately secondary: SAHMK remains the quote/monitoring source.
    """

    def __init__(self, timeout=20.0):
        self.client = httpx.AsyncClient(timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})

    async def close(self):
        await self.client.aclose()

    @staticmethod
    def ticker(symbol):
        symbol = str(symbol).strip().upper()
        return symbol if "." in symbol else f"{symbol}.SR"

    async def _chart(self, symbol, range_, interval):
        ticker = self.ticker(symbol)
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
        r = await self.client.get(url, params={"range": range_, "interval": interval, "includePrePost": "false", "events": "div,splits"})
        r.raise_for_status()
        payload = r.json()
        result = ((payload.get("chart") or {}).get("result") or [])
        if not result:
            return None, None
        item = result[0]
        timestamps = item.get("timestamp") or []
        quote = (((item.get("indicators") or {}).get("quote") or [{}])[0])
        if not timestamps or not quote:
            return None, None
        n = len(timestamps)
        data = {
            "timestamp": timestamps,
            "open": (quote.get("open") or [None] * n),
            "high": (quote.get("high") or [None] * n),
            "low": (quote.get("low") or [None] * n),
            "close": (quote.get("close") or [None] * n),
            "volume": (quote.get("volume") or [None] * n),
        }
        df = pd.DataFrame(data)
        df["datetime"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
        for c in ["open", "high", "low", "close", "volume"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df.dropna(subset=["open", "high", "low", "close", "volume"]).sort_values("datetime").reset_index(drop=True)

        # Do not let a still-forming Yahoo intraday candle create a weak RVOL,
        # fake wick, or false breakout signal. Yahoo timestamps are bar starts.
        if interval.endswith("m") and not df.empty:
            try:
                minutes = int(interval[:-1])
                last_start = df.iloc[-1]["datetime"]
                now = pd.Timestamp.now(tz="UTC")
                if last_start + pd.Timedelta(minutes=minutes) > now:
                    df = df.iloc[:-1].reset_index(drop=True)
            except (TypeError, ValueError):
                pass

        meta = item.get("meta") or {}
        return df, meta

    async def intraday(self, symbol):
        return await self._chart(symbol, "1mo", "15m")

    async def daily(self, symbol):
        return await self._chart(symbol, "1y", "1d")

    async def tasi_daily(self):
        """Research-only TASI daily series for multi-session relative strength."""
        return await self._chart("^TASI.SR", "1y", "1d")

    async def datasets(self, symbol):
        intraday, imeta = await self.intraday(symbol)
        daily, dmeta = await self.daily(symbol)
        return {"intraday": intraday, "daily": daily, "intraday_meta": imeta or {}, "daily_meta": dmeta or {}}


    async def market_snapshots(self, symbols, *, concurrency=10):
        """Build lightweight delayed snapshots for a full-market stage-1 scan.

        This is research/screening data only. Finalists are still refreshed from
        the primary market-data router before Hunter/Judge analysis. One Yahoo
        chart request per symbol avoids consuming the SAHMK free daily quota.
        """
        sem = asyncio.Semaphore(max(1, int(concurrency)))

        async def one(symbol):
            async with sem:
                try:
                    df, _ = await self._chart(symbol, "5d", "15m")
                    if df is None or df.empty:
                        return None
                    local = df.copy()
                    local["riyadh_date"] = local["datetime"].dt.tz_convert("Asia/Riyadh").dt.date
                    last_date = local.iloc[-1]["riyadh_date"]
                    session = local[local["riyadh_date"] == last_date]
                    if session.empty:
                        session = local.tail(min(26, len(local)))
                    first_open = float(session.iloc[0]["open"])
                    last = session.iloc[-1]
                    price = float(last["close"])
                    change = ((price / first_open) - 1.0) * 100.0 if first_open > 0 else 0.0
                    volume = float(session["volume"].sum())
                    value = max(0.0, price * volume)
                    stamp = last["datetime"]
                    if hasattr(stamp, "to_pydatetime"):
                        stamp = stamp.to_pydatetime()
                    if stamp.tzinfo is None:
                        stamp = stamp.replace(tzinfo=timezone.utc)
                    return Quote(
                        symbol=str(symbol), name="", name_en="", price=price,
                        change_percent=change, volume=volume, value=value,
                        updated_at=stamp, is_delayed=True,
                        raw={"source":"yahoo_full_market_stage1"},
                    )
                except Exception as exc:
                    print(f"[Yahoo] stage1 snapshot {symbol} failed: {exc}")
                    return None

        results = await asyncio.gather(*(one(s) for s in symbols))
        return [q for q in results if q is not None]

    @staticmethod
    def validate_against_quote(df, sahmk_price, max_gap_pct=15.0):
        if df is None or df.empty or sahmk_price <= 0:
            return False
        last = float(df.iloc[-1]["close"])
        gap = abs(last - float(sahmk_price)) / float(sahmk_price) * 100
        return gap <= max_gap_pct

    @staticmethod
    def last_stamp(df):
        if df is None or df.empty:
            return None
        value = df.iloc[-1]["datetime"]
        if hasattr(value, "to_pydatetime"):
            value = value.to_pydatetime()
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value
