from pathlib import Path


def test_approved_visual_assets_are_bundled():
    root = Path(__file__).resolve().parents[1]
    for rel in (
        "app/assets/telegram/trade_card.png",
        "app/assets/telegram/profit_update.png",
        "app/assets/telegram/daily_report.png",
        "app/assets/telegram/weekly_report.png",
    ):
        p = root / rel
        assert p.exists() and p.stat().st_size > 100_000


def test_profit_updates_reply_to_original_signal():
    root = Path(__file__).resolve().parents[1]
    bots = (root / "app/telegram/bots.py").read_text(encoding="utf-8")
    service = (root / "app/service.py").read_text(encoding="utf-8")
    manager = (root / "app/trades/manager.py").read_text(encoding="utf-8")
    assert "reply_to_message_id" in bots
    assert "signal_message_ids" in bots
    assert "set_signal_message_ids" in service
    assert "set_signal_message_ids" in manager
    assert "_send_dynamic_profit_update" in service


def test_daily_weekly_reports_and_private_tests_exist():
    root = Path(__file__).resolve().parents[1]
    bots = (root / "app/telegram/bots.py").read_text(encoding="utf-8")
    service = (root / "app/service.py").read_text(encoding="utf-8")
    settings = (root / "app/config/settings.py").read_text(encoding="utf-8")
    for label in (
        "🧪 اختبار صفقة",
        "🧪 اختبار تحديث أرباح",
        "🧪 اختبار تقرير يومي",
        "🧪 اختبار تقرير أسبوعي",
    ):
        assert label in bots
    assert "async def daily_report" in service
    assert "async def weekly_report" in service
    assert "_scheduled_daily_report" in service
    assert "daily_report_enabled" in settings


def test_reports_are_private_only_and_never_broadcast():
    root = Path(__file__).resolve().parents[1]
    bots = (root / "app/telegram/bots.py").read_text(encoding="utf-8")
    block = bots[bots.index("async def send_report"):bots.index("# MARKET CLOSE")]
    assert "send_admin_report" in block
    assert "_broadcast_photo" not in block
    assert "_broadcast_text" not in block


def test_entry_activation_reply_and_waiting_state_are_exposed():
    root = Path(__file__).resolve().parents[1]
    bots = (root / 'app/telegram/bots.py').read_text(encoding='utf-8')
    service = (root / 'app/service.py').read_text(encoding='utf-8')
    messages = (root / 'app/telegram/messages.py').read_text(encoding='utf-8')
    assert 'async def send_entry' in bots
    assert 'WAITING_ENTRY' in service
    assert '✅ تم دخول الصفقة' in messages
    assert 'actual_rr_tp1' in messages


def test_public_signal_has_no_hypothetical_capital_sizing():
    messages = (Path(__file__).resolve().parents[1] / 'app/telegram/messages.py').read_text(encoding='utf-8')
    assert 'حجم الورقة' not in messages
    assert 'position_shares' not in messages
