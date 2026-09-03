# FINAL REVIEW V21

Release focus: provider-health correctness and reliable Saudi market breadth.

## Fixed

1. SAHMK local counter vs IP-daily-limit contradiction.
2. Tasilab `/v1/market/status` now drives advancing/declining before full-market fallback.
3. Single-quote 502 circuit no longer blocks market/status.
4. Tasilab request accounting distinguishes attempts, successes and last-60-second rate.
5. Breadth remains unavailable rather than fabricated when all validated sources fail.
6. V20 single-SA​HMK policy remains unchanged: SAHMK -> Tasilab.
7. Existing V18/V19/V20 search, deep-analysis, report and dynamic trade-card behavior retained.

## Validation

- pytest: 144 passed
- compileall: PASS
- render.yaml YAML parse: PASS
- secret scan for known exposed credentials: PASS (0 hits)
