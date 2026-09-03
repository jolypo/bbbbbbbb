# V19 Changelog

- Full release audit across providers, Telegram commands, market context, reports, and deployment configuration.
- Added market breadth recovery: explicit provider/Mubasher breadth -> cached full-market scan -> Tasilab full-market fresh snapshot.
- Persisted full-market breadth for later `/market` and `/status` commands.
- Added breadth source + coverage to `/market`.
- Market Quality now marks missing breadth as PARTIAL with a small conservative penalty.
- Fixed horizon-specific `/performance` open-trade count.
- Fixed average return / Profit Factor calculations to use settled WIN/LOSS records only.
- Enhanced `/health` with safe per-SA-HMK-key usage diagnostics.
- Enhanced `/settings` with provider chain, warm-up/signal windows, and breadth policy.
- Removed misleading legacy Render profit-threshold list; explicit `PROFIT_ALERT_STEP_PCT=1.0`.
- Live signal preview is text-only from current trade payload; no static sample-data image.
- Daily/weekly report cards are dynamic and built from the same computed metrics as report text.
- Private report delivery is one photo+caption message.
- Runtime-generated cards ignored by git.
