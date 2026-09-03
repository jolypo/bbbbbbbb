# FINAL REVIEW V24

## Executive result
V24 replaces the ambiguous `Hunter -> Judge APPROVE -> maybe no signal` outcome with a Saudi-native state machine where only `TRADE_READY` means a complete executable paper setup.

## Main defects repaired
1. **Contradictory APPROVE semantics** — a legacy Judge approval could later disappear inside signal construction. V24 records the build failure as `POST_BUILD_DROP` and does not call it a final trade.
2. **No 1–2 session engine** — added a dedicated two-day horizon instead of forcing these opportunities into intraday or five-day logic.
3. **Indicator-first bias** — final decision now separates Money Flow, Leadership, Catalyst, Structure, Entry, Target Feasibility and Risk.
4. **Leader discovery vs execution** — a strong low-liquidity/extended leader can remain visible while execution safety can still prevent a trade.
5. **Arabic readability** — core signal/preview output now carries RTL marks and exposes the important decision scores explicitly.
6. **Scanner coverage** — added a unified scanner for all three horizons, while preserving manual approval and market-aware entry windows.

## Decision model
V24 evaluates each deep-analysis finalist using horizon-specific weights. Intraday emphasizes flow and entry; two-day increases persistence/catalyst weight; multi-session shifts more weight toward leadership, daily structure and catalyst persistence.

A trade can be staged only if the state is `TRADE_READY` and final Entry/SL/Targets, probability and risk construction succeeds.

## Forward-test readiness
Signals now carry `decision_time`, `data_cutoff`, `horizon_sessions`, native decision state and component scores. This supports a month-long forward test without pretending that delayed data was real time and without using future information in the decision.

## Verification
- `pytest`: 158 passed.
- `compileall`: passed.
- Render YAML parse: passed.
- Hardcoded secret scan: passed.
