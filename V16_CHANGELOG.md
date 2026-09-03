# V16 Changelog

- Dynamic Telegram trade-update image generated from the live trade payload.
- Profit/loss SAR is normalized to exactly one share: current_price - actual_entry.
- Every +1% profit milestone is one Photo+Caption message and a strict reply to the original signal.
- Periodic open-trade updates are one Photo+Caption message and a strict reply to the original signal.
- TP1/TP2/TP3 use the same dynamic image path; static sample profit-card values are no longer used for live target updates.
- Periodic text now shows one-share SAR P/L.
- No standalone fallback if the original signal message id is unavailable.

Verification: 121/121 tests passed; compileall passed.
