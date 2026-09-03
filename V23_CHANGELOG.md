# V23 Changelog

- Added `🛰️ تشغيل مراقب القادة` and `⏹️ إيقاف مراقب القادة` to the intraday menu.
- Added one-day, admin-enabled Emerging Leader monitoring every 30 minutes during 10:30–14:50 KSA.
- Automatic monitor performs discovery only; every APPROVE still requires manual Telegram confirmation before any Paper Trade is published.
- Prevented automatic scans from replacing an existing pending confirmation.
- Added concise private monitor summaries when no APPROVE exists and private APPROVE preview when a trade candidate exists.
- Added Exceptional Leader discovery override so an >=8% mover with >=6pp RS does not disappear solely due to low absolute traded value.
- Kept final liquidity, R/R, anti-chase, limit-state, Entry Quality and Judge gates unchanged.
- Added persistent monitor state compatible with both JSON and SQL stores; state expires logically at the next Saudi day.
- Added V23 regression tests for exceptional-leader discovery, one-day enable/disable behavior, private-only monitoring and pending-confirmation protection.
