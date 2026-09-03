# FINAL V10 CHECKLIST — ALLUQMANU_TASI

| Area | Result | Evidence / note |
|---|---|---|
| V9 dual-horizon trading logic retained | PASS | Intraday + Multi-Session paths unchanged in principle |
| Top Volume discovery | PASS | Source status/count tracked |
| Top Traded Value discovery | PASS | Source status/count tracked |
| Top Gainers discovery | PASS | Source status/count tracked |
| Catalyst/Persistent Watchlist | PASS | Source status/count tracked |
| Candidate source dedupe | PASS | Existing V9 merge retained |
| Acceleration provenance | PASS | Added to candidate discovery tags when positive overlay is active |
| Persistence provenance | PASS | Added to candidate discovery tags when positive overlay is active |
| Catalyst provenance | PASS | Catalyst watch symbols explicitly distinguished |
| Persistent-leader provenance | PASS | Leader watch symbols explicitly distinguished |
| FULL COVERAGE | PASS | Requires full count + all enabled core sources OK |
| DEGRADED COVERAGE | PASS | Triggered on source failure/throttle/fallback or incomplete count |
| Coverage percentage | PASS | Telegram shows selected/requested (%) |
| Cached Stage-1 diagnostics | PASS | Cached rows reuse matching diagnostics and show Cache indicator |
| Provider fallback visible | PASS | Tasilab volume fallback and unavailable SAHMK channels are explicit |
| Search result source diagnostics | PASS | Telegram result includes per-source status |
| APPROVE preview discovery source | PASS | `📍 اكتشاف` shown |
| Nearest candidates discovery source | PASS | Top 3 near candidates show provenance |
| Full-market Yahoo fill provenance | PASS | Rows tagged `Yahoo Full-Market Fill` |
| Daily/Weekly public reports blocked | PASS | V9 rule retained |
| Paper Trading only | PASS | No broker execution added |
| pytest | PASS | 102 passed |
| compileall | PASS | app + tests |
| render.yaml parse | PASS | YAML valid |
| `.env` excluded | PASS | Release cleanup |
| obvious hardcoded secrets | PASS | Static scan |
| Live provider scan with user credentials | NOT TESTABLE | Production credentials/network not used |
