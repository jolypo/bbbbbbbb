# FINAL REVIEW V17

## Scope
Global dual-key SAHMK routing with Tasilab final fallback.

## Verified logic
- SAHMK #1 is primary.
- If #1 reaches the configured safe daily threshold, the pool advances to SAHMK #2 before ProviderRouter considers Tasilab.
- A daily-quota 429 on #1 retries the same operation on #2.
- A key-specific HTTP 401/403 advances to #2.
- Temporary 429 does not permanently abandon the active key.
- Network and provider-wide 5xx do not permanently consume a key; existing provider fallback behavior is preserved.
- After both SAHMK keys are unavailable/exhausted, Tasilab becomes active for the Saudi day.
- New Saudi calendar day restores SAHMK #1.
- All project SAHMK data paths use the key-pool object, including generic calls, company/universe refresh, quotes and discovery ranking endpoints.
- Telegram/start/status/market/health/API diagnostics expose active provider labels only, never raw API keys.

## Verification
- pytest: 126 passed
- compileall app/tests: PASS
- render.yaml YAML parse: PASS
- `.env.example`: placeholders only

## Limitation
If SAHMK enforces quota/rate limits by account, subscription, or source IP rather than independently per API key, a second key may not provide a separate usable quota. The router will still fail safely to Tasilab.
