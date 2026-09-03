# ALLUQMANU_TASI V11 — Resilient News Fallback

- Saudi Exchange remains the primary issuer-announcement source.
- Added automatic Mubasher Saudi-market RSS fallback when Saudi Exchange is blocked, dynamic, or unusable from Render.
- Added Mubasher market-announcements page as a last-resort display/cache fallback if RSS is unavailable.
- RSS publication timestamps are preserved and can be used by Catalyst scoring.
- Last-resort page items without verified publication timestamps are display-only and cannot alter Catalyst score.
- Telegram news status now shows every provider state and the effective source.
- News remains context only; it never creates a standalone BUY/SELL.
- Provider outages never stop the trading service.
- Added regression tests for 403 -> RSS fallback and RSS failure -> display-only page fallback.
