from pathlib import Path


def test_public_signal_path_is_text_only_single_message():
    service = Path('app/service.py').read_text(encoding='utf-8')
    bots = Path('app/telegram/bots.py').read_text(encoding='utf-8')

    # Confirm path must not attach the static/sample trade card.
    start = service.index('async def confirm_pending_signal')
    end = service.index('# =========================================================\n    # LEARNING MEMORY', start)
    block = service[start:end]
    assert 'signal_message(signal)' in block
    assert 'image_path=image_path' not in block

    # Public send_signal must issue one send_message and no send_photo.
    start = bots.index('async def send_signal')
    end = bots.index('# PROFIT BOT PUBLIC OUTPUT', start)
    block = bots[start:end]
    assert block.count('send_message(') == 1
    assert 'send_photo(' not in block
    assert 'reply_to_message_id' not in block
