# FINAL REVIEW V10 — ALLUQMANU_TASI

## Verdict

**APPROVED FOR PAPER-TRADING DEPLOYMENT WITH TRANSPARENT SEARCH-COVERAGE DIAGNOSTICS.**

V10 keeps the V9 dual-horizon, catalyst-aware Saudi logic and adds user-visible evidence about how each manual scan was constructed. The system no longer hides whether Top Volume, Top Traded Value, Top Gainers, or the Catalyst/Persistent Watchlist was unavailable.

## What changed in V10

1. ProviderRouter records Stage-1 diagnostics per source: enabled/status/count/provider/error.
2. Candidate-pool cache preserves the exact diagnostics alongside cached rows.
3. Telegram search results display `FULL COVERAGE` or `DEGRADED COVERAGE` and actual selected/requested percentage.
4. Rate Limit / unavailable / fallback-degraded sources are explicitly shown.
5. Each finalist records discovery provenance from Top Volume / Top Value / Top Gainers / Watchlist plus dynamic `Acceleration`, `Persistence`, `Catalyst`, and `Persistent Leader` tags.
6. APPROVE preview shows `📍 اكتشاف` before Leadership/Entry Quality.
7. The three nearest WAIT/REJECT candidates show their discovery provenance too.
8. Full-market Yahoo fill rows are marked as `Yahoo Full-Market Fill` rather than silently blending with primary discovery rows.

## Trading logic review

V10 does **not** alter the core V9 trading thresholds merely to create more signals. Leadership, Entry Quality, Persistence, Momentum Decay, Intraday/Multi-Session separation, Failed Breakout, Severe Chase, liquidity/freshness checks, R/R and limit-state handling remain intact.

## Transparency rule

`FULL COVERAGE` requires both:
- the requested candidate count to be filled; and
- every enabled core Stage-1 channel (Volume / Value / Gainers) to have completed successfully.

A scan can therefore have 100/100 candidate rows but still show `DEGRADED COVERAGE` when Top Gainers failed. This is intentional: row count and source quality are different facts.

## Automated verification

- `pytest -q`: **102 passed**.
- `python -m compileall -q app tests`: PASS.
- `render.yaml`: YAML parse PASS.
- `.env`: excluded from release.
- obvious secret-literal scan: PASS.

## Remaining production limitation

Live behavior still depends on SAHMK/Tasilab/Saudi Exchange availability, account quotas and delayed-data freshness. V10 makes those discovery degradations visible; it cannot make an unavailable provider endpoint available.
