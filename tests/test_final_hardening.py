from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from app.backtest.engine import evaluate_long_signal, summarize, walk_forward_slices
from app.data.tasilab import TasilabProvider
from app.database.json_store import JsonStore
from app.database.store import SqlStore
from app.trades.manager import TradeManager


def _settings():
    return SimpleNamespace(
        max_open_trades=5,
        fee_bps=15.5,
        slippage_bps=5,
        tp1_percent=30,
        tp2_percent=30,
        tp3_percent=40,
        trailing_after_tp1_to_entry=True,
        trailing_stop_enabled=False,
        trailing_after_tp2_atr=1.0, min_rr=1.0,
    )


def _signal():
    return {
        "symbol": "2222", "name": "Test", "entry": 100.0,
        "entry_low": 99.9, "entry_high": 100.1,
        "sl": 98.0, "tp1": 102.0, "tp2": 104.0, "tp3": 106.0,
        "discovered_at": "2026-08-27T07:00:00+00:00",
    }


def test_tasilab_missing_timestamp_stays_missing():
    provider = object.__new__(TasilabProvider)
    q = provider.quote_from_payload({"symbol": "2222", "price": 30.0})
    assert q is not None
    assert q.updated_at is None
    assert q.raw["_timestamp_source"] == "missing"


def test_completed_bar_catches_tp_between_snapshots(tmp_path):
    store = JsonStore(tmp_path)
    manager = TradeManager(store, _settings())
    assert manager.add(_signal())
    manager.activate_entry("2222", 100.0)
    trade, events = manager.update_bar(
        "2222", high=102.5, low=99.0, close=100.2,
        bar_time="2026-08-27T07:15:00+00:00",
    )
    assert "TP1" in events
    assert trade["tp1_hit"] is True
    assert trade["status"] == "OPEN"


def test_completed_bar_uses_conservative_stop_first(tmp_path):
    store = JsonStore(tmp_path)
    manager = TradeManager(store, _settings())
    assert manager.add(_signal())
    manager.activate_entry("2222", 100.0)
    trade, events = manager.update_bar(
        "2222", high=103.0, low=97.5, close=101.0,
        bar_time="2026-08-27T07:15:00+00:00",
    )
    assert events == ["SL"]
    assert trade["status"] == "CLOSED_SL"
    assert trade["bar_execution_assumption"] == "CONSERVATIVE_STOP_FIRST"


def test_sql_store_persists_state_and_history(tmp_path):
    url = f"sqlite:///{tmp_path / 'state.db'}"
    store1 = SqlStore(url)
    state = store1.state()
    state["paused"] = True
    store1.save_state(state)
    store1.save_history([{"symbol": "2222", "result": "WIN"}])

    store2 = SqlStore(url)
    assert store2.state()["paused"] is True
    assert store2.history()[0]["symbol"] == "2222"


def test_backtest_engine_conservative_and_summary():
    signal = _signal()
    bars = pd.DataFrame([
        {"datetime": "2026-08-27T07:15:00Z", "high": 102.5, "low": 99.5, "close": 102.0},
        {"datetime": "2026-08-27T07:30:00Z", "high": 106.5, "low": 101.5, "close": 106.0},
    ])
    result = evaluate_long_signal(signal, bars, fee_bps=0, slippage_bps=0)
    assert result.result == "WIN"
    assert result.exit_reason == "TP3"
    metrics = summarize([result])
    assert metrics["trades"] == 1
    assert metrics["wins"] == 1


def test_walk_forward_slices_are_out_of_sample():
    slices = list(walk_forward_slices(100, train=60, test=20, step=20))
    assert slices == [((0, 60), (60, 80)), ((20, 80), (80, 100))]


def test_sahmk_counts_failed_attempt_toward_local_budget():
    import asyncio
    import httpx
    from app.data.providers.sahmk import SahmkProvider

    async def run():
        def handler(request):
            return httpx.Response(500, request=request, json={"detail": "upstream"})

        provider = SahmkProvider(
            "dummy", "https://example.test", min_request_interval=6.1,
            local_daily_request_limit=100,
        )
        await provider.client.aclose()
        provider.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider.max_retries = 0
        try:
            await provider._get("/x")
        except httpx.HTTPStatusError:
            pass
        stats = provider.stats()
        assert stats["daily_requests"] == 1
        assert stats["local_attempted_requests"] == 1
        assert stats["local_successful_requests"] == 0
        await provider.close()

    asyncio.run(run())


def test_signal_engine_is_technical_not_capital_sized():
    from app.data.providers.base import Quote
    from app.signal_engine.engine import SignalEngine

    settings = SimpleNamespace(
        min_score=82, allow_long=True, min_daily_traded_value=2_000_000,
        min_probability=65, min_rr=1.8,
    )
    candidate = SimpleNamespace(
        quote=Quote(symbol="2222", name="Test", name_en="Test", price=100.0, value=10_000_000)
    )
    assessment = SimpleNamespace(
        score=90.0, hard_rejects=[], grade="A+", trade_type="مضاربة قصيرة",
        strategy="TEST", features={"atr14": 1.2, "support20": 98.5},
        reasons=[], invalidation_reasons=[],
    )
    signal = SignalEngine(settings, []).build_assessment(candidate, "BULLISH", "الطاقة", assessment)
    assert signal is not None
    assert not hasattr(signal, "position_shares")
    assert signal.trade_type == "مضاربة قصيرة"


def test_render_blueprint_uses_json_fallback_and_final_thresholds():
    import yaml
    data = yaml.safe_load(Path("render.yaml").read_text())
    assert "databases" not in data
    env = {item["key"]: item for item in data["services"][0]["envVars"] if "key" in item}
    assert env["SAHMK_DAILY_SWITCH_LIMIT"]["value"] == "95"
    assert env["INTRADAY_MAX_PRICE_GAP_PCT"]["value"] == "2.5"
    assert env["HISTORICAL_INTRADAY_MAX_AGE_MINUTES"]["value"] == "30"
    assert "DATABASE_URL" not in env
    assert "PAPER_ACCOUNT_SIZE_SAR" not in env
    assert env["LEARNING_MIN_SAMPLES"]["value"] == "12"
    assert env["ENTRY_WAIT_EXPIRY_MINUTES"]["value"] == "1440"

