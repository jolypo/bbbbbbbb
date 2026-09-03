# FINAL REVIEW V9 — ALLUQMANU_TASI

## Verdict

**APPROVED FOR PAPER-TRADING DEPLOYMENT WITH DOCUMENTED NEWS-SOURCE LIMITATION.**

V9 is materially stronger than V8 in candidate discovery and decision separation. It no longer equates “market leadership” with “good entry,” and the Stage-1 discovery pool is no longer dependent on Top Volume alone.

## Trading-logic review

### PASS — Horizon separation
Intraday and 2–5 session trades use distinct assessment/Hunter/Judge context and distinct lifecycle behavior. Multi-session positions are not forced into a same-day LOSS classification.

### PASS — Leadership vs entry
Leadership, Entry Quality and Persistence are separate diagnostics. A limit-up/overextended stock can rank as a leader while being rejected for new entry.

### PASS — Dynamic leader discovery
Stage-1 uses Top Volume + Top Traded Value + Top Gainers + bounded verified catalyst/persistent-leader watchlists. Persistence and acceleration are ranking overlays, not hard BUY gates.

### PASS — Saudi market context
Mildly red TASI remains context rather than an automatic veto. Broad selloff, stale data, failed breakout, severe chase, execution-liquidity problems and poor actual R/R remain meaningful blockers.

### PASS — Price-limit handling
Normal/new-listing limits are not inferred from price momentum. Provider/listing metadata can override the normal default when trustworthy.

## Telegram review

### PASS — Reports
Daily/Weekly scheduler publication is disabled. Public `send_report()` behavior is replaced by a private-admin safety alias. Manual report requests generate/send the report to admin private only.

### PASS — Menus
Trading is separated into Intraday and Multi-Session. Open trades can be viewed by horizon or all.

### PASS — Publication safety
APPROVE still requires private admin preview and explicit confirmation before public signal publication.

## Data/provider review

### PASS — Free candidate-discovery endpoints
Current SAHMK documentation supports Free Top Gainers/Volume/Value endpoints used by V9.

### PASS — Quota protection improvement
A 180-second candidate-pool cache reduces repeated Stage-1 calls when two horizon scans are run back-to-back. Existing quote cache and daily switch protection remain.

### LIMITATION — Free single-symbol detail calls
Current SAHMK references mark batch quotes Starter+, so deep finalist refresh on Free can still consume several single-symbol requests. This is a real Free-plan budget constraint; V9 does not hide it.

## News review

### PASS — startup attempt / failure isolation
Server startup attempts the official Saudi Exchange announcement source. Failure or dynamic-page degradation does not crash the trading service.

### PASS — stale-news contamination guard
Announcement `fetched_at` is never substituted for `published_at` when deciding whether a catalyst is current. Unknown publication time = display-only, zero trading catalyst effect.

### LIMITATION — official central listing is dynamic
The Saudi Exchange announcements listing is a dynamic portlet and may return no parseable items/timestamps to a plain HTTP client. V9 exposes this as `EMPTY_DYNAMIC_SOURCE` rather than claiming success. SAHMK structured Stock Events are currently documented as Pro+, so V9 does not silently make a paid dependency mandatory.

## Security review

- PASS: no `.env` in release tree.
- PASS: `.env.example` placeholders only.
- PASS: no obvious Telegram/SAHMK/Tasilab secret literals found in source scan.
- PASS: Paper Trading boundary retained.

## Automated verification

- `pytest -q`: **99 passed**.
- `python -m compileall -q app tests`: PASS.
- `render.yaml`: parses successfully as YAML.
- Runtime cache/test bytecode are excluded from final ZIP.

## Environment note

`pip check` on the shared execution environment reports an unrelated installed MoviePy/Pillow mismatch because the host currently has Pillow 12.3.0, while this project pins `Pillow==11.3.0` and does not depend on MoviePy. This is not a conflict inside `requirements.txt`; deployment should install the project-pinned requirements in its own environment.

## What V9 does NOT claim

- It does not guarantee profitability or a win rate.
- It does not treat Score as empirical Probability.
- It does not guarantee official news ingestion when the Saudi Exchange dynamic page exposes no machine-readable announcement rows.
- It does not execute real trades.

## First-week monitoring recommendation

Watch: candidate-pool coverage/source mix, SAHMK daily request consumption, freshness rejection counts, WAIT/REJECT reasons, persistence behavior across repeated scans, and `news_source_state`. Use Paper Trading outcomes to calibrate thresholds only after enough samples; do not loosen gates merely to increase signal count.
