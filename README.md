# WASEEM 30 — EARLY HUNTER ENGINE (V26)

**Primary engine:** WASEEM 30. **Legacy comparison:** WASEEM 20 remains available under previous engines.
WASEEM 30 is Paper Trading only and separates early abnormal-flow discovery from trade readiness, with anti-chase, persistent state transitions, data-completeness diagnostics, and internal/external liquidity context.

See `V26_CHANGELOG.md`, `FINAL_REVIEW_V26.md`, and `FINAL_V26_CHECKLIST.md`.

---

# V25 — WASEEM 20 Unified Saudi Opportunity Engine

V25 adds **WASEEM 20**, a persistent unified Saudi-market scanner that starts at the official opening auction (09:30 Riyadh), scans every 15 minutes through 14:50, and automatically chooses the best horizon (same-session / 1–2 sessions / 2–5 sessions) from one analysis pass. It sends detailed WAIT plans as well as TRADE_READY previews, uses verified catalyst/news context, and explicitly reports unavailable auction fields instead of fabricating them. Legacy engines remain available under **🧰 المحركات السابقة** for comparison.

Key operating rule: during 09:30–10:00 WASEEM 20 is intelligence/WAIT-only. It never creates TRADE_READY before continuous trading begins.

See `V25_CHANGELOG.md`, `FINAL_REVIEW_V25.md`, and `FINAL_V25_CHECKLIST.md`.

# ALLUQMANU_TASI — Saudi Trading & Paper Signal System

نظام تحليل وإشارات **Paper Trading فقط** للسوق السعودي TASI. لا ينفذ أوامر شراء/بيع حقيقية، ولا يعتمد على رأس مال شخصي.

## ما الجديد في V10

V10 تبني على V9 وتفصل بين سؤالين كانا مختلطين سابقًا: **هل السهم قائد؟** و **هل الدخول الآن جيد؟** كما تفصل فعليًا بين التداول اليومي والصفقات متعددة الجلسات.

```text
Manual Telegram Search
  -> Saudi Stage-1 Discovery
     Top Volume + Top Traded Value + Top Gainers
     + verified Catalyst Watchlist + Previous Persistent Leaders
  -> Fast Score = Ranker only
  -> Deep Analysis
  -> Leadership Score
  -> Entry Quality Score
  -> Relative-Strength Persistence / Momentum Decay
  -> Intraday Hunter/Judge OR Multi-Session Hunter/Judge
  -> APPROVE / WAIT / REJECT
  -> Private Admin Preview
  -> ✅ Publish / ❌ Cancel
  -> WAITING_ENTRY
  -> real entry-zone touch
  -> OPEN
  -> horizon-specific TP/SL/time-exit lifecycle
```

## Telegram horizons

### ⚡ تداول يومي
- 15m = execution/setup.
- 60m = context/confirmation.
- Daily = broader context.
- VWAP, opening move, intraday momentum, liquidity, RS, persistence and anti-chase receive the larger weights.
- Market-data warm-up starts **10:15 Riyadh**; new-entry window remains **10:30–14:50 Riyadh**.
- Intraday positions are reconciled after the closing auction/Trade-at-Last only when a fresh post-close quote is available; no fabricated close price.

### 📅 متعدد الجلسات
- Default horizon: **2–5 observed Saudi trading sessions**.
- Daily + 60m carry the thesis; 15m is used mainly to time entry.
- Uses multi-day relative strength (3D/5D/10D), daily structure, sector context, catalyst context and overnight/gap risk.
- A multi-session trade is **not** classified as a loss merely because one session ended.

## Leadership vs Entry Quality

Every finalist can now have separate diagnostics:

- `Leadership Score`: relative strength, traded value/liquidity, sector context, persistence and verified catalysts.
- `Entry Quality Score`: VWAP/EMA structure, breakout/retest, resistance room, ATR extension, wick/failed-breakout risk, chase and daily price-limit room.
- `Persistence Score`: whether relative strength survives across scans instead of being a short opening spike.
- `Momentum Decay`: detects a leader that is losing a meaningful portion of its move from the session peak.

A stock can therefore be a market leader but still be `REJECT`/`NO_EXECUTABLE_ENTRY` for a new entry.

## Stage-1 discovery — V10

The old Top-Volume-only blind spot is removed. Stage-1 now combines:

1. SAHMK Top Volume.
2. SAHMK Top Traded Value.
3. SAHMK Top Gainers.
4. Recent verified catalyst symbols.
5. Persistent leaders remembered from prior scans.

