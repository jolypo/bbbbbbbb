# FINAL V9 CHECKLIST — ALLUQMANU_TASI

| Area | Result | Evidence / note |
|---|---|---|
| Paper Trading only | PASS | No broker execution added |
| Manual discovery only | PASS | Scheduler monitors/refreshes; no automatic new-signal scan |
| Intraday menu | PASS | Separate 25/50/100/Full Market path |
| Multi-Session menu | PASS | Separate 2–5 session path |
| Intraday Hunter/Judge | PASS | Horizon-aware assessment + Judge context |
| Multi-Session Hunter/Judge | PASS | Daily/60m thesis + multi-day RS + horizon context |
| Leadership separate from Entry Quality | PASS | Separate computed diagnostics and Judge inputs |
| Persistence | PASS | Scan history tracked and reused |
| Momentum Decay | PASS | Penalizes deteriorating leaders without making momentum a hard gate alone |
| Stage-1 Top Volume | PASS | Included |
| Stage-1 Top Value | PASS | Included |
| Stage-1 Top Gainers | PASS | Included; SAHMK Free endpoint |
| Stage-1 verified Catalyst Watchlist | PASS | Bounded and requires verified publication time |
| Stage-1 persistent leader watchlist | PASS | Bounded recent-leader carry-over |
| Search25 remains bounded | PASS | Candidate pool returns requested cap after dedupe |
| Stage-1 short cache | PASS | 180s default; regression-tested API-call reuse |
| Full Market dynamic universe | PASS | Existing V8 full-market coverage logic retained |
| Fast Score is ranker, not gate | PASS | Finalists filled even if preferred threshold is empty |
| TASI mildly red not global reject | PASS | Existing Saudi-native market-quality behavior retained |
| Failed Breakout | PASS | Remains safety reject when confirmed |
| Severe Chase | PASS | Remains safety reject |
| LIMIT_UP leadership vs entry | PASS | Leader can be strong while new-entry quality is rejected |
| Newly listed ±30% handling | PASS WITH DATA DEPENDENCY | Uses trustworthy metadata/override when available; never guesses from move size |
| News startup bootstrap attempt | PASS | Called on service startup |
| News refresh while running | PASS | Periodic bounded refresh |
| Saudi Exchange dynamic page handling | LIMITATION | May return no parseable rows; explicit `EMPTY_DYNAMIC_SOURCE` |
| Unknown publication time safety | PASS | Display-only; zero catalyst score/watchlist effect |
| SAHMK Events automatic fallback | NOT ENABLED | Current SAHMK docs mark `/events/` Pro+; Free project does not require it |
| Daily report to group | PASS | Structurally blocked |
| Daily report to channel | PASS | Structurally blocked |
| Weekly report to group | PASS | Structurally blocked |
| Weekly report to channel | PASS | Structurally blocked |
| Automatic private Daily/Weekly | PASS | Disabled; request required |
| Admin-private on-demand reports | PASS | Implemented |
| Open trades by horizon | PASS | Daily / Multi / All views |
| WAITING_ENTRY not OPEN | PASS | Existing state machine retained |
| MISSED_ENTRY not LOSS | PASS | Existing state machine retained |
| Learning WIN/LOSS only | PASS | Existing contamination guard retained |
| Intraday end-of-day reconciliation | PASS | Requires fresh post-close quote |
| Multi-session survives session close | PASS | Distinct sessions_held lifecycle |
| Health endpoint no market API calls | PASS | Adds cached news/horizon diagnostics only |
| `.env` excluded | PASS | No `.env` in release tree |
| obvious hardcoded secrets | PASS | source scan found none |
| pytest | PASS | 99 passed |
| compileall | PASS | app + tests compile |
| render.yaml parse | PASS | YAML parse successful |
| Real live SAHMK/Tasilab market scan in this environment | NOT TESTABLE | No user production credentials/network execution used |
| Profitability/statistical edge | NOT TESTABLE | Requires larger forward/historical sample |
