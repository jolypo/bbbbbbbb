from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.service import TradingService


class DummyStore:
    def __init__(self, state=None, history=None):
        self._state = state or {"open_trades": [], "meta": {}}
        self._history = history or []
    def state(self):
        return self._state
    def history(self):
        return self._history


@pytest.mark.asyncio
async def test_market_breadth_recovers_from_tasilab_full_market_without_sahmk():
    svc = object.__new__(TradingService)
    now = datetime(2026, 8, 31, 10, 0, tzinfo=timezone.utc)
    svc._utc_now = lambda: now
    svc.s = SimpleNamespace(
        data_max_delay_minutes=30,
        market_breadth_tasilab_enabled=True,
        market_breadth_cache_seconds=900,
        market_breadth_min_coverage=0.65,
        market_breadth_min_samples=80,
    )
    svc.universe = [{"symbol": str(1000+i)} for i in range(100)]
    svc.last_market_breadth = None
    svc.last_market_breadth_at = None
    svc.last_market_summary = None
    svc.last_market_summary_at = None

    quotes = {}
    for i in range(100):
        change = 1.0 if i < 60 else (-1.0 if i < 90 else 0.0)
        quotes[str(1000+i)] = SimpleNamespace(
            symbol=str(1000+i), price=10.0, change_percent=change, updated_at=now
        )

    class TasiLab:
        async def quotes(self, symbols):
            assert len(symbols) == 100
            return quotes

    svc.p = SimpleNamespace(tasilab=TasiLab())
    out = await svc._recover_market_breadth({"index_value": 11100.0})
    assert out["advancers"] == 60
    assert out["decliners"] == 30
    assert out["breadth_unchanged"] == 10
    assert out["breadth_source"] == "TASILAB_FULL_MARKET"
    assert out["breadth_coverage"] == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_market_breadth_does_not_publish_partial_sample_as_market_breadth():
    svc = object.__new__(TradingService)
    now = datetime(2026, 8, 31, 10, 0, tzinfo=timezone.utc)
    svc._utc_now = lambda: now
    svc.s = SimpleNamespace(
        data_max_delay_minutes=30,
        market_breadth_tasilab_enabled=True,
        market_breadth_cache_seconds=900,
        market_breadth_min_coverage=0.65,
        market_breadth_min_samples=80,
    )
    svc.universe = [{"symbol": str(1000+i)} for i in range(100)]
    svc.last_market_breadth = None
    svc.last_market_breadth_at = None
    svc.last_market_summary = None
    svc.last_market_summary_at = None
    quotes = {
        str(1000+i): SimpleNamespace(symbol=str(1000+i), price=10, change_percent=1, updated_at=now)
        for i in range(50)
    }
    class TasiLab:
        async def quotes(self, symbols):
            return quotes
    svc.p = SimpleNamespace(tasilab=TasiLab())
    out = await svc._recover_market_breadth({"index_value": 11100.0})
    assert out.get("advancers") is None
    assert out.get("decliners") is None


def test_performance_horizon_uses_settled_only_and_filtered_open_count():
    svc = object.__new__(TradingService)
    svc.store = DummyStore(
        state={
            "open_trades": [
                {"trade_horizon": "intraday"},
                {"trade_horizon": "multi_session"},
            ],
            "meta": {},
        },
        history=[
            {"trade_horizon": "intraday", "result": "WIN", "result_pct": 4.0},
            {"trade_horizon": "intraday", "result": "LOSS", "result_pct": -2.0},
            {"trade_horizon": "intraday", "result": "MISSED_ENTRY", "result_pct": 99.0},
            {"trade_horizon": "multi_session", "result": "WIN", "result_pct": 10.0},
        ],
    )
    text = svc.performance_text("intraday")
    assert "متوسط العائد: +1.00%" in text
    assert "الصفقات المفتوحة: 1" in text
    assert "Win Rate: 50.0%" in text


def test_status_reports_cached_breadth_source_without_network_call():
    svc = object.__new__(TradingService)
    svc.store = DummyStore({"open_trades": [], "meta": {"last_scan": "—"}})
    svc.last_market_summary = {"advancers": 120, "decliners": 130, "breadth_source": "FULL_MARKET_SCAN"}
    svc.last_monitor = None
    svc.universe = [1] * 272
    svc.s = SimpleNamespace(sahmk_plan="free", paper_mode=True, trade_price_update_minutes=20)
    svc.market_is_open = lambda: True
    svc.p = SimpleNamespace(
        provider_order_text=lambda: "SAHMK → Tasilab",
        active_provider_detail=lambda: "SAHMK",
    )
    text = svc.status_text()
    assert "Market Breadth: 120/130 (FULL_MARKET_SCAN)" in text
    assert "SAHMK → Tasilab" in text


