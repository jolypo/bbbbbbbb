# FINAL REVIEW V18

## Scope
V18 addresses the full-market multi-session case where Stage-1 had broad live coverage but all deep-analysis finalists were skipped because the separate detail quote refresh returned missing/stale data.

## Reviewed logic
1. Stage-1 candidate discovery remains unchanged.
2. Finalist detailed price recovery now follows a bounded chain:
   - ProviderRouter detail quote request (SAHMK pool and existing router behavior).
   - ProviderRouter monitor quote path, which is Tasilab-first.
   - Fresh Stage-1 quote as last price-only fallback.
3. Every recovered quote must pass the existing quote freshness validation.
4. Historical 15m/daily datasets, gap validation, bar-count rules, 60m confirmation, Hunter, Judge, liquidity/risk and manual confirmation remain mandatory.
5. If all candidates fail because of unavailable data, Telegram states that the deep analysis was incomplete and does not assert that the market had no valid trade.
6. Coverage formatting uses one decimal and requires actual complete selection for FULL COVERAGE.

## Verification
- pytest: 130 passed
- python compileall app: PASS
- render.yaml YAML parse: PASS

## Limitation
V18 cannot manufacture missing Yahoo historical candles. If both current-price recovery succeeds but required historical datasets remain unavailable or stale, the candidate is still safely skipped and the reason is shown.
