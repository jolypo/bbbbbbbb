from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from app.service import TradingService
from app.telegram.trade_update_card import build_trade_update_card


def test_dynamic_card_profit_and_loss_are_one_share_based(tmp_path):
    trade = {"symbol":"7202", "name":"Solutions", "entry":215.10}
    profit = tmp_path / "profit.png"
    loss = tmp_path / "loss.png"
    assert Path(build_trade_update_card(trade, 217.25, str(profit), title="PROFIT UPDATE")).exists()
    assert Path(build_trade_update_card(trade, 214.80, str(loss), title="OPEN TRADE UPDATE")).exists()
    assert Image.open(profit).size == (1200, 675)
    assert Image.open(loss).size == (1200, 675)


def test_open_trade_text_contains_one_share_sar_pnl():
    service = object.__new__(TradingService)
    service.s = SimpleNamespace(near_sl_warning_pct=0.5)
    service._local_now = lambda: __import__('datetime').datetime(2026,8,31,11,41)
    trade = {
        "name":"سلوشنز", "symbol":"7202", "trade_type":"متعدد الجلسات",
        "entry":215.10, "tp1":226.30, "tp2":231.80, "tp3":238.00, "sl":208.90,
    }
    quote = SimpleNamespace(price=214.80)
    text = service._price_update_text(trade, quote)
    assert "-0.30 ر.س" in text
    assert "-0.14%" in text
    assert "سهم واحد" in text


def test_profit_message_and_periodic_update_use_dynamic_image_in_source():
    source = Path('app/service.py').read_text(encoding='utf-8')
    assert 'kind="PROFIT"' in source
    assert 'kind="OPEN"' in source
    assert '_send_dynamic_profit_update' in source
    assert 'kind="TARGET"' in source
    bot_source = Path('app/telegram/bots.py').read_text(encoding='utf-8')
    assert 'reply_to_message_id=int(reply_id)' in bot_source
    assert 'await bot.send_photo(photo=fh, caption=text, **kwargs)' in bot_source
