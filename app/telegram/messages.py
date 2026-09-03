from datetime import datetime
from zoneinfo import ZoneInfo


def _fmt(v,d=2):
    try:return f"{float(v):.{d}f}"
    except:return "—"

def _time_ar(value):
    if not value:return "—"
    try:
        dt=datetime.fromisoformat(str(value).replace("Z","+00:00")).astimezone(ZoneInfo("Asia/Riyadh"))
        return dt.strftime("%d-%m-%Y %H:%M")
    except:return str(value)

def _probability(t):
    if t.get("probability_status")=="VALIDATED":
        return (
            f"📊 احتمالية الأداء التجريبية: {_fmt(t.get('probability'),1)}% | "
            f"العينات المشابهة: {t.get('probability_samples',0)}\n"
            "🧪 مبنية على نتائج Paper Trading المشابهة وليست ضمانًا"
        )
    return (
        f"📊 الاحتمالية: غير موثقة بعد | العينات: {t.get('probability_samples',0)}\n"
        "🧪 لا تعرض نسبة موثقة قبل اكتمال العينة المطلوبة"
    )


def _horizon_label(t):
    h=t.get("trade_horizon")
    return "⚡ تداول يومي" if h == "intraday" else "⏭️ فرصة 1–2 جلسة" if h == "two_day" else "📅 متعدد الجلسات"

def _duration_label(t):
    h=t.get("trade_horizon")
    return "نفس الجلسة" if h == "intraday" else "1–2 جلسة تداول" if h == "two_day" else "2–5 جلسات تداول"


def _rtl(text):
    """Prefix each non-empty line with RLM so mixed Arabic/numbers render RTL more consistently in Telegram."""
    rlm = "\u200f"
    return "\n".join((rlm + line if line else line) for line in str(text).splitlines())

def signal_message(t):
    """Public message: useful but intentionally shorter than the private preview."""
    reasons="\n".join(f"• {x}" for x in t.get("reasons",[])[:6]) or "• توافق الشروط الفنية المطلوبة"
    invalid="\n".join(f"• {x}" for x in t.get("invalidation_reasons",[])[:4])
    return _rtl(
        "🚨 فرصة تداول ورقية جديدة\n\n"
        f"السهم: {t.get('name','—')}\nالرمز: {t.get('symbol','—')}\n\n"
        f"🧭 المسار: {_horizon_label(t)}\n"
        f"🧭 نوع الصفقة: {t.get('trade_type','—')}\n"
        f"⏳ المدة المتوقعة: {_duration_label(t)}\n\n"
        f"💰 منطقة الدخول: {_fmt(t.get('entry_low'))} – {_fmt(t.get('entry_high'))}\n"
        f"🛑 وقف الخسارة: {_fmt(t.get('sl'))}\n"
        f"🎯 TP1: {_fmt(t.get('tp1'))} | TP2: {_fmt(t.get('tp2'))} | TP3: {_fmt(t.get('tp3'))}\n\n"
        f"✅ الحالة النهائية: {t.get('decision_state','TRADE_READY')}\n"
        f"🔥 Leadership: {_fmt(t.get('leadership_score'),1)}/100\n"
        f"💵 Money Flow: {_fmt(t.get('money_flow_score'),1)}/100\n"
        f"🏗 Structure: {_fmt(t.get('structure_score'),1)}/100\n"
        f"🎯 Entry Quality: {_fmt(t.get('entry_quality_score'),1)}/100\n"
        f"🧭 Target Feasibility: {_fmt(t.get('target_feasibility_score'),1)}/100\n"
        f"🛡 Risk Quality: {_fmt(t.get('risk_score'),1)}/100\n"
        f"⏱ Persistence: {_fmt(t.get('persistence_score'),1)}/100\n"
        f"🏹 Hunter: {_fmt(t.get('hunter_score',t.get('score')),1)}/100\n"
        f"⚖️ Judge: {_fmt(t.get('judge_score',t.get('score')),1)}/100 | المطلوب: {_fmt(t.get('required_score'),1)}\n"
        f"✅ القرار: {t.get('judge_decision','APPROVE')}\n"
        f"📈 Market Quality: {t.get('market_state',t.get('market_regime','—'))}\n"
        f"💧 السيولة: {t.get('liquidity_state','—')}\n"
        f"🌪 التذبذب: {t.get('volatility_state','—')}\n"
        f"🏦 القطاع: {t.get('sector','غير متاح')}\n"
        f"📰 Catalyst: {t.get('catalyst_impact','NONE')} ({float(t.get('catalyst_score',0) or 0):+.1f})\n"
        f"🚦 Limit State: {t.get('limit_state','NORMAL')}\n"
        f"⚖️ R/R: 1 : {_fmt(t.get('rr_tp1'))}\n"
        f"🕒 وقت القرار: {_time_ar(t.get('decision_time',t.get('discovered_at')))}\n"
        f"📡 آخر بيانات مستخدمة: {_time_ar(t.get('data_cutoff',t.get('historical_updated_at')))}\n"
        f"🧠 Learning Adjustment: {float(t.get('learning_adjustment',0) or 0):+.2f}\n\n"
        f"📌 أسباب الترشيح:\n{reasons}\n\n"
        f"⚠️ يبطل السيناريو عند:\n{invalid}\n\n"
        "🟡 الحالة بعد النشر: WAITING_ENTRY\n"
        "لن تعتبر الصفقة مفتوحة حتى يلمس السعر منطقة الدخول.\n\n"
        f"{_probability(t)}\n\n"
        "⚠️ تداول ورقي فقط — لا يوجد تنفيذ حقيقي"
    )


