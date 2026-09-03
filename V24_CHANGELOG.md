# V24 — Saudi Native Trading Engine

## Scope
Rebuilt the trading-search decision layer while preserving Telegram transport, paper-trade confirmation, trade management, reports, provider router, news subsystem, and deployment structure.

## New trading architecture
- Added `app/strategy/saudi_native.py` as the final Saudi-market decision layer.
- Final states are now: `RADAR`, `LEADER`, `SETUP`, `TRADE_READY`, `WAIT_PULLBACK`, `NO_CHASE`, `INVALIDATED`.
- `TRADE_READY` is the only state that can stage a paper trade for admin confirmation.
- Legacy Judge remains diagnostic evidence; it is no longer the user-facing final meaning of a trade.
- Added explicit `POST_BUILD_DROP` diagnostics when a setup passes the native decision but Entry/SL/Targets/Probability/Risk cannot be built.

## Three independent horizons
- Intraday: same session.
- Two-Day: 1–2 sessions.
- Multi-session: 2–5 sessions.

## Saudi-native score stack
The final decision uses separate scores for:
- Market Context
- Money Flow / participation
- Leadership / persistence
- Catalyst
- Structure
- Entry Quality
- Target Feasibility
- Risk

Traditional EMA/RSI/MACD/ADX remain evidence/context and are not the sole trade engine.

## Saudi scanner
- Added unified admin-enabled scanner button.
- Runs intraday, 1–2 session and 2–5 session engines during the entry window.
- Never publishes automatically; `TRADE_READY` still requires manual confirmation.
- Service may stay up outside market hours while new trade creation remains market-aware.

## Telegram UX
- Added 1–2 session menus, open trades and performance views.
- Added RTL direction marks to core signal and preview messages for more consistent Arabic rendering.
- Preview includes Money Flow, Structure, Target Feasibility and Risk scores.

## Horizon lifecycle
- Intraday closes at the validated session-end reconciliation.
- Two-Day positions time-exit after two observed Saudi sessions if targets/stop did not resolve first.
- Multi-session keeps the configured max horizon (default five observed sessions).

## Compatibility
- Existing V23 leader-monitor methods are preserved for compatibility/tests.
- Existing provider fallback, news, paper trading, reports and security behavior are retained.