This is specifically designed to avoid missing a stock that starts quietly and accelerates later in the session. Stage-1 remains a **ranker**, never the final trade gate.

A short default `STAGE1_CANDIDATE_CACHE_SECONDS=180` reuses the same delayed discovery snapshot when the admin runs Intraday and Multi-Session searches back-to-back, reducing unnecessary SAHMK Free requests.

### Telegram Search Coverage Diagnostics — V10

Every manual search now explains the Stage-1 discovery quality inside the Telegram response itself:

- Top Volume status and returned count.
- Top Traded Value status and returned count.
- Top Gainers status and returned count.
- Catalyst / Persistent Watchlist status.
- `FULL COVERAGE` vs `DEGRADED COVERAGE`.
- Actual coverage count and percentage (`selected/requested`).
- Cache reuse indicator when the Stage-1 snapshot is reused.
- Each finalist/near-candidate shows `Discovery:` provenance such as `Top Traded Value + Top Gainers + Acceleration + Catalyst`.

A source failure is never hidden: Rate Limit, provider unavailability, or fallback degradation is shown while the remaining discovery channels continue safely.


SAHMK documents `/market/gainers/`, `/market/volume/` and `/market/value/` as Free endpoints:
https://www.sahmk.sa/developers/docs/stocks

## Market/technical logic retained

V10 keeps the established Saudi-native analysis instead of adding indicators for their own sake:

- EMA 9 / 20 / 50 / 200
- Session VWAP
- RSI 14
- MACD 12/26/9
- ADX 14 + DI
- ATR 14
- RVOL
- 5-bar momentum
- OBV / Accumulation-Distribution
- Bollinger extension diagnostics
- Support / resistance
- HH / HL / LH / LL
- Breakout + Hold
- Retest
- Failed Breakout
- TASI regime / breadth
- Sector strength
- Relative strength vs TASI
- execution liquidity / traded value / spread when trustworthy

Failed breakout, severe chase, stale critical data, genuinely poor execution liquidity and unacceptable R/R remain real safety blockers.

## Price-limit awareness

V10 distinguishes normal Main-Market daily limits from newly listed securities when trustworthy metadata is available. It never infers a ±30% new-listing limit merely because the stock has already moved strongly.

Saudi Exchange states that newly listed Main-Market securities (with specified exceptions) use ±30% daily limits and ±10% static limits for the first three trading days, reverting to ±10% daily limits from the fourth day.

Official reference:
https://www.saudiexchange.sa/wps/portal/saudiexchange/rules-guidance/capital-market-overview/Equities?locale=en

## News / Catalyst Engine

At service startup, including a manual start around 10:00, 10:15 or 11:00, the service immediately performs a best-effort news bootstrap and then refreshes periodically.

Primary source configured in code:
https://www.saudiexchange.sa/wps/portal/saudiexchange/newsandreports/issuer-news/issuer-announcements?locale=ar

Important V10 safety rule:

- News is context only; it can never create BUY/SELL by itself.
- A generic financial-results headline is not assumed positive.
- Corporate actions are treated as context, not automatic bullish signals.
- An announcement without a **verified publication timestamp** is display-only and cannot add a Catalyst bonus/penalty or enter the catalyst watchlist.
- The Saudi Exchange central announcements page is a dynamic portlet. If it returns HTTP 200 but no parseable announcement items, V10 reports `EMPTY_DYNAMIC_SOURCE` and retains cache instead of falsely saying that news is current.
- SAHMK provides structured `/events/`, but its current documentation marks that endpoint **Pro+**. V10 does not silently depend on a paid endpoint when the project is configured for the Free plan.

SAHMK events reference:
https://www.sahmk.sa/en/developers/docs/events

## Reports — immutable safety rule

Daily and weekly reports are **never automatically published**.

- ❌ no group Daily report
- ❌ no channel Daily report
- ❌ no group Weekly report
- ❌ no channel Weekly report
- ❌ no automatic private report
- ✅ admin private only, after explicit request

The old scheduler methods remain harmless compatibility stubs, but `scheduled_tasks()` never invokes them and `send_report()` is a private-admin safety alias only.

## Open trades

Telegram now separates:

- ⚡ المفتوحة اليومية
- 📅 المفتوحة متعدد الجلسات
- 📂 كل الصفقات المفتوحة

Slash `/open` remains a compatibility view for all open/waiting trades.

