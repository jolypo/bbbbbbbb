# Saudi Market Research Basis — V9

V9 keeps the V8 Saudi-native calibration and adds evidence specifically supporting the new discovery, horizon and news-safety logic.

## 1. Saudi session structure

Saudi Exchange states that equity trading is Sunday–Thursday with Opening Auction 09:30–10:00, continuous trading 10:00–15:00, Closing Auction 15:00–15:10 and Trade-at-Last 15:10–15:20.

Source:
https://www.saudiexchange.sa/wps/portal/saudiexchange/rules-guidance/capital-market-overview/trading-cycle-and-times

Engineering consequence: V9 retains the manual new-entry window 10:30–14:50 and does not fabricate an intraday closing price before a fresh post-close quote is available.

## 2. Daily fluctuation limits

Saudi Exchange procedures specify ±30% Daily Limit and ±10% Static Limit for the first three trading days of newly listed Main-Market securities (subject to the published instrument exceptions), and ±10% Daily Limit from the fourth trading day onward.

Sources:
https://www.saudiexchange.sa/wps/portal/saudiexchange/rules-guidance/capital-market-overview/Equities?locale=en
https://www.saudiexchange.sa/wps/wcm/connect/80f74efa-f010-4a71-b7ad-d45e3f15b87b/Amended%2BTrading%2Band%2BMembership%2BProcedures.pdf?CACHE=NONE&ContentCache=NONE&MOD=AJPERES

Engineering consequence: `LIMIT_UP`/`NEAR_LIMIT_UP` uses trustworthy provider/listing metadata when available; V9 never guesses “new listing ±30%” merely from the size of the price move.

## 3. Stage-1 discovery inputs

SAHMK documentation marks these market endpoints Free:

- `/market/gainers/`
- `/market/volume/`
- `/market/value/`
- `/market/summary/`

Sources:
https://www.sahmk.sa/developers/docs/stocks
https://github.com/sahmk-sa/sahmk-python

Engineering consequence: the candidate pool is no longer Top-Volume-only. It combines Volume + Traded Value + Gainers, then adds bounded persistent-leader/catalyst watchlist slots. This improves detection of a stock that accelerates after a quiet opening without making percentage gain a BUY rule.

## 4. Free-plan request economics

SAHMK documents single-symbol `/quote/{symbol}/` as Free while batch `/quotes/` is Starter+ in the current SDK reference.

Source:
https://github.com/sahmk-sa/sahmk-python

Engineering consequence: V9 does not assume Free batch quotes. It keeps single-quote caching and adds a short 180-second Stage-1 discovery cache so back-to-back Intraday/Multi-Session searches can reuse the same delayed activity snapshot.

## 5. News and catalysts

Saudi Exchange provides an official Issuer Announcements surface:
https://www.saudiexchange.sa/wps/portal/saudiexchange/newsandreports/issuer-news/issuer-announcements?locale=ar

However, the central page is a dynamic `IssuerAnnouncementsV2Portlet`, so a simple HTTP fetch can return the application shell without structured announcement rows/timestamps.

SAHMK provides a structured `/events/` endpoint, but its current documentation marks Stock Events as Pro+:
https://www.sahmk.sa/en/developers/docs/events

Engineering consequence: V9 performs a best-effort official startup bootstrap, exposes source degradation explicitly, and never grants a catalyst score to an announcement whose publication timestamp is not verified. This is intentionally conservative; it prevents stale news from contaminating Hunter/Judge.

## 6. Trading interpretation

V9 does not encode:

- “TASI red = reject everyone”.
- “Stock +5% = buy”.
- “Positive headline = buy”.
- “High RVOL = high execution liquidity”.

It instead evaluates Context + Relative Strength + Persistence + Sector + Liquidity + Structure + Entry Quality + Risk.

## 7. Known research limitation

The 99 automated tests verify deterministic software/logic behaviors, not market profitability. Empirical edge still requires a sufficiently large forward Paper Trading sample and/or bias-controlled historical validation split by horizon, regime, sector, setup, liquidity and time-of-day.
