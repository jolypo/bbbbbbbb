from pathlib import Path

from app.service import TradingService


def test_profit_milestones_every_one_percent_once():
    assert TradingService._profit_milestones(0.99, set(), 1.0) == []
    assert TradingService._profit_milestones(1.01, set(), 1.0) == [1.0]
    assert TradingService._profit_milestones(3.25, {1.0}, 1.0) == [2.0, 3.0]
    assert TradingService._profit_milestones(3.25, {1.0, 2.0, 3.0}, 1.0) == []
    assert TradingService._profit_milestones(-2.0, set(), 1.0) == []


def test_default_profit_step_is_one_percent_in_settings_source():
    text = Path("app/config/settings.py").read_text(encoding="utf-8")
    assert "profit_alert_step_pct: float = 1.0" in text


def test_trade_updates_are_strict_replies_and_near_sl_is_threaded():
    text = Path("app/telegram/bots.py").read_text(encoding="utf-8")
    start = text.index("async def _broadcast_reply")
    end = text.index("# =========================================================\n    # PRIVATE ADMIN SEND", start)
    block = text[start:end]
    assert "allow_sending_without_reply=False" in block
    assert "trying non-reply fallback" not in block
    assert "missing original signal message_id" in block

    near_start = text.index("async def send_near_sl")
    near_end = text.index("# =========================================================\n    # REPORT BOT PUBLIC OUTPUT", near_start)
    near_block = text[near_start:near_end]
    assert "self._broadcast_reply" in near_block
    assert "self.send_loss(" not in near_block


def test_profit_alerts_use_dynamic_trade_image_not_static_sample():
    text = Path("app/service.py").read_text(encoding="utf-8")
    marker = "# PROFIT ALERTS — V15 EVERY +1% STEP"
    start = text.index(marker)
    end = text.index("# NEAR STOP", start)
    block = text[start:end]
    assert 'kind="PROFIT"' in block
    assert '_send_dynamic_profit_update' in block
    assert "profit_update_image" not in block
