# V21 Changelog — Provider Health & Breadth Hardening

- Tasilab `/v1/market/status` is now the first breadth recovery path.
- Market status bypasses the single-quote 5xx circuit while still respecting provider-wide 429 cooldown.
- Short market-status cache reduces duplicate API usage.
- Robust parsing for TASI, change, advancers, decliners, unchanged and common alternate/nested field names.
- Breadth recovery order: existing provider/Mubasher -> Tasilab market/status -> validated cache -> Tasilab full-market quotes.
- SAHMK daily/IP-limit state is exposed separately from the local request counter.
- `/api usage` no longer reports SAHMK as healthy after an IP-daily block.
- Tasilab diagnostics now expose attempts, successes, requests in the last 60 seconds, and market-status health.
- Health output distinguishes the quote circuit from market-status health.
- Full regression suite: 144/144 passing.