## Entry state machine

- Published signal => `WAITING_ENTRY`, not OPEN.
- Actual zone touch + valid actual R/R => OPEN.
- Completed-bar reconciliation is conservative when delayed data makes intrabar order unknowable.
- If the same activation bar intersects Entry and SL => `ENTRY_THEN_STOP_CONSERVATIVE`.
- `MISSED_ENTRY / EXPIRED` is not a LOSS and is excluded from Learning.

## Learning Memory

`data/learning_memory.json`

Learning remains bounded to approximately `-2..+2`, uses completed WIN/LOSS examples only, and cannot override hard safety gates. Intraday and Multi-Session performance can be viewed separately.

## Data providers

- **SAHMK #1**: primary market provider; Free endpoints are used conservatively.
- **Tasilab**: final secondary/degraded fallback after the configured SAHMK keys are unavailable/exhausted.
- **Yahoo**: historical/research and broad Stage-1 fallback where appropriate; not preferred over a fresher final quote.

The SAHMK Free quote endpoint is single-symbol; V10 retains quote caching and adds Stage-1 caching to protect the daily request budget.

## Saudi session assumptions

Official Saudi Exchange equity sessions:

- Opening auction: 09:30–10:00
- Continuous trading: 10:00–15:00
- Closing auction: 15:00–15:10
- Trade at Last: 15:10–15:20

Reference:
https://www.saudiexchange.sa/wps/portal/saudiexchange/rules-guidance/capital-market-overview/trading-cycle-and-times

## Render / secrets

Use Render Environment for secrets only:

- SIGNAL_BOT_TOKEN
- PROFIT_BOT_TOKEN
- LOSS_BOT_TOKEN
- REPORT_BOT_TOKEN
- TELEGRAM_CHAT_ID
- TELEGRAM_CHANNEL_ID
- TELEGRAM_ADMIN_USER_ID
- SAHMK_API_KEY
- TASILAB_API_KEY

Do not upload `.env`. `.env.example` contains placeholders/defaults only.

## V10 verification

Release verification performed locally:

- `pytest -q`: **102 passed**
- `python -m compileall -q app tests`: PASS
- `render.yaml` YAML parse: PASS
- secret-literal scan: no obvious embedded Telegram/SAHMK/Tasilab credentials
- `.env` absent from release tree

See `FINAL_V10_CHECKLIST.md` and `FINAL_REVIEW_V10.md` for exact limitations and review notes.

## V11 news fallback
Saudi Exchange remains the primary issuer-announcement source. If the official page is blocked (for example HTTP 403 from Render) or unusable, the News/Catalyst engine automatically attempts Mubasher's Saudi-market RSS feed. If RSS is also unavailable, it can use Mubasher's Saudi market-announcements page for display/cache only; headlines without a verified publication timestamp never alter Catalyst scoring. Telegram shows every provider state and the effective source. News remains context only and never generates a standalone trade.

## V13 market-data resilience
V13 treats a zero/missing primary TASI snapshot as invalid and can fall back to Mubasher for TASI level/change. Mubasher remains the market-wide volume/value source when available. Breadth is never fabricated. Market context warms from 10:15 Riyadh; new signals remain restricted to 10:30–14:50.


## V17 dual-SA H MK key routing

V17 promotes SAHMK credentials into a sequential key pool used by the entire project. The runtime order is `SAHMK #1 -> SAHMK #2 -> Tasilab` when `SAHMK_API_KEY_2` is configured; otherwise the legacy `SAHMK #1 -> Tasilab` order remains. Each SAHMK key has its own request counter and safe daily threshold. Daily quota exhaustion or a key-specific 401/403 moves to the next SAHMK key. Temporary 429, network timeouts, and provider-wide 5xx do not permanently burn a key. At the next Saudi calendar day the pool returns to SAHMK #1. Telegram `/start`, market/status/health, API-usage diagnostics and server logs expose the active provider label without revealing key material.

## V18 — Deep-analysis recovery
Wide/full-market scans now recover missing/stale finalist detail quotes through the existing secondary monitor path and, only when fresh, the Stage-1 quote. Coverage is shown precisely (for example 271/272 = 99.6%). A scan where all finalists fail for data reasons is reported as incomplete rather than falsely reported as “no trade”.


## V19 — Full release audit / market breadth / command consistency

