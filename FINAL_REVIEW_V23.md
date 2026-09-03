# FINAL REVIEW — V23

## Scope
Full review of the V22 intraday leader logic plus the requested automatic leader-monitor workflow.

## Key conclusions

- The automatic leader monitor is opt-in and admin-controlled.
- Default interval is 30 minutes, chosen because the available free data can be delayed ~15 minutes and a 15-minute scan would materially increase provider load with limited incremental information.
- The monitor runs only inside the existing 10:30–14:50 intraday signal window.
- An APPROVE is never auto-published. It is staged privately and uses the same confirmation flow as a manual search.
- Existing pending confirmations are never overwritten by an automatic scan.
- The V22 anti-chase design remains intact: strong leaders can be WATCH / WAIT_PULLBACK / NO_CHASE.
- The low-liquidity leader blind spot is fixed at discovery only; execution liquidity remains enforced by the final Judge.
- The core/manual intraday path and multi-session path remain unchanged.

## Validation

- pytest: 153 passed
- compileall: PASS
- render.yaml parse: PASS
- no secondary SAHMK key runtime logic reintroduced
- no live API key or Telegram token found in executable/config files

## Operational limitation
If Render is deployed without `DATABASE_URL`, the one-day monitor switch and pending-trade state use ephemeral JSON storage and can be lost on restart/redeploy. This is an existing deployment limitation, not a V23 logic failure. The monitor can simply be re-enabled after a restart.
