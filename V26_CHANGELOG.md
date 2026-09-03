# V26 CHANGELOG — WASEEM 30 EARLY HUNTER

## الهدف
تم إنشاء **WASEEM 30** كمحرك جديد مستقل، مع إبقاء **WASEEM 20** كما هو داخل قائمة المحركات السابقة للمقارنة. المشروع ما زال **Paper Trading فقط**.

## تغييرات المحرك
- إضافة `app/strategy/waseem30.py` كمحرك مستقل.
- فصل **الاكتشاف المبكر** عن **قرار الدخول**؛ `Total/Priority Score` لم يعد Hard Gate مثل شرط 72 في WASEEM20.
- مراحل القرار: `EARLY_RADAR -> BUILDING -> SETUP -> TRADE_READY -> WAIT_PULLBACK -> INVALIDATED`.
- `Move Stage`: `PRE_MOVE / EARLY_MOVE / ACTIVE_MOVE / EXTENDED / EXHAUSTION_RISK`.
- Stage-1 جديد يركز على Value/Volume acceleration وRS acceleration ويمنع جعل +3%/+5% شرطًا ضمنيًا للاكتشاف.
- إضافة Value Velocity / Value Acceleration وVolume Velocity عبر الفحوصات المحفوظة.
- الإبقاء على Time-adjusted RVOL الموجود، مع وسم البيانات التقريبية عند عدم توفر baseline أدق.
- إضافة Flow / Relative Strength Acceleration / Opening Pressure / VWAP / Compression-Expansion / Momentum families لتجنب double counting للمؤشرات المترابطة.
- إضافة `EARLY_MOMENTUM` و`PULLBACK` كنوعي دخول.
- Anti-Chase: الحركة الممدودة تتحول إلى `WAIT_PULLBACK` ولا يسمح لها بـ`TRADE_READY`.
- إضافة خريطة سيولة داخلية/خارجية من بنية الدعم/المقاومة مع Bid/Ask/Spread عند توفره.
- قاعدة `MISSING != BAD`: Bid/Ask والمزاد والخبر الناقص لا يتحول إلى صفر تلقائيًا.
- إضافة `Data Completeness Score` وحالات `AVAILABLE / UNAVAILABLE / UNKNOWN / APPROXIMATED`.
- `Catalyst` غير المؤكد يبقى Unknown/Neutral، والحركة القوية بلا خبر توسم `UNEXPLAINED_ACTIVITY`.
- كل حالة غير جاهزة تحمل سببًا صريحًا؛ لا يوجد `WAIT` بلا تفسير.

## Persistence / Metrics
- حفظ First Seen time/price/change.
- حفظ snapshot لكل سهم بين الفحوصات.
- حفظ state transitions حتى 1000 انتقال.
- حفظ Max Change After Discovery.
- Metrics: Early Catch Rate، Late Detection Rate، Average Change at First Discovery، Average Move After Discovery، TRADE_READY Conversion، WAIT-to-TRADE_READY count.
- Spam suppression يعتمد على تغير الحالة/Move Stage/Early Score/السعر/Entry/Catalyst.

## Telegram / التشغيل
- WASEEM30 أصبح المحرك الأساسي في قائمة التداول.
- WASEEM20 انتقل إلى `المحركات السابقة` ولم يحذف.
- تشغيل WASEEM30 يوقف WASEEM20/legacy auto scanners لتجنب تشغيل محركين تلقائيًا في الوقت نفسه والعكس صحيح.
- رسائل W30 تعرض Early Score، Move Stage، Entry Type، Data Completeness، والسيولة الداخلية/الخارجية.

## Config
إعدادات غير سرية جديدة:
- `WASEEM30_INTERVAL_MINUTES=15`
- `WASEEM30_SCREEN_LIMIT=300`
- `WASEEM30_DETAIL_LIMIT=20`
- `WASEEM30_OPENING_AUCTION_START=09:30`
- `WASEEM30_NEW_ENTRY_END=14:50`

لا توجد مفاتيح API أو Tokens جديدة.
