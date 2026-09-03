# V13 Changelog — Market Core Fallback + 10:15 Warm-up

## Market-data resilience
- SAHMK remains the primary market provider.
- Mubasher remains the preferred source for market-wide total volume and traded value when available.
- If the primary market summary raises an exception or returns an invalid/zero TASI level, Mubasher now supplies TASI level and change percentage as a safety fallback.
- Valid primary TASI/change values are never overwritten by Mubasher.
- A 0/0 advancers/decliners payload during an active session is treated as unavailable, not neutral breadth.
- Breadth is never fabricated; Mubasher breadth is used only if explicit breadth fields are actually present.
- Market status Telegram output now identifies the source for TASI/change and the source for volume/value separately.

## 10:15 market-data warm-up
- Added MARKET_DATA_START=10:15.
- Scheduler primes market context once per Saudi trading session from 10:15 onward.
- Warm-up creates no signal, preview, or trade.
- New-entry/manual signal window remains 10:30–14:50 to preserve the opening-noise protection.

## Verification
- 113 automated tests passed.
- Python compileall passed.
- render.yaml parsed successfully.