def preview_message(t):
    inds=t.get("indicators",{}) or {}
    reasons="\n".join(f"✅ {x}" for x in t.get("reasons",[])[:12]) or "—"
    jreasons="\n".join(f"• {x}" for x in t.get("judge_reasons",[])[:10]) or "• لا توجد ملاحظات إضافية"
    blockers="\n".join(f"• {x}" for x in t.get("judge_blockers",[])[:8]) or "• لا توجد موانع"
    return _rtl(
        "🔎 معاينة فرصة قبل النشر\n\n"
        f"السهم: {t.get('name','—')} ({t.get('symbol','—')})\n"
        f"🧭 المسار: {_horizon_label(t)}\n"
        f"🧭 نوع الصفقة: {t.get('trade_type','—')}\n"
        f"⏳ المدة: {_duration_label(t)}\n\n"
        "━━━━━━━━━━━━━━\n🏹 HUNTER\n━━━━━━━━━━━━━━\n"
        f"القرار: BUY/WATCH Candidate\n"
        f"Leadership: {_fmt(t.get('leadership_score'),1)}/100 | Entry: {_fmt(t.get('entry_quality_score'),1)}/100 | Persistence: {_fmt(t.get('persistence_score'),1)}/100\n"
        f"Hunter Score: {_fmt(t.get('hunter_score',t.get('score')),1)}/100\n"
        f"Structure: {inds.get('structure_state','—')}\n"
        f"RSI14: {_fmt(inds.get('rsi14'),1)} | ADX14: {_fmt(inds.get('adx14'),1)}\n"
        f"RVOL: {_fmt(inds.get('time_adjusted_rvol',inds.get('relative_volume')),2)}×\n"
        f"Breakout: {'نعم' if inds.get('is_breakout',0)>=.5 else 'لا'} | Retest: {'مؤكد' if inds.get('retest_confirmed',0)>=.5 else 'غير مؤكد/غير مطلوب'}\n\n"
        f"{reasons}\n\n"
        "━━━━━━━━━━━━━━\n⚖️ JUDGE\n━━━━━━━━━━━━━━\n"
        f"القرار النهائي: {t.get('judge_decision','APPROVE')}\n"
        f"الحالة النهائية: {t.get('decision_state','TRADE_READY')}\n"
        f"Money Flow: {_fmt(t.get('money_flow_score'),1)}/100 | Structure: {_fmt(t.get('structure_score'),1)}/100\n"
        f"Target Feasibility: {_fmt(t.get('target_feasibility_score'),1)}/100 | Risk: {_fmt(t.get('risk_score'),1)}/100\n"
        f"Judge Legacy: {_fmt(t.get('judge_score'),1)}/100\n"
        f"Required Score: {_fmt(t.get('required_score'),1)}/100\n"
        f"Market Quality: {t.get('market_state','—')}\n"
        f"Liquidity: {t.get('liquidity_state','—')}\n"
        f"Volatility: {t.get('volatility_state','—')}\n"
        f"Data Quality: {t.get('data_quality','—')}\n"
        f"Confirmed Setup: {t.get('confirmed_setup','—')}\n"
        f"Sector: {t.get('sector','غير متاح')}\n"
        f"Catalyst: {t.get('catalyst_impact','NONE')} ({float(t.get('catalyst_score',0) or 0):+.1f}) | Limit: {t.get('limit_state','NORMAL')}\n"
        f"Momentum Decay: {_fmt(t.get('momentum_decay'),2)}\n"
        f"Learning Adjustment: {float(t.get('learning_adjustment',0) or 0):+.2f}\n\n"
        f"أسباب Judge:\n{jreasons}\n\n"
        f"الموانع:\n{blockers}\n\n"
        "━━━━━━━━━━━━━━\n💰 مستويات الصفقة\n━━━━━━━━━━━━━━\n"
        f"الدخول: {_fmt(t.get('entry_low'))} – {_fmt(t.get('entry_high'))}\n"
        f"SL: {_fmt(t.get('sl'))}\nTP1: {_fmt(t.get('tp1'))}\nTP2: {_fmt(t.get('tp2'))}\nTP3: {_fmt(t.get('tp3'))}\n"
        f"R/R: 1 : {_fmt(t.get('rr_tp1'))}\n\n"
        "إذا وافقت: تُنشر الفرصة كـ WAITING_ENTRY، ولا تعتبر OPEN إلا بعد لمس منطقة الدخول."
    )


