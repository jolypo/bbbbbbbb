# Final Review V22

## Scope
Full regression review focused on the user's repeated production observations: strong Saudi leaders not reaching the radar, breadth 0/0/unavailable behavior, and the need for two explicit intraday logic choices.

## Decisions
1. Preserved the original conservative engine unchanged as `Core Quality Engine`.
2. Added a separate `Emerging Leader Hunter` rather than weakening the original gates.
3. Strong/near-limit stocks can be leaders without being executable entries. This prevents the false choice between "ignore the stock" and "chase the stock".
4. MTF uses real available 15m history, resampled 60m, and Daily. No synthetic 5m timeframe was invented.
5. Missing breadth is context degradation, not a standalone scan veto. 0/0 is unavailable.
6. Final breadth chain can reach Yahoo only after cheaper/cleaner providers fail and is throttled.

## Release validation
- pytest: 149 passed
- compileall: PASS
- render.yaml parse: PASS
- secret scan: PASS (no real Telegram/SAHMK/Tasilab secrets committed)
