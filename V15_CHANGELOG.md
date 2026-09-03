# V15 Changelog — 1% Profit Milestones + Strict Telegram Threading

## Trading update policy
- Profit milestone alert now fires once at every newly crossed positive 1% level: +1%, +2%, +3%, ...
- A delayed quote that crosses multiple whole-percent levels emits each missing milestone once.
- Existing 20-minute open-trade update remains enabled.
- TP/SL/entry/time-exit behavior remains unchanged.

## Telegram threading
- Every trade-specific public update must reply to the original published signal message.
- Removed standalone public fallback from `_broadcast_reply`; a failed/missing root reply is logged rather than posted as an unthreaded message.
- Near-stop-loss warning now uses the same strict reply path.
- Profit milestone alerts are text replies and do not use the legacy static profit image.

## Compatibility
- Existing `PROFIT_ALERT_THRESHOLDS` ENV is retained only for backward compatibility, but V15 uses `profit_alert_step_pct=1.0` by default. No new ENV value is required.