def entry_message(t, price, when=None):
    return (
        "✅ تم دخول الصفقة\n\n"
        f"السهم: {t.get('name','—')}\nالرمز: {t.get('symbol','—')}\n"
        f"💰 سعر التنفيذ الورقي: {_fmt(price)}\n"
        f"📍 منطقة الدخول: {_fmt(t.get('entry_low'))} – {_fmt(t.get('entry_high'))}\n"
        f"⚖️ R/R الفعلي إلى TP1: 1 : {_fmt(t.get('actual_rr_tp1', t.get('rr_tp1')))}\n"
        "🟢 الحالة: OPEN\n"
        f"🕒 وقت التفعيل: {_time_ar(when or t.get('entry_time'))}"
    )


def expired_entry_message(t):
    return (
        "⌛ انتهت فرصة الدخول دون تنفيذ\n\n"
        f"السهم: {t.get('name','—')} ({t.get('symbol','—')})\n"
        f"منطقة الدخول: {_fmt(t.get('entry_low'))} – {_fmt(t.get('entry_high'))}\n"
        "الحالة: MISSED_ENTRY / EXPIRED\n"
        "لم تُحسب كصفقة مفتوحة أو نتيجة ربح/خسارة."
    )


def profit_message(t,price,delta,milestone_pct=None):
    pct=(price-float(t["entry"]))/float(t["entry"])*100
    milestone = ""
    if milestone_pct is not None:
        milestone = f"🎉 تم تجاوز مستوى ربح +{float(milestone_pct):g}%\n"
    return (
        "🟢 تحديث الأرباح\n\n"
        f"{t['name']} — {t['symbol']}\n"
        f"{milestone}"
        f"الدخول الفعلي: {_fmt(t['entry'])}\n"
        f"السعر الحالي: {_fmt(price)}\n"
        f"الحركة: {delta:+.2f} ريال\n"
        f"الربح الحالي: {pct:+.2f}%\n"
        "الحالة: OPEN"
    )

