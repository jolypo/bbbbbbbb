# ALLUQMANU_TASI V9 — Change List

1. Split Telegram and logic into Intraday and Multi-Session horizons.
2. Added separate Intraday/Multi-Session Hunter/Judge paths.
3. Added Leadership Score separate from Entry Quality Score.
4. Added scan-to-scan Relative Strength Persistence.
5. Added Momentum Decay / distance-from-leadership deterioration logic.
6. Added multi-day RS context (3D/5D/10D) for Multi-Session analysis.
7. Added daily-limit/near-limit states with newly-listed-security awareness when metadata exists.
8. Added Startup News/Catalyst Engine and periodic refresh.
9. Added strict unknown-publication-time guard: unverified news is display-only.
10. Added explicit `EMPTY_DYNAMIC_SOURCE` diagnostics for Saudi Exchange dynamic announcement pages.
11. Added Saudi Stage-1 candidate pool: Top Volume + Top Value + Top Gainers + verified catalysts + persistent leaders.
12. Added bounded Stage-1 persistence/acceleration/catalyst ranking overlays.
13. Added 180-second Stage-1 pool cache to reduce duplicate SAHMK Free requests for back-to-back horizon scans.
14. Fixed Telegram `_run_search()` horizon keyword wiring.
15. Fixed second Judge pass after Learning so V9 Leadership/Entry/Persistence/Catalyst context is preserved.
16. Removed duplicate acceleration-reason diagnostic append.
17. Split open-trades Telegram view into daily / multi-session / all.
18. Added horizon/news state to `/health` without consuming market APIs.
19. Disabled scheduler Daily/Weekly reports structurally.
20. Restricted report delivery to explicit admin-private requests only.
21. Added horizon-specific close lifecycle: intraday post-close reconciliation vs 2–5 observed sessions.
22. Added/expanded regression tests; release suite = 99 passed.
