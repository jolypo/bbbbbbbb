# FINAL REVIEW V25 — WASEEM 20

## لماذا تغير المحرك
V24 كان أفضل تشخيصيًا لكنه بقي قائمًا على ثلاثة مسارات منفصلة، وكان المستخدم مضطرًا لتكرار البحث. كما أن WAIT لم تكن تقدم دائمًا خطة سعر كاملة، ولم يكن هناك مسار pre-open صريح.

## التصميم الجديد
WASEEM 20 هو محرك واحد: Market Context → Candidate Discovery → Money Flow → Leadership/Persistence → Catalyst → 15m/60m/Daily Structure → Pullback Entry → Target Feasibility → Risk → Horizon Selection.

### Pre-open
09:30–10:00 هي مرحلة معلومات فقط. يعتمد المحرك على الأخبار/المحفزات، قائمة المراقبة، آخر سياق تاريخي، وأي حقول مزاد فعلية يعيدها المزود. لا يمكن إصدار TRADE_READY في المزاد.

### أثناء التداول
من 10:00 حتى 14:50 يختار المحرك الأفق تلقائيًا ولا يحتاج المستخدم إلى اختيار يومي/يومين/متعدد.

### WAIT
WAIT أصبح نتيجة قابلة للاستخدام وليست مجرد رفض: ترسل السعر المرصود، آخر timestamps، الأفق، جميع scores، خبر/محفز مع مصدره ووقته، بيانات المزاد المتاحة/غير المتاحة، Entry pullback zone، SL وTP1/2/3، وأسباب الانتظار.

### منع المطاردة
السهم القائد لا يختفي إذا كان ممدودًا. يتحول WAIT ويُبنى entry anchor أقرب إلى VWAP/EMA20/support أو pullback ATR بدل شراء السعر الممدود.

## مثال التصميم الذي كشف الحاجة
حركة 7200 (إم آي إس) في 1 سبتمبر 2026 مع محفز صباحي قوي هي نموذج لنوع الفرص الذي يجب أن يدخل الرادار من الأخبار + القوة النسبية + تدفق المال قبل أن تصبح المؤشرات التقليدية مثالية.

## ما لم يتغير
Telegram infrastructure، Paper Trading، confirmation flow، trade management، TP/SL monitoring، reports، providers، secrets via ENV، news fallback، Render deployment.

## نتيجة QA
الـregression suite بالإضافة إلى اختبارات V25 نجحت بالكامل عند الإصدار. راجع FINAL_V25_CHECKLIST.md للقيود والنتيجة التفصيلية.
