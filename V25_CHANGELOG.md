# V25 CHANGELOG — WASEEM 20

## الهدف
إعادة تصميم تجربة الصيد للسوق السعودي حول محرك واحد دائم بدل إجبار المستخدم على البحث اليدوي المتكرر بين اليومي/اليومين/متعدد الجلسات.

## التغييرات
- إضافة `app/strategy/waseem20.py`.
- محرك موحّد يختار تلقائيًا: نفس الجلسة / 1–2 جلسة / 2–5 جلسات.
- تشغيل اختياري دائم حتى يوقفه المستخدم، وليس مفتاح يوم واحد.
- Scan كل 15 دقيقة داخل 09:30–14:50 بتوقيت الرياض.
- دعم Opening Auction 09:30–10:00 كمرحلة intelligence/WAIT فقط.
- لا يتم اختراع بيانات المزاد: indicative price/volume/imbalance تظهر UNAVAILABLE إذا لم يرسلها المزود.
- الأخبار/المحفزات تدخل Stage-1 وقرار WASEEM.
- WAIT لم يعد يختفي: يرسل خطة Entry/SL/TP1/TP2/TP3 + timestamps + scores + blockers.
- Anti-chase صار يخطط pullback/retest anchor بدل مطاردة السعر.
- TRADE_READY فقط بعد 10:00 وعند اجتياز flow/leadership/structure/entry/target/risk.
- عند تشغيل WASEEM 20 يتم تعطيل الـauto scanners القديمة لمنع سباق/تكرار الفحوصات.
- المحركات السابقة بقيت للمقارنة تحت قائمة Legacy ولا تعمل قبل الافتتاح.
- RTL عبر RLM في رسالة WASEEM المفصلة.
- SignalEngine يستخدم WASEEM pullback anchor عند بناء الصفقة النهائية.

## الاختبارات
- مزاد بلا حقول لا يختلق بيانات.
- حقول مزاد حقيقية تستخدم فقط إذا كانت موجودة في payload.
- Pre-open لا يصبح TRADE_READY.
- Setup قوي يمكن أن يصبح TRADE_READY أثناء التداول.
- Leader ممدود يتحول WAIT مع Entry أقل من السعر الحالي.
- اختيار أفق multi-session تلقائيًا عند وجود daily persistence.
