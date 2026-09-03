from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.database.json_store import JsonStore
from app.service import TradingService
from app.strategy.emerging_leader import stage1_emerging_score


class Q:
    def __init__(self, change_percent, value):
        self.change_percent = change_percent
        self.value = value


class FakeBots:
    def __init__(self):
        self.texts = []
        self.previews = []

    async def send_admin_text(self, text):
        self.texts.append(text)

    async def send_admin_signal_preview(self, trade, prefix=None):
        self.previews.append((trade, prefix))


def make_service(tmp_path):
    svc = TradingService.__new__(TradingService)
    svc.store = JsonStore(tmp_path)
    svc.tz = ZoneInfo("Asia/Riyadh")
    svc.s = SimpleNamespace(
        signal_window_start="10:30",
        signal_window_end="14:50",
        leader_monitor_interval_minutes=30,
        leader_monitor_screen_limit=50,
        leader_monitor_detail_limit=6,
    )
    svc.b = FakeBots()
    fixed = datetime(2026, 8, 31, 8, 0, tzinfo=timezone.utc)  # 11:00 Riyadh
    svc._utc_now = lambda: fixed
    return svc


def test_exceptional_low_liquidity_leader_stays_on_radar():
    # Mirrors the blind spot found in the audit: +~10%, strong RS, but value below
    # the preferred execution-liquidity threshold. Discovery must keep it visible.
    snap = stage1_emerging_score(
        Q(9.98, 1_650_000),
        -0.20,
        acceleration=0.0,
        persistence=50.0,
        min_traded_value=2_000_000,
    )
    assert snap.score >= 68.0
    assert any("استثناء اكتشاف" in r for r in snap.reasons)
    assert snap.state in {"WAIT_PULLBACK", "NO_CHASE"}


def test_leader_monitor_is_one_day_switch_and_can_stop(tmp_path):
    svc = make_service(tmp_path)
    status = svc.enable_leader_monitor()
    assert status["enabled"] is True
    assert status["day"] == "2026-08-31"
    due, _ = svc._leader_monitor_due(force=False)
    assert due is True
    status = svc.disable_leader_monitor()
    assert status["enabled"] is False


@pytest.mark.asyncio
async def test_auto_monitor_never_publicly_publishes_and_sends_private_summary(tmp_path):
    svc = make_service(tmp_path)
    svc.enable_leader_monitor()
    svc.pending_signal = lambda: None

    calls = []
    async def fake_scan_once(**kwargs):
        calls.append(kwargs)
        return "🔎 اكتمل الفحص اليدوي.\n📌 أقرب المرشحين:\n• 1234: WAIT — قائد يحتاج Pullback"

    svc.scan_once = fake_scan_once
    ran, _ = await svc.run_leader_monitor(force=True)
    assert ran is True
    assert len(calls) == 1
    assert calls[0]["intraday_logic"] == "emerging"
    assert calls[0]["trade_horizon"] == "intraday"
    assert svc.b.texts and "لا يوجد APPROVE الآن" in svc.b.texts[-1]
    assert svc.b.previews == []


@pytest.mark.asyncio
async def test_auto_monitor_does_not_replace_pending_confirmation(tmp_path):
    svc = make_service(tmp_path)
    svc.enable_leader_monitor()
    svc.pending_signal = lambda: {"symbol": "7202"}

    async def should_not_run(**kwargs):
        raise AssertionError("scan must not run while confirmation is pending")

    svc.scan_once = should_not_run
    ran, reason = await svc.run_leader_monitor(force=True)
    assert ran is False
    assert "معلقة" in reason
