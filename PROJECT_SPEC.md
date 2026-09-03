# Project Spec — ALLUQMANU_TASI V10

## Product boundary

Saudi TASI technical-analysis and Paper Trading signal system. No broker integration and no real orders.

## Operating model

1. Admin starts the service.
2. Startup performs a best-effort News/Catalyst bootstrap; source failure must not stop service availability.
3. New-signal discovery remains **manual only** from Telegram.
4. Admin chooses either `Intraday` or `Multi-Session` before choosing Search 25/50/100/Full Market.
5. Stage-1 discovery combines Volume + Traded Value + Gainers + verified Catalyst Watchlist + prior Persistent Leaders, and records source-level diagnostics.
6. Telegram reports Stage-1 source status, actual coverage, FULL/DEGRADED quality, and candidate discovery provenance.
7. Fast Score ranks candidates but never hard-rejects the whole list.
8. Deep analysis computes technical context plus Leadership / Entry Quality / Persistence / Momentum Decay.
9. Horizon-specific Hunter and Judge return APPROVE / WAIT / REJECT.
10. APPROVE creates a **private admin preview only**.
11. Only explicit admin confirmation publishes a signal.
12. Published setup starts as `WAITING_ENTRY`; it becomes OPEN only on validated entry-zone touch.
13. Monitoring manages TP/SL and horizon-specific time exits.
14. Learning uses completed WIN/LOSS samples only.

## Horizon definitions

### Intraday
- Market data warm-up starts 10:15 Riyadh; new entry remains 10:30–14:50 Riyadh.
- 15m execution, 60m context, Daily broad context.
- Exit/reconcile after session using a fresh post-close quote only.

### Multi-Session
- 2–5 observed Saudi sessions by default.
- Daily/60m thesis; 15m entry timing.
- Multi-day RS 3D/5D/10D, sector/catalyst/overnight context.
- Session end alone is not a loss.

## Scoring separation

- Leadership Score = quality of market leadership.
- Entry Quality Score = quality/executability of current entry.
- Persistence = durability of RS across scans/days.
- Judge Score = final contextual decision; Score is not empirical Probability.

## News safety

- Saudi Exchange announcements page is the primary configured source.
- Startup attempts retrieval at any server start time.
- Unknown publication time => display-only, zero trading score, excluded from catalyst watchlist.
- Dynamic/empty official page => explicit degraded status, retain cache, never fake “no news”.
- SAHMK `/events/` is not used by default because current docs mark it Pro+.

## Telegram report policy

Daily/Weekly reports are structurally private + on-demand only. Scheduler and public broadcast paths cannot publish them.

## Safety invariants

- Paper mode only.
- Manual discovery only.
- Admin confirmation before public signal.
- Failed breakout/severe chase/stale critical data/poor actual R:R remain safety blockers.
- No fabricated market timestamp, sector data, spread, closing price or catalyst time.
- Full Market means the resolved dynamic equity universe, not a hard-coded 100/250/271 count.
