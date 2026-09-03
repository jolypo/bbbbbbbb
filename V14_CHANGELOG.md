# V14 Changelog

- Fixed public Telegram signal duplication.
- Approved public signal now sends exactly one detailed text message.
- Static `trade_card.png` is no longer attached to live public signals because it contains sample/baked-in values that can contradict the approved trade payload.
- The detailed `signal_message(signal)` payload is the single source of truth for symbol, entry, SL, TP1/TP2/TP3, scores, horizon and status.
- Added regression test ensuring public signal path has one `send_message`, no `send_photo`, and no reply-chain duplication.
- Full suite: 114 passed.
