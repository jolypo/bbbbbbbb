# FINAL V25 CHECKLIST

| البند | الحالة |
|---|---|
| WASEEM 20 unified engine | PASS |
| Official opening-auction window 09:30–10:00 | PASS |
| Pre-open WAIT-only guard | PASS |
| Auction missing-data transparency | PASS |
| Unified horizon selection | PASS |
| WAIT full price plan | PASS |
| Pullback/retest anti-chase entry | PASS |
| Persistent scanner enable/disable | PASS |
| Legacy auto scanners disabled when WASEEM starts | PASS |
| TRADE_READY still requires manual confirmation | PASS |
| Paper Trading only | PASS |
| RTL WASEEM admin alert | PASS |
| Existing regression suite | PASS |
| New V25 tests | PASS |
| compileall | PASS |
| render.yaml parse | PASS |
| Source secret-pattern scan | PASS |

## LIMITATIONS
- SAHMK/Tasilab interfaces currently used by the project do not guarantee auction indicative price, matched auction volume, or order imbalance fields. WASEEM reports these as unavailable rather than inferring them.
- Historical 15m data remains Yahoo in this release.
- Real-time order book/depth is not available with the current configured data products.
- No strategy can guarantee 100% target accuracy; V25 is designed for forward Paper Trading measurement.
