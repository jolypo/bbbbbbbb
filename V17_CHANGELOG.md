# V17 Changelog — Dual SAHMK Key Pool

- Added optional `SAHMK_API_KEY_2`.
- Global provider order is now `SAHMK #1 → SAHMK #2 → Tasilab` when key #2 exists.
- The same key pool is used by market summary, quotes, companies/universe, discovery rankings, monitoring fallback and all generic SAHMK requests.
- Each SAHMK key maintains an independent daily counter/cooldown.
- Safe daily threshold or daily-quota 429 advances to the next SAHMK key.
- Key-specific HTTP 401/403 advances to the next key.
- Temporary 429 does not permanently discard a key.
- Network/5xx errors do not permanently discard a key; existing Tasilab one-call fallback remains.
- When all SAHMK keys are exhausted, ProviderRouter activates Tasilab for the rest of the Saudi day.
- New Saudi day resets routing to SAHMK #1.
- `/start`, market status, system health/API usage and server logs show provider order and active source without exposing credentials.
- Added `.env.example` placeholders only.
