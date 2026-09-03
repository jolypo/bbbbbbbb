import hashlib
import hmac
import os

from telegram import Bot, Update, ReplyKeyboardMarkup
from telegram.constants import ChatType
from telegram.ext import Application, CommandHandler, MessageHandler, filters

from app.telegram.messages import (
    loss_message,
    near_sl_message,
    signal_message,
    signal_caption,
    preview_message,
    time_exit_message,
)


class TelegramBots:
    def __init__(self, settings):
        self.s = settings

        self.signal = Bot(
            settings.signal_bot_token
        )

        self.profit = Bot(
            settings.profit_bot_token
        )

        self.loss = Bot(
            settings.loss_bot_token
        )

        self.report = Bot(
            settings.report_bot_token
        )

        self.application = None
        self.service = None
        self._last_test_trade_message_id = None
        self._awaiting_learning_import = False
        self._awaiting_learning_reset = False

        self.mode = self._resolve_mode()

        self.webhook_secret = hashlib.sha256(
            settings.signal_bot_token.encode(
                "utf-8"
            )
        ).hexdigest()[:48]

    # =========================================================
    # MODE
    # =========================================================

    def _resolve_mode(self):
        configured = str(
            getattr(
                self.s,
                "telegram_mode",
                "auto",
            )
        ).strip().lower()

        if configured in {
            "polling",
            "webhook",
        }:
            return configured

        return (
            "webhook"
            if os.getenv("RENDER_EXTERNAL_URL")
            else "polling"
        )

    # =========================================================
    # SERVICE
    # =========================================================

    def attach_service(self, service):
        self.service = service

    # =========================================================
    # PUBLIC DESTINATIONS
    # =========================================================

    def _public_chat_ids(self):
        """
        الأماكن العامة التي تستقبل:
        - الإشارات
        - تحديثات الأسعار
        - TP
        - SL

        التقارير مستثناة صراحة: خاص/عند الطلب فقط.

        1) القروب
        2) القناة
        """

        destinations = []

        group_id = getattr(
            self.s,
            "telegram_chat_id",
            None,
        )

        channel_id = getattr(
            self.s,
            "telegram_channel_id",
            None,
        )

        if group_id:
            destinations.append(
                int(group_id)
            )

        if channel_id:
            channel_id = int(
                channel_id
            )

            if channel_id not in destinations:
                destinations.append(
                    channel_id
                )

        return destinations

    # =========================================================
    # PRIVATE ADMIN MENU
    # =========================================================

    def _admin_menu(self):
        """Persistent Arabic control panel for the private admin chat."""
        return ReplyKeyboardMarkup(
            [
                ["📈 التداول", "📂 الصفقات المفتوحة"],
                ["📊 التقارير", "🧠 التعلم"],
                ["⚙️ النظام", "🧪 اختبارات الرسائل"],
                ["ℹ️ المساعدة", "👤 معرفي"],
            ],
            resize_keyboard=True, is_persistent=True,
            input_field_placeholder="اختر من لوحة التحكم",
        )

    def _trading_menu(self):
        return ReplyKeyboardMarkup(
            [
                ["🧠 وسيم 30", "📈 حالة السوق"],
                ["▶️ تشغيل وسيم 30", "⏹️ إيقاف وسيم 30"],
                ["📡 حالة وسيم 30", "📰 الأخبار والمحـفزات"],
                ["🧰 المحركات السابقة", "📊 الأداء"],
                ["🛡️ المخاطر", "⬅️ رجوع للقائمة الرئيسية"],
            ],
            resize_keyboard=True, is_persistent=True, input_field_placeholder="وسيم 30 — Early Hunter Engine",
        )

    def _legacy_trading_menu(self):
        return ReplyKeyboardMarkup(
            [
                ["🧠 وسيم 20", "⚡ تداول يومي"],
                ["⏭️ فرص 1–2 جلسة", "📅 متعدد الجلسات"],
                ["🛰️ تشغيل السكان السعودي", "⬅️ رجوع للتداول"],
            ],
            resize_keyboard=True, is_persistent=True, input_field_placeholder="المحركات السابقة — Legacy",
        )

    def _waseem20_menu(self):
        return ReplyKeyboardMarkup(
            [
                ["▶️ تشغيل وسيم 20", "⏹️ إيقاف وسيم 20"],
                ["📡 حالة وسيم 20", "📈 حالة السوق"],
                ["⬅️ رجوع للتداول"],
            ],
            resize_keyboard=True, is_persistent=True, input_field_placeholder="WASEEM 20",
        )

    def _waseem30_menu(self):
        return ReplyKeyboardMarkup(
            [
                ["▶️ تشغيل وسيم 30", "⏹️ إيقاف وسيم 30"],
                ["📡 حالة وسيم 30", "📈 حالة السوق"],
                ["⬅️ رجوع للتداول"],
            ],
            resize_keyboard=True, is_persistent=True, input_field_placeholder="WASEEM 30 — EARLY HUNTER",
        )

    def _intraday_menu(self):
        return ReplyKeyboardMarkup(
            [
                ["🛡️ الجودة الأساسية", "🚀 صائد القادة"],
                ["🛰️ تشغيل السكان السعودي", "⏹️ إيقاف السكان السعودي"],
                ["⬅️ رجوع للتداول"],
            ],
            resize_keyboard=True, is_persistent=True, input_field_placeholder="اختر منطق التداول اليومي",
        )

    def _intraday_core_menu(self):
        return ReplyKeyboardMarkup(
            [
                ["🛡️ أساسي — بحث 25", "🛡️ أساسي — بحث 50"],
                ["🛡️ أساسي — بحث 100", "🛡️ أساسي — السوق كامل"],
                ["⬅️ رجوع للتداول اليومي"],
            ],
            resize_keyboard=True, is_persistent=True, input_field_placeholder="🛡️ الجودة الأساسية",
        )

    def _intraday_emerging_menu(self):
        return ReplyKeyboardMarkup(
            [
                ["🚀 قادة — بحث 25", "🚀 قادة — بحث 50"],
                ["🚀 قادة — بحث 100", "🚀 قادة — السوق كامل"],
                ["⬅️ رجوع للتداول اليومي"],
            ],
            resize_keyboard=True, is_persistent=True, input_field_placeholder="🚀 صائد القادة",
        )

    def _two_day_menu(self):
        return ReplyKeyboardMarkup(
            [
                ["⚡ يومين — بحث 25", "🎯 يومين — بحث 50"],
                ["🔭 يومين — بحث 100", "🌐 يومين — السوق كامل"],
                ["⬅️ رجوع للتداول"],
            ],
            resize_keyboard=True, is_persistent=True, input_field_placeholder="⏭️ فرص 1–2 جلسة",
        )

    def _multi_session_menu(self):
        return ReplyKeyboardMarkup(
            [
                ["⚡ متعدد — بحث 25", "🎯 متعدد — بحث 50"],
                ["🔭 متعدد — بحث 100", "🌐 متعدد — السوق كامل"],
                ["⬅️ رجوع للتداول"],
            ],
            resize_keyboard=True, is_persistent=True, input_field_placeholder="📅 متعدد الجلسات 2–5",
        )

    def _open_trades_menu(self):
        return ReplyKeyboardMarkup(
            [
                ["⚡ المفتوحة اليومية", "⏭️ المفتوحة 1–2 جلسة"],
                ["📅 المفتوحة متعدد الجلسات"],
                ["📂 كل الصفقات المفتوحة"],
                ["⬅️ رجوع للقائمة الرئيسية"],
            ],
            resize_keyboard=True, is_persistent=True, input_field_placeholder="اختر نوع الصفقات المفتوحة",
        )

    def _system_menu(self):
        return ReplyKeyboardMarkup(
            [["📡 استهلاك مزودي البيانات", "🩺 صحة النظام"], ["📡 حالة النظام", "🧪 اختبار Tasilab"],
             ["⏸️ إيقاف الإشارات", "▶️ استئناف الإشارات"], ["⚙️ الإعدادات", "⬅️ رجوع للقائمة الرئيسية"]],
            resize_keyboard=True, is_persistent=True, input_field_placeholder="النظام والمزودون",
        )

    def _learning_menu(self):
        return ReplyKeyboardMarkup(
            [["📊 حالة التعلم"], ["📤 تصدير ملف التعلم", "📥 استيراد ملف التعلم"],
             ["🗑 تصفير التعلم", "⬅️ رجوع للقائمة الرئيسية"]],
            resize_keyboard=True, is_persistent=True, input_field_placeholder="Learning Memory",
        )


    def _search_menu(self):
        return ReplyKeyboardMarkup(
            [
                ["⚡ بحث 25", "🎯 بحث 50"],
                ["🔭 بحث 100", "🌐 السوق كامل"],
                ["⬅️ رجوع للقائمة الرئيسية"],
            ],
            resize_keyboard=True,
            is_persistent=True,
            input_field_placeholder="اختر نطاق البحث اليومي",
        )

    def _api_usage_menu(self):
        return ReplyKeyboardMarkup(
            [
                ["📊 ملخص المزودين", "🧾 سجل الطلبات"],
                ["📡 استهلاك SAHMK", "📡 استهلاك Tasilab"],
                ["⬅️ رجوع للقائمة الرئيسية"],
            ],
            resize_keyboard=True,
            is_persistent=True,
            input_field_placeholder="اختر مزود البيانات",
        )
    def _reports_menu(self):
        return ReplyKeyboardMarkup(
            [
                ["🧾 تقرير يومي", "📅 تقرير أسبوعي"],
                ["⚡ أداء اليومي", "⏭️ أداء 1–2 جلسة"],
                ["📅 أداء متعدد الجلسات"],
                ["⬅️ رجوع للقائمة الرئيسية"],
            ],
            resize_keyboard=True,
            is_persistent=True,
            input_field_placeholder="اختر نوع التقرير",
        )

    def _tests_menu(self):
        return ReplyKeyboardMarkup(
            [
                ["🧪 اختبار صفقة", "🧪 اختبار تحديث أرباح"],
                ["🧪 اختبار تقرير يومي", "🧪 اختبار تقرير أسبوعي"],
                ["⬅️ رجوع للقائمة الرئيسية"],
            ],
            resize_keyboard=True,
            is_persistent=True,
            input_field_placeholder="اختبارات العرض الخاصة",
        )

    def _confirm_signal_menu(self):
        return ReplyKeyboardMarkup(
            [
                ["✅ إرسال الصفقة", "❌ إلغاء الصفقة"],
                ["⬅️ رجوع للقائمة الرئيسية"],
            ],
            resize_keyboard=True,
            is_persistent=True,
            input_field_placeholder="أكد نشر الصفقة أو ألغها",
        )

    async def _menu_reply(self, update, text):
        if update.effective_message:
            await update.effective_message.reply_text(
                text,
                reply_markup=self._admin_menu(),
            )

    # =========================================================
    # SAFE PUBLIC BROADCAST
    # =========================================================

    async def _broadcast_text(
        self,
        bot,
        text,
    ):
        """
        إرسال نص للقروب والقناة.

        فشل جهة لا يمنع الجهة الثانية.
        """

        success = 0

        for chat_id in self._public_chat_ids():

            try:

                await bot.send_message(
                    chat_id=chat_id,
                    text=text,
                )

                success += 1

            except Exception as exc:

                print(
                    "[telegram] broadcast text "
                    f"failed chat={chat_id}: "
                    f"{exc!r}"
                )

        return success

    async def _broadcast_photo(
        self,
        bot,
        image_path,
        caption=None,
    ):
        """
        إرسال صورة للقروب والقناة.
        """

        success = 0

        for chat_id in self._public_chat_ids():

            try:

                with open(
                    image_path,
                    "rb",
                ) as fh:

                    await bot.send_photo(
                        chat_id=chat_id,
                        photo=fh,
                        caption=caption,
                    )

                success += 1

            except Exception as exc:

                print(
                    "[telegram] broadcast photo "
                    f"failed chat={chat_id}: "
                    f"{exc!r}"
                )

        return success

    async def _broadcast_photo_with_ids(self, bot, image_path, caption=None):
        """Broadcast a photo and return {chat_id: message_id} for reply threading."""
        sent = {}
        for chat_id in self._public_chat_ids():
            try:
                with open(image_path, "rb") as fh:
                    msg = await bot.send_photo(chat_id=chat_id, photo=fh, caption=caption)
                sent[str(chat_id)] = int(msg.message_id)
            except Exception as exc:
                print(f"[telegram] broadcast photo/id failed chat={chat_id}: {exc!r}")
        return sent

    async def _broadcast_reply(self, bot, text, trade, image_path=None):
        """Reply strictly to the original signal message in each public destination.

        V15 intentionally does not fall back to a standalone public message.  A
        trade update without its root signal thread is more confusing than a
        missed public update; failures are logged for diagnosis instead.
        """
        success = 0
        ids = (trade or {}).get("signal_message_ids", {}) or {}
        for chat_id in self._public_chat_ids():
            reply_id = ids.get(str(chat_id)) or ids.get(chat_id)
            if not reply_id:
                print(f"[telegram] reply skipped chat={chat_id}: missing original signal message_id")
                continue
            try:
                kwargs = dict(
                    chat_id=chat_id,
                    reply_to_message_id=int(reply_id),
                    allow_sending_without_reply=False,
                )
                if image_path:
                    with open(image_path, "rb") as fh:
                        await bot.send_photo(photo=fh, caption=text, **kwargs)
                else:
                    await bot.send_message(text=text, **kwargs)
                success += 1
            except Exception as exc:
                print(f"[telegram] strict reply failed chat={chat_id} root={reply_id}: {exc!r}")
        return success

    # =========================================================
    # PRIVATE ADMIN SEND
    # =========================================================

    async def send_admin_text(
        self,
        text,
    ):
        """
        إرسال نص للمشرف في الخاص.
        """

        await self.signal.send_message(
            chat_id=int(
                self.s.telegram_admin_user_id
            ),
            text=text,
        )

    async def send_admin_report(self, text=None, image_path=None):
        """Send one private report message; image and caption share one payload."""
        admin_id = int(self.s.telegram_admin_user_id)
        if image_path:
            with open(image_path, "rb") as fh:
                await self.signal.send_photo(chat_id=admin_id, photo=fh, caption=text or None)
            return
        if text:
            await self.signal.send_message(chat_id=admin_id, text=text)

    async def send_admin_signal_preview(self, trade, prefix=None):
        """Private preview from the live payload only; never show a static sample card."""
        admin_id = int(self.s.telegram_admin_user_id)
        text = (prefix or "🟡 معاينة قبل النشر — لم تُرسل للقروب بعد\n\n") + preview_message(trade)
        msg = await self.signal.send_message(
            chat_id=admin_id,
            text=text,
            reply_markup=self._confirm_signal_menu(),
        )
        return int(msg.message_id)

    # =========================================================
    # CONNECTION TEST
    # =========================================================

    async def test(self):
        """
        اختبار البوتات الأربعة.
        """

        await self._broadcast_text(
            self.signal,
            "🟢 SIGNAL BOT — اتصال ناجح",
        )

        await self._broadcast_text(
            self.profit,
            "🟡 PROFIT BOT — اتصال ناجح",
        )

        await self._broadcast_text(
            self.loss,
            "🔴 LOSS BOT — اتصال ناجح",
        )

        await self._broadcast_text(
            self.report,
            "📊 REPORT BOT — اتصال ناجح",
        )

    # =========================================================
    # SIGNAL BOT PUBLIC OUTPUT
    # =========================================================

    async def send_signal(self, text, image_path=None, trade=None):
        """Publish each approved signal as exactly one public text message.

        ``image_path`` is accepted for backward compatibility but deliberately
        ignored.  A static/sample trade card must never be paired with a live
        signal because its baked-in symbol/prices can contradict the approved
        trade payload.  The detailed signal text is the single source of truth.
        """
        sent = {}
        for chat_id in self._public_chat_ids():
            try:
                root = await self.signal.send_message(chat_id=chat_id, text=text)
                sent[str(chat_id)] = int(root.message_id)
            except Exception as exc:
                print(f"[telegram] signal send failed chat={chat_id}: {exc!r}")
        return sent

    # =========================================================
    # PROFIT BOT PUBLIC OUTPUT
    # =========================================================

    async def send_profit(self, text, trade=None, image_path=None):
        """Profit/TP updates reply to the original signal when trade metadata exists."""
        if trade:
            return await self._broadcast_reply(self.profit, text, trade, image_path=image_path)
        if image_path:
            return await self._broadcast_photo(self.profit, image_path, caption=text)
        return await self._broadcast_text(self.profit, text)

    async def send_entry(self, text, trade):
        """Entry activation is a text Reply to the original published setup."""
        return await self._broadcast_reply(self.profit, text, trade, image_path=None)

    async def send_time_exit(self, trade):
        """Publish a final horizon/time-exit update as a reply to the original signal."""
        text = time_exit_message(trade)
        result = float((trade or {}).get("result_pct") or 0.0)
        bot = self.profit if result >= 0 else self.loss
        return await self._broadcast_reply(bot, text, trade, image_path=None)

    # =========================================================
    # LOSS BOT PUBLIC OUTPUT
    # =========================================================

    async def send_loss(
        self,
        text,
    ):
        """
        تحديثات وقف الخسارة:
        القروب + القناة
        """

        return await self._broadcast_text(
            self.loss,
            text,
        )

    async def send_loss_for_trade(
        self,
        trade,
        price,
    ):
        await self._broadcast_reply(self.loss, loss_message(trade, price), trade, image_path=None)

    async def send_near_sl(
        self,
        trade,
        price,
    ):
        """Near-stop warning must stay inside the original signal thread."""
        return await self._broadcast_reply(
            self.loss,
            near_sl_message(trade, price),
            trade,
            image_path=None,
        )

    # =========================================================
    # REPORT BOT PUBLIC OUTPUT
    # =========================================================

    async def send_report(self, text=None, image_path=None):
        """Safety alias: reports are private/admin/on-demand only.

        Kept for backward compatibility with old code/tests, but it can never
        broadcast to the group or channel.
        """
        await self.send_admin_report(text=text, image_path=image_path)
        return 1

    # =========================================================
    # MARKET CLOSE
    # =========================================================

    async def send_market_close(
        self,
        local_time_text,
    ):
        """
        إشعار إغلاق السوق:
        للمشرف في الخاص فقط.

        لا يتم نشر رسالة إغلاق السوق
        في القروب أو القناة.
        """

        text = (
            "🔔 السوق أغلق اليوم\n\n"
            f"التاريخ والوقت: "
            f"{local_time_text}\n\n"
            "📊 TASI — انتهت جلسة التداول اليوم.\n"
            "📡 البيانات: SAHMK delayed"
        )

        await self.send_admin_text(
            text
        )

    # =========================================================
    # CHAT CHECKS
    # =========================================================

    def _is_private_chat(
        self,
        update: Update,
    ):
        return bool(
            update.effective_chat
            and update.effective_chat.type
            == ChatType.PRIVATE
        )

    def _is_admin_user(
        self,
        update: Update,
    ):
        return bool(
            update.effective_user
            and int(
                update.effective_user.id
            )
            == int(
                self.s.telegram_admin_user_id
            )
        )

    # =========================================================
    # COMMAND ACCESS
    # =========================================================

    def _is_allowed_chat(
        self,
        update: Update,
    ):
        """
        جميع أوامر التحكم تعمل فقط:
        - في الخاص
        - للمستخدم الإداري المحدد

        القروب والقناة للنشر فقط.
        """

        return (
            self._is_private_chat(update)
            and self._is_admin_user(update)
        )

    async def _safe_reply(
        self,
        update,
        text,
    ):
        if update.effective_message:

            await update.effective_message.reply_text(
                text
            )

    async def _guard(
        self,
        update,
    ):
        if self._is_allowed_chat(update):
            return True

        # لا نرسل أي رد داخل القروب
        # حتى يبقى Feed نظيف.
        if not self._is_private_chat(update):
            return False

        await self._safe_reply(
            update,
            "🔒 هذا البوت غير متاح لهذا الحساب.",
        )

        return False

    # =========================================================
    # ADMIN
    # =========================================================

    async def _admin_only(
        self,
        update,
    ):
        return (
            self._is_private_chat(update)
            and self._is_admin_user(update)
        )

    # =========================================================
    # MY ID
    # =========================================================

    async def myid(
        self,
        update,
        context,
    ):
        """
        يعرض Telegram User ID
        للمستخدم في الخاص.
        """

        if (
            not self._is_private_chat(update)
            or not update.effective_user
        ):
            return

        await self._safe_reply(
            update,
            "👤 Telegram User ID الخاص بك:\n"
            f"{update.effective_user.id}",
        )

    # =========================================================
    # START
    # =========================================================

    async def start(
        self,
        update,
        context,
    ):
        if not await self._guard(update):
            return

        provider_order = "SAHMK → Tasilab"
        active_provider = "—"
        if self.service and getattr(self.service, "p", None):
            try:
                provider_order = self.service.p.provider_order_text()
                active_provider = self.service.p.active_provider_detail()
            except Exception:
                pass

        await self._menu_reply(
            update,
            "🤖 لوحة تحكم TASI KSA\n\n"
            "📊 اختر الوظيفة من الأزرار أسفل المحادثة.\n"
            "⚠️ Paper Trading فقط.\n\n"
            f"📡 مزودو البيانات: {provider_order}\n"
            f"🟢 المزود النشط الآن: {active_provider}\n\n"
            "الأوامر القديمة /signal وغيرها ما زالت تعمل احتياطًا.",
        )

    async def help(
        self,
        update,
        context,
    ):
        await self.start(
            update,
            context,
        )

    # =========================================================
    # SIGNAL COMMAND
    # =========================================================

    async def signal_command(
        self,
        update,
        context,
    ):
        if (
            not await self._guard(update)
            or not self.service
        ):
            return

        await self._safe_reply(
            update,
            "🔎 بدأ الفحص اليدوي للأسهم النشطة...\n"
            "لن تُنشأ صفقة إلا إذا اجتازت جميع الشروط.",
        )

        try:

            result = await self.service.scan_once(
                source="telegram_private"
            )

            pending = self.service.pending_signal()
            if pending:
                await self.send_admin_signal_preview(pending)
                await self._safe_reply(
                    update,
                    result + "\n\nاختر ✅ إرسال الصفقة أو ❌ إلغاء الصفقة من الأزرار.",
                )
            else:
                await self._safe_reply(update, result)

        except Exception as exc:

            print(
                "[telegram] /signal failed: "
                f"{exc!r}"
            )

            await self._safe_reply(
                update,
                "⚠️ تعذر إكمال الفحص حاليًا. "
                "راجع Render Logs.",
            )

    # =========================================================
    # MARKET
    # =========================================================

    async def market(
        self,
        update,
        context,
    ):
        if (
            not await self._guard(update)
            or not self.service
        ):
            return

        try:

            text = await self.service.market_text()

            await self._safe_reply(
                update,
                text,
            )

        except Exception as exc:

            print(
                "[telegram] /market failed: "
                f"{exc!r}"
            )

            await self._safe_reply(
                update,
                "⚠️ تعذر قراءة حالة السوق.",
            )

    # =========================================================
    # OPEN TRADES
    # =========================================================

    async def open_trades_menu(self, update, context):
        if not await self._guard(update):
            return
        await self._safe_reply(update, "📂 اختر أفق الصفقات المفتوحة:", reply_markup=self._open_trades_menu())

    async def open_trades(
        self,
        update,
        context,
    ):
        if (
            not await self._guard(update)
            or not self.service
        ):
            return
        await self._safe_reply(update, self.service.open_trades_text())

    async def open_trades_intraday(self, update, context):
        if not await self._guard(update) or not self.service:
            return
        await self._safe_reply(update, self.service.open_trades_text("intraday"), reply_markup=self._open_trades_menu())

    async def open_trades_two_day(self, update, context):
        if not await self._guard(update) or not self.service: return
        await self._safe_reply(update, self.service.open_trades_text("two_day"), reply_markup=self._open_trades_menu())

    async def open_trades_multi_session(self, update, context):
        if not await self._guard(update) or not self.service:
            return
        await self._safe_reply(update, self.service.open_trades_text("multi_session"), reply_markup=self._open_trades_menu())

    # =========================================================
    # PERFORMANCE
    # =========================================================

    async def performance(
        self,
        update,
        context,
    ):
        if (
            not await self._guard(update)
            or not self.service
        ):
            return

        await self._safe_reply(
            update,
            self.service.performance_text(),
        )

    async def performance_intraday(self, update, context):
        if not await self._guard(update) or not self.service:
            return
        await self._safe_reply(update, self.service.performance_text("intraday"))

    async def performance_two_day(self, update, context):
        if not await self._guard(update) or not self.service: return
        await self._safe_reply(update, self.service.performance_text("two_day"), reply_markup=self._performance_menu())

    async def performance_multi_session(self, update, context):
        if not await self._guard(update) or not self.service:
            return
        await self._safe_reply(update, self.service.performance_text("multi_session"))

    # =========================================================
    # DAILY / WEEKLY REPORT COMMANDS
    # =========================================================

    async def daily_report_command(self, update, context):
        if not await self._guard(update) or not self.service:
            return
        try:
            await self._safe_reply(update, "📊 جاري إنشاء التقرير اليومي...")
            result = await self.service.daily_report(send=False, private=True)
            await self._safe_reply(update, result)
        except Exception as exc:
            print(f"[telegram] daily report failed: {exc!r}")
            await self._safe_reply(update, "⚠️ تعذر إنشاء التقرير اليومي حاليًا.")

    async def report_command(
        self,
        update,
        context,
    ):
        """
        /report من الخاص:

        1) ينشئ التقرير.
        2) يرسل الصورة للمشرف في الخاص.
        3) لا يرسل التقرير اليدوي للقروب أو القناة.
        """

        if (
            not await self._guard(update)
            or not self.service
        ):
            return

        try:

            await self._safe_reply(
                update,
                "📊 جاري إنشاء التقرير الأسبوعي..."
            )

            result = await self.service.weekly_report(
                send=False,
                private=True,
            )

            await self._safe_reply(
                update,
                result,
            )

        except Exception as exc:

            print(
                "[telegram] /report failed: "
                f"{exc!r}"
            )

            await self._safe_reply(
                update,
                "⚠️ تعذر إنشاء أو إرسال التقرير حاليًا.",
            )

    # =========================================================
    # STATUS
    # =========================================================

    async def status(
        self,
        update,
        context,
    ):
        if (
            not await self._guard(update)
            or not self.service
        ):
            return

        await self._safe_reply(
            update,
            self.service.status_text(),
        )

    # =========================================================
    # HEALTH
    # =========================================================

    async def health(
        self,
        update,
        context,
    ):
        if (
            not await self._guard(update)
            or not self.service
        ):
            return

        try:

            text = await self.service.health_text()

            await self._safe_reply(
                update,
                text,
            )

        except Exception as exc:

            print(
                "[telegram] /health failed: "
                f"{exc!r}"
            )

            await self._safe_reply(
                update,
                "⚠️ تعذر قراءة صحة النظام.",
            )


    # =========================================================
    # TASILAB DIAGNOSTIC
    # =========================================================

    async def test_tasilab(self, update, context):
        """Lightweight private-admin Tasilab diagnostic.

        Uses only three requests by default:
        - /v1/auth/me
        - /v1/market/status
        - /v1/market/quote/1120

        It intentionally skips bulk quotes to keep API consumption low.
        """
        if (
            not await self._guard(update)
            or not self.service
        ):
            return

        provider = getattr(self.service, "p", None)
        tasilab = getattr(provider, "tasilab", None)

        if tasilab is None or not hasattr(tasilab, "diagnose"):
            await self._safe_reply(
                update,
                "⚠️ تشخيص Tasilab غير متاح في هذه النسخة.",
            )
            return

        await self._safe_reply(
            update,
            "🧪 جاري اختبار Tasilab بثلاثة طلبات خفيفة...",
        )

        try:
            report = await tasilab.diagnose(
                "1120",
                include_bulk=False,
            )

            classification = str(
                report.get("classification", "UNKNOWN")
            )
            checks = report.get("checks", {}) or {}

            labels = {
                "auth": "المصادقة",
                "market_status": "حالة السوق",
                "single_quote": "سعر 1120",
            }

            lines = [
                "🧪 تشخيص Tasilab",
                f"النتيجة: {classification}",
                "",
            ]

            for key in ("auth", "market_status", "single_quote"):
                item = checks.get(key, {}) or {}
                status = item.get("status")
                latency = item.get("latency_ms")
                ok = bool(item.get("ok"))
                icon = "✅" if ok else "❌"
                status_text = str(status) if status is not None else "NETWORK"
                latency_text = (
                    f"{latency}ms" if latency is not None else "—"
                )
                lines.append(
                    f"{icon} {labels[key]}: HTTP {status_text} | {latency_text}"
                )

            # Show operational hints without exposing credentials or long HTML.
            failed = [
                item for item in checks.values()
                if isinstance(item, dict) and not item.get("ok")
            ]
            if failed:
                retry_after = next(
                    (str(x.get("retry_after")) for x in failed if x.get("retry_after")),
                    "",
                )
                cloudflare = any(bool(x.get("cloudflare")) for x in failed)
                if retry_after:
                    lines.append(f"⏳ Retry-After: {retry_after}s")
                if cloudflare:
                    lines.append("☁️ الخطأ مر عبر Cloudflare")

            meanings = {
                "HEALTHY": "✅ Tasilab يعمل حاليًا.",
                "AUTH_ERROR": "🔑 مشكلة في API Key أو صلاحية المصادقة.",
                "RATE_LIMIT": "⏳ تم الوصول إلى Rate Limit.",
                "PROVIDER_OR_UPSTREAM_5XX": "🌐 عطل 5xx من Tasilab أو المزود الخلفي.",
                "QUOTE_ENDPOINT_5XX": "🌐 Endpoint الأسعار يعاني خطأ 5xx.",
                "NETWORK_OR_TIMEOUT": "📡 تعذر الاتصال أو انتهت المهلة.",
                "ENDPOINT_OR_PARAMETER_ERROR": "⚙️ مشكلة endpoint أو parameters.",
                "DEGRADED_UNKNOWN": "⚠️ الخدمة تعمل جزئيًا وتحتاج مراجعة اللوق.",
            }
            lines.extend(["", meanings.get(classification, "⚠️ نتيجة غير معروفة.")])

            await self._safe_reply(update, "\n".join(lines))

        except Exception as exc:
            print("[telegram] /test_tasilab failed: " f"{exc!r}")
            await self._safe_reply(
                update,
                "⚠️ تعذر إكمال اختبار Tasilab. راجع Render Logs.",
            )

    # =========================================================
    # SETTINGS
    # =========================================================

    async def settings(
        self,
        update,
        context,
    ):
        if (
            not await self._guard(update)
            or not self.service
        ):
            return

        await self._safe_reply(
            update,
            self.service.settings_text(),
        )

    # =========================================================
    # RISK
    # =========================================================

    async def risk(
        self,
        update,
        context,
    ):
        if (
            not await self._guard(update)
            or not self.service
        ):
            return

        await self._safe_reply(
            update,
            self.service.risk_text(),
        )

    # =========================================================
    # PAUSE
    # =========================================================

    async def pause(
        self,
        update,
        context,
    ):
        if (
            not await self._guard(update)
            or not self.service
        ):
            return

        if not await self._admin_only(update):

            await self._safe_reply(
                update,
                "🔒 أمر /pause متاح للمشرف فقط.",
            )

            return

        self.service.set_paused(
            True
        )

        await self._safe_reply(
            update,
            "⏸️ تم إيقاف إنشاء الإشارات الجديدة.\n"
            "الصفقات المفتوحة تستمر في المتابعة.",
        )

    # =========================================================
    # RESUME
    # =========================================================

    async def resume(
        self,
        update,
        context,
    ):
        if (
            not await self._guard(update)
            or not self.service
        ):
            return

        if not await self._admin_only(update):

            await self._safe_reply(
                update,
                "🔒 أمر /resume متاح للمشرف فقط.",
            )

            return

        self.service.set_paused(
            False
        )

        await self._safe_reply(
            update,
            "▶️ تم استئناف إنشاء الإشارات الجديدة.",
        )

    async def search_menu(self, update, context):
        if not await self._guard(update):
            return
        await update.effective_message.reply_text(
            "🔎 نطاق البحث\n\nاختر عدد الأسهم النشطة التي تريد فرزها قبل التحليل العميق.",
            reply_markup=self._search_menu(),
        )

    async def _run_search_logic(self, update, screen_limit, detail_limit, label, *, full_market=False, trade_horizon="intraday", intraday_logic="core"):
        if not await self._guard(update) or not self.service:
            return
        try:
            stats = self.service.p.stats() if hasattr(self.service.p, "stats") else {}
            used = int(stats.get("sahmk_daily_requests", stats.get("daily_requests", 0)) or 0)
            safe = int(getattr(self.s, "sahmk_daily_switch_limit", 95))
            if (not full_market) and used >= max(0, safe - 10) and screen_limit > 25 and str(stats.get("active_provider", "sahmk")) == "sahmk":
                screen_limit, detail_limit = 25, min(detail_limit, 4)
                label += " (تم تخفيضه تلقائيًا إلى 25 لحماية حصة SAHMK)"
        except Exception:
            pass
        await self._safe_reply(update, f"🔎 بدأ {label}...\nلن تُنشر أي صفقة قبل تأكيدك.")
        try:
            result = await self.service.scan_once(
                source=("telegram_full_market" if full_market else f"telegram_menu_{screen_limit}"),
                screen_limit_override=screen_limit,
                detail_limit_override=detail_limit,
                full_market=full_market,
                trade_horizon=trade_horizon,
                intraday_logic=intraday_logic,
            )
            pending = self.service.pending_signal()
            if pending:
                await self.send_admin_signal_preview(pending)
                await update.effective_message.reply_text(
                    result + "\n\nاختر إرسال الصفقة أو إلغاءها.",
                    reply_markup=self._confirm_signal_menu(),
                )
            else:
                if trade_horizon == "intraday":
                    menu = self._intraday_emerging_menu() if intraday_logic == "emerging" else self._intraday_core_menu()
                else:
                    menu = self._multi_session_menu()
                await update.effective_message.reply_text(result, reply_markup=menu)
        except Exception as exc:
            print(f"[telegram] menu search failed: {exc!r}")
            if trade_horizon == "intraday":
                menu = self._intraday_emerging_menu() if intraday_logic == "emerging" else self._intraday_core_menu()
            else:
                menu = self._multi_session_menu()
            await update.effective_message.reply_text("⚠️ تعذر إكمال البحث حاليًا.", reply_markup=menu)

    async def _run_search(self, update, screen_limit, detail_limit, label, *, full_market=False, trade_horizon="intraday"):
        """Backward-compatible core search entry point used by older commands/tests."""
        return await self._run_search_logic(
            update, screen_limit, detail_limit, label, full_market=full_market,
            trade_horizon=trade_horizon, intraday_logic="core",
        )

    async def search_25(self, update, context):
        await self._run_search(update, 25, 4, "البحث الاقتصادي — 25 سهم")

    async def search_50(self, update, context):
        await self._run_search(update, 50, 6, "البحث المتوازن — 50 سهم")

    async def search_100(self, update, context):
        await self._run_search(update, 100, 10, "البحث الموسع — 100 سهم")

    async def search_extended(self, update, context):
        size = len(self.service.universe) if self.service else 0
        label = f"بحث السوق الكامل — تحديث ودمج Universe (المتاح قبل التحديث {size}) / فحص كل الرموز وتحليل أعمق لأفضل 20"
        await self._run_search(update, max(1, size), 20, label, full_market=True)

    async def intraday_menu(self, update, context):
        if not await self._guard(update): return
        await update.effective_message.reply_text(
            "⚡ التداول اليومي — اختر المنطق\n\n"
            "🛡️ الجودة الأساسية: المنطق المحافظ الحالي للبنية والسيولة وR/R ومنع المطاردة.\n"
            "🚀 صائد القادة: يلتقط الأسهم التي تتحول إلى قيادة عبر التسارع وRS وتوافق 15m/60m/Daily، ثم يمر بنفس Judge وإدارة المخاطر.",
            reply_markup=self._intraday_menu(),
        )

    async def waseem30_menu(self, update, context):
        if not await self._guard(update): return
        await update.effective_message.reply_text(
            "🧠 وسيم 30 — EARLY HUNTER ENGINE\n\n"
            "المحرك الأساسي الجديد: يبحث عن تسارع السيولة والقيمة والقوة النسبية قبل القفزة، ثم يفصل بين EARLY_RADAR / BUILDING / SETUP / TRADE_READY / WAIT_PULLBACK.\n"
            "يستخدم السيولة الداخلية والخارجية، VWAP، Opening Pressure، Compression/Expansion، MTF، Anti-Chase، Data Completeness، ويعامل Missing كـ UNKNOWN وليس BAD.\n"
            "وسيم 20 محفوظ داخل المحركات السابقة للمقارنة. Paper Trading فقط.",
            reply_markup=self._waseem30_menu(),
        )

    async def waseem30_start(self, update, context):
        if not await self._guard(update) or not self.service: return
        self.service.enable_waseem30()
        interval = int(getattr(self.s, "waseem30_interval_minutes", 15) or 15)
        await update.effective_message.reply_text(
            f"▶️ تم تشغيل وسيم 30. الفحص كل {interval} دقيقة داخل 09:30–14:50.\n"
            "🛰️ الصيد المبكر لا يعني صفقة؛ TRADE_READY فقط بعد اكتمال Core Conditions.\n"
            "🟣 السهم الممدود يتحول WAIT_PULLBACK بدل المطاردة.\n⚠️ Paper Trading فقط.",
            reply_markup=self._waseem30_menu(),
        )
        try:
            ran, detail = await self.service.run_waseem30_scanner(force=True)
            if not ran: await self._safe_reply(update, f"ℹ️ وسيم 30 مفعّل: {detail}")
        except Exception as exc:
            print(f"[telegram] WASEEM30 immediate run failed: {exc!r}")
            await self._safe_reply(update, "⚠️ تم تفعيل وسيم 30، وتعذر الفحص الفوري؛ سيعاود المحاولة آليًا.")

    async def waseem30_stop(self, update, context):
        if not await self._guard(update) or not self.service: return
        self.service.disable_waseem30()
        await update.effective_message.reply_text("⏹️ تم إيقاف وسيم 30.", reply_markup=self._waseem30_menu())

    async def waseem30_status(self, update, context):
        if not await self._guard(update) or not self.service: return
        st=self.service.waseem30_status(); m=dict(st.get("metrics") or {})
        await self._safe_reply(update,
            "📡 حالة وسيم 30\n\n"
            f"الحالة: {'🟢 يعمل' if st.get('enabled') else '🔴 متوقف'}\n"
            f"آخر فحص: {st.get('last_run_at') or 'لم يعمل بعد'}\n"
            f"Tracked: {m.get('tracked',0)} | Early Catch: {m.get('early_catch_rate',0)}% | Late Detection: {m.get('late_detection_rate',0)}%\n"
            f"Transitions: {m.get('transition_count',0)}"
        )

    async def waseem20_menu(self, update, context):
        if not await self._guard(update): return
        await update.effective_message.reply_text(
            "🧠 وسيم 20 — المحرك السعودي الموحّد\n\n"
            "يبدأ من مزاد الافتتاح 09:30، ثم يتابع السوق كل 15 دقيقة.\n"
            "لا توجد داخله قوائم يومي/يومين/متعدد: هو يختار الأفق الأنسب تلقائيًا من نفس التحليل.\n"
            "يقرأ Market Map + Money Flow + Leadership + Catalyst + 15m/60m/Daily + Target Feasibility.\n"
            "إذا كانت الفرصة WAIT يرسل لك الخطة كاملة: السعر، منطقة الدخول، الوقف، الأهداف، وقت البيانات، وسبب الانتظار.\n"
            "بيانات المزاد غير الموجودة لدى المزود تظهر صراحة: غير متاح — ولا يتم اختراعها.",
            reply_markup=self._waseem20_menu(),
        )

    async def waseem20_start(self, update, context):
        if not await self._guard(update) or not self.service: return
        self.service.enable_waseem20()
        interval = int(getattr(self.s, "waseem20_interval_minutes", 15) or 15)
        await update.effective_message.reply_text(
            "▶️ تم تشغيل وسيم 20 بشكل مستمر حتى توقفه أنت.\n"
            f"⏱ الفحص: كل {interval} دقيقة داخل نافذة 09:30–14:50.\n"
            "🌅 09:30–10:00: مزاد الافتتاح + الأخبار/المحفزات + أي بيانات مزاد يوفرها المزود.\n"
            "📈 بعد 10:00: يدمج اليومي + 1–2 جلسة + 2–5 جلسات ويختار الأفق تلقائيًا.\n"
            "🟡 WAIT تُرسل بتفاصيلها ولا تختفي.\n"
            "✅ TRADE_READY تبقى بحاجة لتأكيدك؛ Paper Trading فقط.\n"
            "🌙 خارج نافذة التداول يبقى مفعّلًا وتستمر خدمة الأخبار/المتابعة دون إنشاء دخول جديد.",
            reply_markup=self._waseem20_menu(),
        )
        try:
            ran, detail = await self.service.run_waseem20_scanner(force=True)
            if not ran:
                await self._safe_reply(update, f"ℹ️ وسيم 20 مفعّل: {detail}")
        except Exception as exc:
            print(f"[telegram] WASEEM20 immediate run failed: {exc!r}")
            await self._safe_reply(update, "⚠️ تم تفعيل وسيم 20، وتعذر الفحص الفوري؛ سيعاود المحاولة آليًا.")

    async def waseem20_stop(self, update, context):
        if not await self._guard(update) or not self.service: return
        self.service.disable_waseem20()
        await update.effective_message.reply_text(
            "⏹️ تم إيقاف وسيم 20. لن يعود للفحص حتى تضغط تشغيل مرة أخرى.",
            reply_markup=self._waseem20_menu(),
        )

    async def waseem20_status(self, update, context):
        if not await self._guard(update) or not self.service: return
        st = self.service.waseem20_status()
        await self._safe_reply(update,
            "📡 حالة وسيم 20\n\n"
            f"الحالة: {'🟢 يعمل' if st.get('enabled') else '🔴 متوقف'}\n"
            f"آخر فحص: {st.get('last_run_at') or 'لم يعمل بعد'}\n"
            f"عدد الأسهم التي لها سجل اكتشاف: {len(st.get('first_seen') or {})}"
        )

    async def legacy_trading_menu(self, update, context):
        if not await self._guard(update): return
        await update.effective_message.reply_text(
            "🧰 المحركات السابقة\n\nأبقيت اليومي/صائد القادة/1–2/متعدد للمقارنة والرجوع، لكنها لا تعمل قبل 10:00 ولا تحل محل وسيم 20.",
            reply_markup=self._legacy_trading_menu(),
        )

    async def intraday_core_menu(self, update, context):
        if not await self._guard(update): return
        await update.effective_message.reply_text(
            "🛡️ الجودة الأساسية\n\nالمنطق الأساسي المحافظ: Structure + VWAP + RS + Liquidity + Anti-Chase + Judge.",
            reply_markup=self._intraday_core_menu(),
        )

    async def intraday_emerging_menu(self, update, context):
        if not await self._guard(update): return
        await update.effective_message.reply_text(
            "🚀 صائد القادة — Emerging Leader Hunter\n\nيراقب الانتقال إلى القيادة والتسارع والقوة النسبية وتوافق الفترات. السهم القوي لا يختفي بسبب التمدد؛ يظهر كـ EXECUTABLE أو WAIT_PULLBACK أو NO_CHASE قبل القرار النهائي.",
            reply_markup=self._intraday_emerging_menu(),
        )

    async def leader_monitor_start(self, update, context):
        if not await self._guard(update) or not self.service:
            return
        status = self.service.enable_saudi_scanner()
        interval = int(getattr(self.s, "leader_monitor_interval_minutes", 30) or 30)
        await self._safe_reply(
            update,
            "🛰️ تم تشغيل السكان السعودي الآلي لهذا اليوم.\n"
            f"⏱ سيعيد فحص محركات اليومي + 1–2 جلسة + متعدد الجلسات كل {interval} دقيقة داخل نافذة التداول اليومي.\n"
            "✅ إذا وجد TRADE_READY سيخبرك أنه مرشح صفقة ويطلب تأكيدك؛ لن ينشر تلقائيًا.\n"
            "🟡 القائد الممدود سيظهر WAIT_PULLBACK/NO_CHASE بدل أن يختفي.\n"
            "🔒 يتوقف تلقائيًا بعد نهاية اليوم ولا يستمر لليوم التالي.",
            reply_markup=self._intraday_menu(),
        )
        try:
            ran, detail = await self.service.run_saudi_scanner(force=True)
            if not ran and detail not in {"لم يحن موعد الفحص", "خارج نافذة البحث"}:
                await self._safe_reply(update, f"ℹ️ المراقب مفعّل، لكن الفحص الفوري لم يعمل: {detail}")
        except Exception as exc:
            print(f"[telegram] leader monitor immediate run failed: {exc!r}")
            await self._safe_reply(update, "⚠️ تم تفعيل المراقب، لكن تعذر الفحص الفوري. سيعاود المحاولة في دورة المجدول التالية.")

    async def leader_monitor_stop(self, update, context):
        if not await self._guard(update) or not self.service:
            return
        self.service.disable_saudi_scanner()
        await self._safe_reply(
            update,
            "⏹️ تم إيقاف السكان السعودي الآلي. البحث اليدوي وصائد القادة اليدوي ما زالا متاحين.",
            reply_markup=self._intraday_menu(),
        )

    async def two_day_menu(self, update, context):
        if not await self._guard(update): return
        await update.effective_message.reply_text(
            "⏭️ فرص 1–2 جلسة\n\nمحرك سعودي مستقل لالتقاط الاستمرار بعد الجلسة: Money Flow + RS + Catalyst + 60m/Daily + Target Feasibility.",
            reply_markup=self._two_day_menu(),
        )

    async def two_day_25(self, update, context):
        await self._run_search(update, 25, 4, "فرص يومين — 25 سهم", trade_horizon="two_day")
    async def two_day_50(self, update, context):
        await self._run_search(update, 50, 6, "فرص يومين — 50 سهم", trade_horizon="two_day")
    async def two_day_100(self, update, context):
        await self._run_search(update, 100, 10, "فرص يومين — 100 سهم", trade_horizon="two_day")
    async def two_day_full(self, update, context):
        size = len(self.service.universe) if self.service else 0
        await self._run_search(update, max(1, size), 20, "فرص يومين — السوق كامل", full_market=True, trade_horizon="two_day")

    async def multi_session_menu(self, update, context):
        if not await self._guard(update): return
        await update.effective_message.reply_text(
            "📅 متعدد الجلسات — 2 إلى 5 جلسات\n\nDaily/60m + RS متعدد الأيام + Sector/Catalyst + Overnight Risk، مع 15m لتوقيت الدخول فقط.",
            reply_markup=self._multi_session_menu(),
        )

    async def back_trading_menu(self, update, context):
        await self.trading_menu(update, context)

    async def intraday_25(self, update, context):
        await self._run_search(update, 25, 4, "الجودة الأساسية — 25 سهم", trade_horizon="intraday")
    async def intraday_50(self, update, context):
        await self._run_search(update, 50, 6, "الجودة الأساسية — 50 سهم", trade_horizon="intraday")
    async def intraday_100(self, update, context):
        await self._run_search(update, 100, 10, "الجودة الأساسية — 100 سهم", trade_horizon="intraday")
    async def intraday_full(self, update, context):
        size = len(self.service.universe) if self.service else 0
        await self._run_search(update, max(1, size), 20, "الجودة الأساسية — السوق كامل", full_market=True, trade_horizon="intraday")

    async def emerging_25(self, update, context):
        await self._run_search_logic(update, 25, 4, "صائد القادة — 25 سهم", trade_horizon="intraday", intraday_logic="emerging")
    async def emerging_50(self, update, context):
        await self._run_search_logic(update, 50, 6, "صائد القادة — 50 سهم", trade_horizon="intraday", intraday_logic="emerging")
    async def emerging_100(self, update, context):
        await self._run_search_logic(update, 100, 10, "صائد القادة — 100 سهم", trade_horizon="intraday", intraday_logic="emerging")
    async def emerging_full(self, update, context):
        size = len(self.service.universe) if self.service else 0
        await self._run_search_logic(update, max(1, size), 20, "صائد القادة — السوق كامل", full_market=True, trade_horizon="intraday", intraday_logic="emerging")

    async def multi_25(self, update, context):
        await self._run_search(update, 25, 4, "متعدد الجلسات — 25 سهم", trade_horizon="multi_session")
    async def multi_50(self, update, context):
        await self._run_search(update, 50, 6, "متعدد الجلسات — 50 سهم", trade_horizon="multi_session")
    async def multi_100(self, update, context):
        await self._run_search(update, 100, 10, "متعدد الجلسات — 100 سهم", trade_horizon="multi_session")
    async def multi_full(self, update, context):
        size = len(self.service.universe) if self.service else 0
        await self._run_search(update, max(1, size), 20, "متعدد الجلسات — السوق كامل", full_market=True, trade_horizon="multi_session")

    async def news_status(self, update, context):
        if not await self._guard(update) or not self.service: return
        await self._safe_reply(update, self.service.news_status_text())

    async def api_usage_menu(self, update, context):
        if not await self._guard(update):
            return
        await update.effective_message.reply_text(
            "📡 استهلاك API\n\nالعدادات محلية داخل هذا المشروع؛ Tasilab لا يعلن حصة يومية رسمية هنا.",
            reply_markup=self._api_usage_menu(),
        )

    async def api_usage_sahmk(self, update, context):
        if not await self._guard(update) or not self.service:
            return
        await update.effective_message.reply_text(self.service.api_usage_text("sahmk"), reply_markup=self._api_usage_menu())

    async def api_usage_tasilab(self, update, context):
        if not await self._guard(update) or not self.service:
            return
        await update.effective_message.reply_text(self.service.api_usage_text("tasilab"), reply_markup=self._api_usage_menu())

    async def api_usage_all(self, update, context):
        if not await self._guard(update) or not self.service:
            return
        await update.effective_message.reply_text(self.service.api_usage_text("all"), reply_markup=self._api_usage_menu())

    async def api_request_log(self, update, context):
        if not await self._guard(update) or not self.service:
            return
        await update.effective_message.reply_text(
            self.service.api_request_log_text(),
            reply_markup=self._api_usage_menu(),
        )

    async def trading_menu(self, update, context):
        if not await self._guard(update): return
        await update.effective_message.reply_text("📈 التداول والتحليل", reply_markup=self._trading_menu())

    async def system_menu(self, update, context):
        if not await self._guard(update): return
        await update.effective_message.reply_text("⚙️ النظام", reply_markup=self._system_menu())

    async def learning_menu(self, update, context):
        if not await self._guard(update): return
        await update.effective_message.reply_text("🧠 Learning Memory", reply_markup=self._learning_menu())

    async def learning_status(self, update, context):
        if not await self._guard(update) or not self.service: return
        st=self.service.learning_status()
        await update.effective_message.reply_text(st, reply_markup=self._learning_menu())

    async def learning_export(self, update, context):
        if not await self._guard(update) or not self.service: return
        path=self.service.learning_export_path()
        admin_id=int(self.s.telegram_admin_user_id)
        with open(path,"rb") as fh:
            await self.signal.send_document(chat_id=admin_id, document=fh, filename="learning_memory.json", caption="🧠 نسخة Learning Memory")
        await update.effective_message.reply_text("✅ تم تصدير ملف التعلم.", reply_markup=self._learning_menu())

    async def learning_import_start(self, update, context):
        if not await self._guard(update): return
        self._awaiting_learning_import=True
        await update.effective_message.reply_text("📥 أرسل الآن ملف learning_memory.json في الخاص. سيتم التحقق منه قبل الاستيراد.", reply_markup=self._learning_menu())

    async def learning_document(self, update, context):
        if not await self._guard(update) or not self.service or not self._awaiting_learning_import: return
        doc=update.effective_message.document
        if not doc: return
        try:
            tgfile=await doc.get_file(); raw=await tgfile.download_as_bytearray()
            n=self.service.learning_import(bytes(raw)); self._awaiting_learning_import=False
            await update.effective_message.reply_text(f"✅ تم استيراد {n} صفقة مكتملة إلى Learning Memory.", reply_markup=self._learning_menu())
        except Exception as exc:
            await update.effective_message.reply_text(f"⚠️ رفض ملف التعلم: {exc}", reply_markup=self._learning_menu())

    async def learning_reset(self, update, context):
        if not await self._guard(update) or not self.service: return
        if not self._awaiting_learning_reset:
            self._awaiting_learning_reset=True
            await update.effective_message.reply_text("⚠️ اضغط 🗑 تأكيد تصفير التعلم لمسح الذاكرة.", reply_markup=ReplyKeyboardMarkup([["🗑 تأكيد تصفير التعلم"],["⬅️ رجوع للقائمة الرئيسية"]],resize_keyboard=True,is_persistent=True))
            return
        self.service.learning_reset(); self._awaiting_learning_reset=False
        await update.effective_message.reply_text("✅ تم تصفير Learning Memory.", reply_markup=self._learning_menu())

    async def learning_reset_confirm(self, update, context):
        if not await self._guard(update) or not self.service: return
        if self._awaiting_learning_reset:
            self.service.learning_reset(); self._awaiting_learning_reset=False
            await update.effective_message.reply_text("✅ تم تصفير Learning Memory.", reply_markup=self._learning_menu())
        else:
            await update.effective_message.reply_text("ℹ️ لا يوجد طلب تصفير معلق.", reply_markup=self._learning_menu())

    # =========================================================
    # SIGNAL CONFIRMATION
    # =========================================================

    async def confirm_signal(self, update, context):
        if not await self._guard(update) or not self.service:
            return
        ok, text = await self.service.confirm_pending_signal()
        await update.effective_message.reply_text(
            text,
            reply_markup=self._admin_menu(),
        )

    async def cancel_signal(self, update, context):
        if not await self._guard(update) or not self.service:
            return
        cancelled = self.service.cancel_pending_signal()
        text = (
            "❌ تم إلغاء الصفقة. لم تُنشر ولم تُسجل ضمن Paper Trading."
            if cancelled
            else "ℹ️ لا توجد صفقة معلقة لإلغائها."
        )
        await update.effective_message.reply_text(text, reply_markup=self._admin_menu())

    # =========================================================
    # PRIVATE DISPLAY TESTS (NO MARKET API)
    # =========================================================

    async def reports_menu(self, update, context):
        if not await self._guard(update):
            return
        await update.effective_message.reply_text(
            "🧾 التقارير — خاص فقط وعند الطلب\n\nلا يوجد إرسال تلقائي للقروب أو القناة. اختر التقرير المطلوب.",
            reply_markup=self._reports_menu(),
        )

    async def tests_menu(self, update, context):
        if not await self._guard(update):
            return
        await update.effective_message.reply_text(
            "🧪 قائمة اختبارات العرض\n\nكل الاختبارات خاصة ولا تستدعي بيانات السوق.",
            reply_markup=self._tests_menu(),
        )

    async def back_main_menu(self, update, context):
        if not await self._guard(update):
            return
        if self.service and self.service.pending_signal():
            self.service.cancel_pending_signal()
        self._awaiting_learning_import=False
        self._awaiting_learning_reset=False
        await self._menu_reply(update, "🤖 رجعت للقائمة الرئيسية.")

    async def test_trade_display(self, update, context):
        if not await self._guard(update):
            return
        admin_id = int(self.s.telegram_admin_user_id)
        text = (
            "🚨 اختبار عرض صفقة — خاص فقط\n\n"
            "السهم: أرامكو\nالرمز: 2222\n"
            "💰 الدخول: 30.50 ريال\n"
            "🎯 TP1: 31.20 | TP2: 32.10 | TP3: 33.20\n"
            "🛑 SL: 28.90\n\n"
            "⚠️ هذه رسالة اختبار واجهة فقط ولا تُسجل كصفقة."
        )
        with open(str(getattr(self.s, "trade_card_image", "app/assets/telegram/trade_card.png")), "rb") as fh:
            msg = await self.signal.send_photo(chat_id=admin_id, photo=fh, caption=text)
        self._last_test_trade_message_id = int(msg.message_id)
        await self._safe_reply(update, "✅ تم إرسال اختبار الصفقة في الخاص.")

    async def test_profit_display(self, update, context):
        if not await self._guard(update):
            return
        admin_id = int(self.s.telegram_admin_user_id)
        if not self._last_test_trade_message_id:
            await self.test_trade_display(update, context)
        text = (
            "🟢 اختبار تحديث أرباح — خاص فقط\n\n"
            "أرامكو — 2222\n"
            "الدخول: 30.50 ريال\nالسعر الحالي: 33.50 ريال\n"
            "الحركة: +3.00 ريال\n"
            "⚠️ اختبار واجهة فقط."
        )
        with open(str(getattr(self.s, "profit_update_image", "app/assets/telegram/profit_update.png")), "rb") as fh:
            await self.signal.send_photo(
                chat_id=admin_id,
                photo=fh,
                caption=text,
                reply_to_message_id=self._last_test_trade_message_id,
                allow_sending_without_reply=True,
            )
        await self._safe_reply(update, "✅ تم إرسال تحديث الأرباح كـ Reply على اختبار الصفقة.")

    async def test_daily_report_display(self, update, context):
        if not await self._guard(update) or not self.service:
            return
        await self.service.daily_report(send=False, private=True)
        await self._safe_reply(update, "✅ تم استعراض التقرير اليومي في الخاص.")

    async def test_weekly_report_display(self, update, context):
        if not await self._guard(update) or not self.service:
            return
        await self.service.weekly_report(send=False, private=True)
        await self._safe_reply(update, "✅ تم استعراض التقرير الأسبوعي في الخاص.")

    # =========================================================
    # PRIVATE MENU BUTTONS
    # =========================================================

    async def menu_button(self, update, context):
        """Route Arabic reply-keyboard buttons to the existing command logic."""
        if not await self._guard(update):
            return

        text = (update.effective_message.text or "").strip()
        routes = {
            "📈 التداول": self.trading_menu,
            "🧠 وسيم 30": self.waseem30_menu,
            "▶️ تشغيل وسيم 30": self.waseem30_start,
            "⏹️ إيقاف وسيم 30": self.waseem30_stop,
            "📡 حالة وسيم 30": self.waseem30_status,
            "🧠 وسيم 20": self.waseem20_menu,
            "▶️ تشغيل وسيم 20": self.waseem20_start,
            "⏹️ إيقاف وسيم 20": self.waseem20_stop,
            "📡 حالة وسيم 20": self.waseem20_status,
            "🧰 المحركات السابقة": self.legacy_trading_menu,
            "⚡ تداول يومي": self.intraday_menu,
            "🛡️ الجودة الأساسية": self.intraday_core_menu,
            "🚀 صائد القادة": self.intraday_emerging_menu,
            "🛰️ تشغيل السكان السعودي": self.leader_monitor_start,
            "🛰️ تشغيل مراقب القادة": self.leader_monitor_start,
            "⏹️ إيقاف السكان السعودي": self.leader_monitor_stop,
            "⏹️ إيقاف مراقب القادة": self.leader_monitor_stop,
            "⬅️ رجوع للتداول اليومي": self.intraday_menu,
            "⏭️ فرص 1–2 جلسة": self.two_day_menu,
            "📅 متعدد الجلسات": self.multi_session_menu,
            "⬅️ رجوع للتداول": self.back_trading_menu,
            "⚡ يومي — بحث 25": self.intraday_25,
            "🎯 يومي — بحث 50": self.intraday_50,
            "🔭 يومي — بحث 100": self.intraday_100,
            "🌐 يومي — السوق كامل": self.intraday_full,
            "🛡️ أساسي — بحث 25": self.intraday_25,
            "🛡️ أساسي — بحث 50": self.intraday_50,
            "🛡️ أساسي — بحث 100": self.intraday_100,
            "🛡️ أساسي — السوق كامل": self.intraday_full,
            "🚀 قادة — بحث 25": self.emerging_25,
            "🚀 قادة — بحث 50": self.emerging_50,
            "🚀 قادة — بحث 100": self.emerging_100,
            "🚀 قادة — السوق كامل": self.emerging_full,
            "⚡ يومين — بحث 25": self.two_day_25,
            "🎯 يومين — بحث 50": self.two_day_50,
            "🔭 يومين — بحث 100": self.two_day_100,
            "🌐 يومين — السوق كامل": self.two_day_full,
            "⚡ متعدد — بحث 25": self.multi_25,
            "🎯 متعدد — بحث 50": self.multi_50,
            "🔭 متعدد — بحث 100": self.multi_100,
            "🌐 متعدد — السوق كامل": self.multi_full,
            "📰 الأخبار والمحـفزات": self.news_status,
            "⚙️ النظام": self.system_menu,
            "🧠 التعلم": self.learning_menu,
            "📊 حالة التعلم": self.learning_status,
            "📤 تصدير ملف التعلم": self.learning_export,
            "📥 استيراد ملف التعلم": self.learning_import_start,
            "🗑 تصفير التعلم": self.learning_reset,
            "🗑 تأكيد تصفير التعلم": self.learning_reset_confirm,
            "📊 التقارير": self.reports_menu,
            "🔎 البحث": self.search_menu,
            "🔎 فحص فرصة": self.search_menu,
            "⚡ بحث 25": self.search_25,
            "🎯 بحث 50": self.search_50,
            "🔭 بحث 100": self.search_100,
            "🌐 السوق كامل": self.search_extended,
            "🌐 بحث موسع": self.search_extended,
            "✅ إرسال الصفقة": self.confirm_signal,
            "❌ إلغاء الصفقة": self.cancel_signal,
            "📈 حالة السوق": self.market,
            "📂 الصفقات المفتوحة": self.open_trades_menu,
            "⚡ المفتوحة اليومية": self.open_trades_intraday,
            "⏭️ المفتوحة 1–2 جلسة": self.open_trades_two_day,
            "📅 المفتوحة متعدد الجلسات": self.open_trades_multi_session,
            "📂 كل الصفقات المفتوحة": self.open_trades,
            "📊 الأداء": self.performance,
            "⚡ أداء اليومي": self.performance_intraday,
            "⏭️ أداء 1–2 جلسة": self.performance_two_day,
            "📅 أداء متعدد الجلسات": self.performance_multi_session,
            "🧾 تقرير يومي": self.daily_report_command,
            "📅 تقرير أسبوعي": self.report_command,
            "🩺 صحة النظام": self.health,
            "⚙️ الإعدادات": self.settings,
            "🛡️ المخاطر": self.risk,
            "🧾 التقارير": self.reports_menu,
            "🧪 اختبارات الرسائل": self.tests_menu,
            "🧪 قائمة الاختبارات": self.tests_menu,
            "🧪 اختبار Tasilab": self.test_tasilab,
            "📡 حالة النظام": self.status,
            "📡 استهلاك API": self.api_usage_menu,
            "📡 استهلاك مزودي البيانات": self.api_usage_menu,
            "🧾 سجل الطلبات": self.api_request_log,
            "📡 استهلاك SAHMK": self.api_usage_sahmk,
            "📡 استهلاك Tasilab": self.api_usage_tasilab,
            "📊 ملخص المزودين": self.api_usage_all,
            "🧪 اختبار صفقة": self.test_trade_display,
            "🧪 اختبار تحديث أرباح": self.test_profit_display,
            "🧪 اختبار تقرير يومي": self.test_daily_report_display,
            "🧪 اختبار تقرير أسبوعي": self.test_weekly_report_display,
            "⬅️ رجوع للقائمة الرئيسية": self.back_main_menu,
            "⏸️ إيقاف الإشارات": self.pause,
            "▶️ استئناف الإشارات": self.resume,
            "ℹ️ المساعدة": self.help,
            "👤 معرفي": self.myid,
        }

        callback = routes.get(text)
        if callback is not None:
            await callback(update, context)
            return

        # Private admin free text is ignored to keep the control chat clean.
        # The persistent keyboard remains available under the input field.

    # =========================================================
    # ERROR HANDLER
    # =========================================================

    async def error(
        self,
        update,
        context,
    ):
        print(
            "[telegram] handler error: "
            f"{context.error!r}"
        )

    # =========================================================
    # HANDLERS
    # =========================================================

    def _add_handlers(self):

        handlers = {
            "start": self.start,
            "help": self.help,
            "signal": self.signal_command,
            "market": self.market,
            "open": self.open_trades,
            "performance": self.performance,
            "daily_report": self.daily_report_command,
            "report": self.report_command,
            "status": self.status,
            "health": self.health,
            "test_tasilab": self.test_tasilab,
            "test_trade": self.test_trade_display,
            "test_profit": self.test_profit_display,
            "test_daily_report": self.test_daily_report_display,
            "test_weekly_report": self.test_weekly_report_display,
            "settings": self.settings,
            "risk": self.risk,
            "pause": self.pause,
            "resume": self.resume,
            "myid": self.myid,
        }

        for name, callback in handlers.items():

            self.application.add_handler(
                CommandHandler(
                    name,
                    callback,
                )
            )

        self.application.add_handler(
            MessageHandler(filters.Document.ALL, self.learning_document)
        )
        self.application.add_handler(
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                self.menu_button,
            )
        )

        self.application.add_error_handler(
            self.error
        )

    # =========================================================
    # START TELEGRAM
    # =========================================================

    async def start_commands(self):

        if self.application is not None:
            return

        self.application = (
            Application.builder()
            .token(
                self.s.signal_bot_token
            )
            .build()
        )

        self._add_handlers()

        await self.application.initialize()
        await self.application.start()

        # -----------------------------------------------------
        # WEBHOOK
        # -----------------------------------------------------

        if self.mode == "webhook":

            base_url = os.getenv(
                "RENDER_EXTERNAL_URL",
                "",
            ).rstrip("/")

            if not base_url:

                raise RuntimeError(
                    "Webhook mode requires "
                    "RENDER_EXTERNAL_URL"
                )

            webhook_url = (
                f"{base_url}/telegram/webhook"
            )

            await self.application.bot.set_webhook(
                url=webhook_url,
                allowed_updates=[
                    "message",
                ],
                drop_pending_updates=True,
                secret_token=self.webhook_secret,
            )

            print(
                "[telegram] webhook started: "
                f"{webhook_url}"
            )

        # -----------------------------------------------------
        # POLLING LOCAL
        # -----------------------------------------------------

        else:

            await self.application.bot.delete_webhook(
                drop_pending_updates=True
            )

            if self.application.updater is None:

                raise RuntimeError(
                    "Telegram updater is unavailable"
                )

            await self.application.updater.start_polling(
                allowed_updates=[
                    "message",
                ],
                drop_pending_updates=True,
            )

            print(
                "[telegram] command polling started"
            )

    # =========================================================
    # WEBHOOK PROCESSING
    # =========================================================

    async def process_webhook(
        self,
        payload,
        received_secret,
    ):

        if (
            self.mode != "webhook"
            or self.application is None
        ):
            return False

        if (
            not received_secret
            or not hmac.compare_digest(
                received_secret,
                self.webhook_secret,
            )
        ):
            return False

        update = Update.de_json(
            payload,
            self.application.bot,
        )

        await self.application.update_queue.put(
            update
        )

        return True

    # =========================================================
    # STOP
    # =========================================================

    async def stop_commands(self):

        if self.application is None:
            return

        try:

            if (
                self.application.updater
                and self.application.updater.running
            ):
                await self.application.updater.stop()

            if self.application.running:
                await self.application.stop()

            await self.application.shutdown()

        finally:
            self.application = None
