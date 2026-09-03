# V22 Changelog — Dual Intraday Logic + Breadth Exhaustion Chain

## Intraday menu
- `⚡ تداول يومي` now opens two distinct engines:
  - `🛡️ الجودة الأساسية` — existing conservative structural/quality engine.
  - `🚀 صائد القادة` — Emerging Leader Hunter.
- Each engine has 25 / 50 / 100 / full-market search choices.

## Emerging Leader Hunter
- Keeps the existing Judge/risk/liquidity/anti-chase gates.
- Adds discovery emphasis on relative strength vs TASI, scan-to-scan acceleration, persistence, traded value, and large-mover leadership.
- Adds 15m/60m/Daily timeframe consensus during deep analysis.
- Separates leadership from entry execution using `EXECUTABLE`, `WAIT_PULLBACK`, `NO_CHASE`, `WATCH`, `REJECT` diagnostics.
- Near-limit/limit-up stocks remain visible as leaders but are not auto-approved merely because they are strong.
- Uses strategy id `SAUDI_INTRADAY_EMERGING_LEADER` for learning/report separation.

## Breadth fallback hardening
- `0/0` remains invalid/unavailable, never neutral breadth.
- Recovery continues through existing routed summary/Mubasher/Tasilab status/cache/Tasilab full-market paths.
- Adds throttled `YAHOO_FULL_MARKET` as the final provider fallback when all cheaper paths fail.
- Same minimum sample/coverage rules apply; no fabricated breadth.

## Validation
- Full suite: 149 tests passed.
