# V20 CHANGELOG

- Removed `SAHMK_API_KEY_2` and the entire SAHMK key-pool implementation.
- Provider order is now strictly `SAHMK -> Tasilab`.
- Removed second-key labels/counters from startup, status, health and API-usage diagnostics.
- Daily/weekly report images now use the approved original visual templates and overlay live metrics dynamically.
- Report tables include live trades for the selected day/week.
- Report performance and one-share SAR P&L are computed from actual report trades, not static zero placeholders.
- Added regression tests for single-key provider behavior and dynamic report templates.
