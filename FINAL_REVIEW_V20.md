# FINAL REVIEW V20

## Scope
- Removed all runtime support for a second SAHMK key.
- Provider order is now SAHMK -> Tasilab.
- Rebuilt daily/weekly report rendering around the user-approved base templates while keeping all report values dynamic.

## Provider logic
- Only `SAHMK_API_KEY` is read by Settings.
- `SAHMK_API_KEY_2` is not part of the application schema, `.env.example`, or `render.yaml`.
- The former `SahmkKeyPool` implementation was removed.
- On safe daily SAHMK exhaustion, ProviderRouter switches to Tasilab according to existing policy.
- Extra/obsolete environment variables are ignored by Pydantic Settings, so a stale `SAHMK_API_KEY_2` value would not be used; removing it from Render is still recommended for clarity.

## Reports
- Daily and weekly cards use the approved user-provided visual templates.
- Date, total trades, wins, losses, win rate, settled/waiting counts, performance, one-share SAR P&L, and table rows are dynamic.
- Report image and Telegram caption are generated from the same report metrics.
- Up to four recent period trades appear in the card table.

## Validation
- `python -m compileall app`: PASS
- `pytest -q`: 140 passed
- `render.yaml` YAML parse: PASS
- no secondary SAHMK runtime references in app code: PASS