def loss_message(t,price):
    pct=(price-float(t["entry"]))/float(t["entry"])*100
    return f"🔴 وقف الخسارة تحقق\n\n{t['name']} — {t['symbol']}\nالدخول: {_fmt(t['entry'])}\nالخروج: {_fmt(price)}\nالنتيجة: {pct:+.2f}%\nالحالة: مغلقة"

def near_sl_message(t,price):
    stop=t.get("trailing_stop") or t.get("sl")
    return f"⚠️ اقتراب من وقف الخسارة\n\nالسهم: {t['name']} ({t['symbol']})\nالسعر الحالي: {_fmt(price)}\nوقف الخسارة الفعّال: {_fmt(stop)}\nالحالة: OPEN"

def tp_message(t,tp_name,price):
    pct=(price-float(t["entry"]))/float(t["entry"])*100
    names={"TP1":"الهدف الأول","TP2":"الهدف الثاني","TP3":"الهدف الثالث"}
    return f"🎯 {names.get(tp_name,tp_name)} تحقق\n\nالسهم: {t['name']} ({t['symbol']})\nالدخول: {_fmt(t['entry'])}\nالسعر: {_fmt(price)}\nالربح: {pct:+.2f}%\nالحالة: {names.get(tp_name,tp_name)} تحقق"


def signal_caption(t):
    return (
        "🚨 فرصة تداول ورقية جديدة\n\n"
        f"السهم: {t.get('name','—')} ({t.get('symbol','—')})\n"
        f"🧭 {_horizon_label(t)} | {_duration_label(t)}\n"
        f"💰 الدخول: {_fmt(t.get('entry_low'))} – {_fmt(t.get('entry_high'))}\n"
        f"🛑 SL: {_fmt(t.get('sl'))}\n"
        f"🎯 TP1: {_fmt(t.get('tp1'))} | TP2: {_fmt(t.get('tp2'))} | TP3: {_fmt(t.get('tp3'))}\n"
        f"🏹 Hunter {_fmt(t.get('hunter_score',t.get('score')),1)} | ⚖️ Judge {_fmt(t.get('judge_score',t.get('score')),1)}/{_fmt(t.get('required_score'),1)}\n"
        "🟡 WAITING_ENTRY — Paper Trading"
    )


def time_exit_message(t):
    result_pct = float(t.get("result_pct") or 0.0)
    horizon = _horizon_label(t)
    reason = str(t.get("time_exit_reason") or "TIME_EXIT")
    reason_ar = {
        "INTRADAY_SESSION_END": "إغلاق التداول اليومي بنهاية الجلسة",
        "MULTI_SESSION_MAX_HORIZON": "انتهاء الحد الأقصى للصفقة متعددة الجلسات",
        "STARTUP_INTRADAY_RECONCILIATION": "تسوية صفقة يومية قديمة بعد إعادة تشغيل السيرفر",
    }.get(reason, reason)
    icon = "🟢" if result_pct > 0 else "🔴" if result_pct < 0 else "⚪"
    return (
        f"{icon} إغلاق زمني للصفقة الورقية\n\n"
        f"السهم: {t.get('name','—')} ({t.get('symbol','—')})\n"
        f"🧭 المسار: {horizon}\n"
        f"الدخول: {_fmt(t.get('entry'))}\n"
        f"الخروج: {_fmt(t.get('exit'))}\n"
        f"النتيجة الصافية التقديرية: {result_pct:+.2f}%\n"
        f"السبب: {reason_ar}\n"
        f"الحالة: {t.get('status','CLOSED_TIME_EXIT')}"
    )
