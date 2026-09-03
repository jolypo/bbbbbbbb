# Render Deploy Checklist — V10

- [ ] Upload/push the V10 ZIP contents; do not upload `.env`.
- [ ] Keep `PAPER_MODE=true`.
- [ ] Configure the four Telegram bot tokens and Telegram IDs in Render Environment.
- [ ] Configure `SAHMK_API_KEY`; configure `TASILAB_API_KEY` if available.
- [ ] `DATABASE_URL` remains optional; without it the project uses JsonStore/ephemeral files.
- [ ] Confirm startup log includes `[startup] news bootstrap ...`; `ok=False` is allowed when the official page is dynamic/unparseable and must not crash startup.
- [ ] Check `📰 الأخبار والمحـفزات`: review `صحة المصدر`, verified-time items and display-only items.
- [ ] Send `/start` once after deployment to refresh the V10 keyboard.
- [ ] Verify main Trading menu separates `⚡ تداول يومي` and `📅 متعدد الجلسات`.
- [ ] Verify Search 25 log shows `selection=volume+value+gainers+watch` and candidate-pool counts.
- [ ] Run Intraday Search 25, then Multi-Session Search 25 within 180s and confirm a Stage-1 cache-hit log when the watchlist key is unchanged.
- [ ] Verify `📂 الصفقات المفتوحة` separates daily/multi/all.
- [ ] Verify Daily and Weekly reports are delivered only to the admin private chat after explicit request.
- [ ] Verify no scheduled Daily/Weekly report appears in group/channel after market close.
- [ ] Verify Full Market logs actual universe and Stage-1 coverage.
- [ ] Check SAHMK usage after searches; V10 adds Top Gainers but reuses a short Stage-1 cache to protect Free quota.
- [ ] Export Learning Memory before redeploy if local ephemeral history matters.