def test_settings_exposes_provider_order_and_time_windows_without_secrets():
    svc = object.__new__(TradingService)
    svc.s = SimpleNamespace(
        sahmk_plan="free", market_data_start="10:15", signal_window_start="10:30", signal_window_end="14:50",
        market_breadth_tasilab_enabled=True, manual_quotes_per_signal=50, detail_quotes_per_signal=5,
        min_score=82, min_probability=65, max_daily_signals=3, max_open_trades=5,
        trade_monitor_quotes_per_cycle=5, scan_interval_seconds=900, trade_price_update_minutes=20,
        data_max_delay_minutes=30, min_rr=1.8, paper_mode=True,
    )
    svc.p = SimpleNamespace(provider_order_text=lambda: "SAHMK → Tasilab")
    text = svc.settings_text()
    assert "10:15" in text and "10:30–14:50" in text
    assert "SAHMK → Tasilab" in text
    assert "Secrets: HIDDEN" in text

from app.market.quality import TASIMarketQualityEngine


def test_market_quality_is_partial_when_breadth_missing():
    q = TASIMarketQualityEngine().evaluate({
        "index_value": 11148.18,
        "change_percent": -0.09,
        "total_volume": 223_118_156,
        "advancers": None,
        "decliners": None,
    })
    assert q.data_quality == "PARTIAL"
    assert q.penalties.get("missing_breadth") == pytest.approx(1.5)
    assert any("اتساع السوق" in reason for reason in q.reasons)


def test_render_uses_one_percent_policy_not_legacy_threshold_list():
    from pathlib import Path
    text = (Path(__file__).resolve().parents[1] / "render.yaml").read_text(encoding="utf-8")
    assert "PROFIT_ALERT_STEP_PCT" in text
    assert "value: '1.0'" in text
    assert "PROFIT_ALERT_THRESHOLDS" not in text
    assert "MARKET_BREADTH_TASILAB_ENABLED" in text


def test_gitignore_protects_real_env_files():
    from pathlib import Path
    text = (Path(__file__).resolve().parents[1] / ".gitignore").read_text(encoding="utf-8")
    assert ".env\n" in text
    assert ".env.*" in text
    assert "!.env.example" in text


def test_live_signal_preview_does_not_use_static_trade_card():
    from pathlib import Path
    text = (Path(__file__).resolve().parents[1] / "app/telegram/bots.py").read_text(encoding="utf-8")
    start = text.index("async def send_admin_signal_preview")
    end = text.index("# CONNECTION TEST", start)
    block = text[start:end]
    assert "trade_card.png" not in block
    assert "send_photo" not in block
    assert "preview_message(trade)" in block


def test_live_reports_use_dynamic_report_card_not_static_zero_image():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    service = (root / "app/service.py").read_text(encoding="utf-8")
    assert "build_report_card" in service
    daily = service[service.index("async def daily_report"):service.index("async def weekly_report")]
    weekly = service[service.index("async def weekly_report"):service.index("# PERFORMANCE")]
    assert "daily_report.png" not in daily
    assert "weekly_report.png" not in weekly
    assert "daily_report_live.png" in daily
    assert "weekly_report_live.png" in weekly


def test_all_core_commands_remain_registered():
    from pathlib import Path
    text = (Path(__file__).resolve().parents[1] / "app/telegram/bots.py").read_text(encoding="utf-8")
    block = text[text.index("def _add_handlers"):text.index("async def start_commands")]
    for cmd in ("start", "help", "signal", "market", "open", "performance", "daily_report", "report", "status", "health", "settings", "risk", "pause", "resume"):
        assert f'"{cmd}":' in block


def test_dynamic_report_card_renders_from_metrics(tmp_path):
    from app.telegram.report_card import build_report_card
    out = tmp_path / "report.png"
    build_report_card({
        "period": "daily", "period_label": "31-08-2026", "wins": 2, "losses": 1,
        "waiting_entry": 1, "active_open": 2, "missed": 0, "win_rate": 66.7,
        "gross_win": 5.5, "gross_loss": 1.2, "net": 4.3,
    }, str(out))
    assert out.exists() and out.stat().st_size > 10_000
