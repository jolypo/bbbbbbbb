# V18 Changelog — Deep Analysis Data Recovery

## Fixed
- Deep-analysis finalists are no longer discarded immediately when the first detailed-quote refresh is missing or stale.
- Recovery order is now: normal router detail quotes -> monitor quote path (Tasilab-first) -> already-fresh Stage-1 quote.
- Stage-1 fallback is accepted only if the existing project freshness validator passes; no synthetic price is created.
- Manual scan output no longer claims that no trade exists when every finalist was skipped because required data was unavailable.
- Incomplete coverage is no longer rounded to 100%; e.g. 271/272 is displayed as 99.6% and remains DEGRADED COVERAGE.
- Added recovery diagnostics showing how many detailed quotes were recovered and how many remained unresolved.

## Safety / trading logic
- Hunter/Judge, risk, RR, entry, TP/SL, manual confirmation, and Paper Trading rules were not relaxed.
- Recovery only restores a valid current price input. Historical/technical requirements still have to pass normally.
- If data remains unusable after the recovery chain, the candidate is still skipped rather than guessed.
