# V10 CHANGELOG — Search Coverage Transparency

## Added

- Stage-1 per-source diagnostics for Top Volume, Top Traded Value, Top Gainers, and Catalyst/Persistent Watchlist.
- Telegram `FULL COVERAGE` / `DEGRADED COVERAGE` status.
- Actual `Coverage: selected/requested (%)` in every completed manual-search response.
- Cache indicator for reused Stage-1 snapshots.
- Candidate discovery provenance (`Top Value + Top Gainers + Acceleration + Catalyst`, etc.).
- Yahoo full-market fill provenance.
- Regression tests for full diagnostics, degraded Top-Gainers behavior, coverage text and discovery labels.

## Unchanged by design

- V9 trading thresholds and core indicators.
- Intraday vs Multi-Session logic.
- Manual-only discovery and admin confirmation.
- Private/on-demand report policy.
- Paper Trading boundary.