V19 is a full release audit rather than a single-point patch. It adds self-healing market breadth (`advancers/decliners`) using this order: provider/Mubasher explicit breadth -> cached full-market breadth -> Tasilab full-market quote snapshot with minimum 65% coverage and 80 fresh samples. Breadth is never fabricated and SAHMK quota is never spent solely to recover it. Full-market scan breadth is persisted so `/market` and `/status` can reuse it.

Additional release fixes:
- Market Quality is `PARTIAL`, not `GOOD`, when breadth is missing, with a small conservative score penalty.
- `/performance` now filters open-trade counts by the requested horizon and excludes `MISSED_ENTRY` from average settled return.
- `/health` displays each configured SAHMK key budget separately without exposing key material.
- `/settings` shows provider order, 10:15 warm-up, 10:30–14:50 signal window, and breadth recovery policy.
- The obsolete Render `PROFIT_ALERT_THRESHOLDS=2,5,10,15,20` setting is removed; `PROFIT_ALERT_STEP_PCT=1.0` is explicit.
- Live signal previews no longer use the static sample trade card.
- Daily/weekly report images are generated dynamically from the same metrics as the Telegram text, removing stale dates/zero-value sample-image mismatches.
- Private reports are sent as one photo+caption message.

V19 verification: `pytest -q` 142 passed; `compileall` PASS; Render YAML parse PASS; secret-pattern scan PASS. Live SAHMK/Tasilab credentials were not exercised in local tests.


## V20 provider simplification

V20 removes the secondary SAHMK-key pool completely. The live provider order is now `SAHMK -> Tasilab`. Only `SAHMK_API_KEY` is supported. `SAHMK_API_KEY_2` must be removed from Render and is not read by the application. Daily/weekly report cards use the approved user templates as visual bases while all dates, counts, performance values, one-share SAR P&L and trade rows are rendered dynamically from the same report metrics used by Telegram captions.

## V21 provider-health hardening

V21 prioritizes Tasilab `/v1/market/status` for TASI breadth (advancers/decliners), keeps that lightweight endpoint independent from the single-quote 5xx circuit, and reports SAHMK IP-daily blocking separately from the process-local request counter. The fallback order for missing breadth is: existing summary/Mubasher -> Tasilab market/status -> validated cache -> Tasilab full-market quotes.


## V22 — Dual Intraday Logic

`⚡ تداول يومي` now exposes two explicit engines:

- `🛡️ الجودة الأساسية` — the original conservative Saudi intraday quality engine.
- `🚀 صائد القادة` — Emerging Leader Hunter for RS/acceleration/persistence/15m-60m-Daily consensus while retaining the same final Judge and anti-chase safeguards.

Breadth remains non-blocking when unavailable and now exhausts the validated fallback chain before reporting `غير متاح`.

Recommended manual scan cadence (KSA): warm-up 10:15; first eligible scan 10:30; Core Quality best checkpoints 10:45, 11:30, 12:30, 13:30, 14:15; Emerging Leader best checkpoints 10:30, 11:00, 11:30, 12:00, 12:30, 13:00, 13:30, 14:00, 14:30. New entries remain blocked after 14:50.

## V23 — Automatic Emerging-Leader Monitor

V23 adds an admin-only intraday button, `🛰️ تشغيل مراقب القادة`, under the daily-trading menu. One press enables the monitor for the current Saudi trading day only. It runs the V22 Emerging Leader Hunter every 30 minutes inside the existing 10:30–14:50 signal window using a balanced 50-name Stage-1 screen and six deep-analysis finalists. It never publishes a trade automatically: APPROVE candidates are staged privately and require the existing admin confirmation flow. The monitor will not replace an already-pending confirmation.

The companion `⏹️ إيقاف مراقب القادة` button disables the monitor immediately. The switch does not carry into a new Saudi trading day. The monitor reports whether a leader is an executable trade now or remains WATCH / WAIT_PULLBACK / NO_CHASE, with the same final Judge, liquidity, anti-chase and R/R safeguards used by the manual path.

V23 also fixes a discovery blind spot found in the two-month leader audit: an exceptional leader (roughly >=8% daily move and >=6 percentage-points RS versus TASI) is no longer removed from the *radar* solely because absolute traded value is below the preferred threshold. This is discovery-only; execution liquidity remains a hard final-Judge concern.

Default V23 monitor settings (no new Render ENV is required):

- `LEADER_MONITOR_INTERVAL_MINUTES=30`
- `LEADER_MONITOR_SCREEN_LIMIT=50`
- `LEADER_MONITOR_DETAIL_LIMIT=6`
