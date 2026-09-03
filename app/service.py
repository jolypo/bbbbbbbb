import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo

import pandas as pd

from app.data.universe import normalize_universe
from app.market.regime import classify_tasi, tasi_context
from app.market.quality import TASIMarketQualityEngine
from app.market.mubasher import MubasherMarketTotalsClient
from app.scanner.screener import fast_score
from app.strategy.analyzer import assess_intraday, assess_multi_session
from app.strategy.emerging_leader import stage1_emerging_score, mtf_consensus_score, execution_state
from app.strategy.saudi_native import evaluate_saudi_opportunity
from app.strategy.waseem20 import evaluate_waseem20, build_wait_plan
from app.strategy.waseem30 import evaluate_waseem30, stage1_waseem30_score
from app.strategy.two_stage import (
    build_intraday_hunter, build_multi_session_hunter, judge as judge_candidate,
)
from app.strategy.leadership import (
    LeadershipTracker, entry_quality as leadership_entry_quality,
    leadership_score as compute_leadership_score, limit_state as classify_limit_state,
)
from app.news.engine import NewsCatalystEngine
from app.learning.memory import LearningMemory
from app.signal_engine.engine import SignalEngine
from app.indicators.technical import resample_ohlcv, latest_features
from app.telegram.trade_update_card import build_trade_update_card
from app.telegram.report_card import build_report_card
from app.telegram.messages import (
    profit_message,
    signal_message,
    tp_message,
    entry_message,
    expired_entry_message,
    time_exit_message,
)
from app.database.store import build_store
from app.trades.manager import TradeManager


class TradingService:
    """
    Core service.

    - New public signals always require MANUAL admin confirmation.
    - Discovery is manual by default; V23 can run the Emerging Leader scan every
      30 minutes only after the admin enables it for the current Saudi day.
    - Scheduler:
        * monitors open trades
        * sends TP / SL events
        * sends periodic price updates
        * sends market-close notification
        * refreshes news/catalyst context
        * can stage a private leader candidate when the admin-enabled monitor is on
        * NEVER publishes a new trade or daily/weekly report automatically
    """

    def __init__(
        self,
        settings,
        provider,
        bots,
        historical_provider=None,
    ):
        self.s = settings
        self.p = provider
        self.h = historical_provider
        self.b = bots

        self.store = build_store(settings)

        self.trade_manager = TradeManager(
            self.store,
            settings,
        )

        self.market_quality_engine = TASIMarketQualityEngine()
        self.news = NewsCatalystEngine(settings)
        self.leadership_tracker = LeadershipTracker(self.store)
        self.last_news_refresh_at = None
        self.learning = LearningMemory(
            getattr(settings, "learning_file", "data/learning_memory.json"),
            min_samples=getattr(settings, "learning_min_samples", 12),
            max_adjustment=getattr(settings, "learning_max_adjustment", 2.0),
        )

        # Bootstrap the universe locally when the router has bundled/runtime
        # symbols. This avoids spending 3+ SAHMK company-pagination requests
        # simply because Render restarted before a manual /signal scan.
        cached_companies = (
            provider.cached_companies()
            if hasattr(provider, "cached_companies")
            else []
        )
        self.universe = normalize_universe(cached_companies)
        self.news.bind_universe(self.universe)

        # A bundled symbol-only universe is useful for startup resilience but it
        # is not "fresh metadata": it has no sector/name classification.  Keep
        # last_refresh unset in that case so the first manual scan attempts one
        # economical SAHMK company refresh and enables real sector analysis.
        has_runtime_metadata = any(
            str(item.get("sector", "") or "").strip() or str(item.get("name", "") or "").strip()
            for item in self.universe
        )
        self.last_refresh = self._utc_now() if (self.universe and has_runtime_metadata) else None
        self.last_scan = None
        self.last_monitor = None

        self.last_market_summary = None
        self.last_market_summary_at = None
        self.last_market_totals = None
        self.last_market_totals_at = None
        self.last_market_breadth = None
        self.last_market_breadth_at = None
        self.last_market_breadth_yahoo_attempt_at = None
        self.market_totals_client = MubasherMarketTotalsClient(
            getattr(self.s, "market_totals_mubasher_url", "https://www.mubasher.info/markets/TDWL"),
            timeout_seconds=float(getattr(self.s, "market_totals_timeout_seconds", 15.0)),
        )

        self.scan_cursor = 0
        self.monitor_cursor = 0

        self.scan_lock = asyncio.Lock()
        self.monitor_lock = asyncio.Lock()

        self.last_report_key = None
        self.last_daily_report_key = None
        self.last_market_close_key = None
        self.last_market_warmup_key = None
        self.last_intraday_close_reconcile_key = None
        self._waseem20_scan_alerts = []
        self._waseem30_scan_alerts = []
        self.last_horizon_exit_attempt_at = None

        self.tz = ZoneInfo(
            self.s.timezone
        )

        self.b.attach_service(self)

    async def startup_bootstrap(self):
        """Best-effort startup context. Never blocks service availability on news failure."""
        result = await self.news.bootstrap()
        self.last_news_refresh_at = self._utc_now()
        print(f"[startup] news bootstrap ok={result.get('ok')} cached={result.get('cached', 0)}")
        return result

    async def refresh_news_if_due(self):
        if not bool(getattr(self.s, "news_enabled", True)):
            return
        now = self._utc_now()
        interval = max(5, int(getattr(self.s, "news_refresh_minutes", 30)))
        if self.last_news_refresh_at and (now - self.last_news_refresh_at).total_seconds() < interval * 60:
            return
        await self.news.refresh(reason="scheduled_refresh")
        self.last_news_refresh_at = now

    def news_status_text(self):
        st = self.news.status()
        recent = self.news.recent(6)
        lines = []
        for item in recent:
            sym = item.get("symbol") or "—"
            impact = item.get("impact") or "LOW"
            headline = str(item.get("headline") or "").strip()
            if len(headline) > 110:
                headline = headline[:107] + "..."
            lines.append(f"• {sym} | {impact} | {headline}")
        recent_text = "\n".join(lines) if lines else "• لا توجد إعلانات مخزنة حاليًا"
        providers = st.get("providers") or {}
        primary = providers.get("SAUDI_EXCHANGE") or {}
        fallback = providers.get("MUBASHER_RSS") or {}
        page_fallback = providers.get("MUBASHER_PAGE") or {}
        primary_reason = primary.get("reason") or "—"
        fallback_reason = fallback.get("reason") or "—"
        page_reason = page_fallback.get("reason") or "—"
        return (
            "📰 محرك الأخبار والمحـفزات\n\n"
            f"المصدر الأساسي: {st.get('source')}\n"
            f"المصدر المستخدم فعليًا: {st.get('effective_source') or 'NONE'}\n"
            f"الحالة: {'مفعّل' if st.get('enabled') else 'متوقف'}\n"
            f"صحة المصدر/المحرك: {st.get('source_state') or 'UNKNOWN'}\n"
            f"سبب التدهور العام: {st.get('source_reason') or '—'}\n\n"
            "📡 حالة المصادر:\n"
            f"• Saudi Exchange: {primary.get('state') or 'UNKNOWN'} | عناصر: {primary.get('items', 0)}\n"
            f"  السبب: {primary_reason}\n"
            f"• Mubasher RSS (Fallback): {fallback.get('state') or 'UNKNOWN'} | عناصر: {fallback.get('items', 0)}\n"
            f"  السبب: {fallback_reason}\n"
            f"• Mubasher Announcements (Last Resort): {page_fallback.get('state') or 'UNKNOWN'} | عناصر: {page_fallback.get('items', 0)}\n"
            f"  السبب: {page_reason}\n\n"
            f"العناصر المخزنة: {st.get('cached_items', 0)}\n"
            f"صالحة زمنيًا للتحليل: {st.get('verified_time_items', 0)}\n"
            f"عرض فقط (وقت غير موثوق): {st.get('display_only_unknown_time_items', 0)}\n"
            f"آخر تحديث: {st.get('last_refresh') or '—'}\n\n"
            "آخر العناصر المخزنة:\n" + recent_text +
            "\n\nالأخبار سياق/Bonus أو Penalty فقط؛ لا تنشئ BUY/SELL وحدها."
        )

    # =========================================================
    # TIME
    # =========================================================

    def _utc_now(self):
        return datetime.now(
            timezone.utc
        )

    def _local_now(self):
        return self._utc_now().astimezone(
            self.tz
        )

    @staticmethod
    def _minutes(clock_text):
        hour, minute = str(
            clock_text
        ).split(":", 1)

        return (
            int(hour) * 60
            + int(minute)
        )

    @staticmethod
    def _parse_datetime(value):
        if not value:
            return None

        if isinstance(
            value,
            datetime,
        ):
            dt = value

        else:
            try:
                dt = datetime.fromisoformat(
                    str(value).replace(
                        "Z",
                        "+00:00",
                    )
                )

            except (
                TypeError,
                ValueError,
            ):
                return None

        if dt.tzinfo is None:
            dt = dt.replace(
                tzinfo=timezone.utc
            )

        return dt.astimezone(
            timezone.utc
        )

    # =========================================================
    # MARKET HOURS
    # =========================================================

    def market_is_open(self):
        local = self._local_now()

        # Saudi Exchange:
        # Sunday -> Thursday
        if local.weekday() in (
            4,
            5,
        ):
            return False

        minute = (
            local.hour * 60
            + local.minute
        )

        return (
            self._minutes(
                self.s.market_open
            )
            <= minute
            < self._minutes(
                self.s.market_close
            )
        )

    def market_is_monitorable(self):
        """True through the Saudi closing-auction / trade-at-last window."""
        local = self._local_now()
        if local.weekday() in (4, 5):
            return False
        minute = local.hour * 60 + local.minute
        return (
            self._minutes(self.s.market_open)
            <= minute
            < self._minutes(getattr(self.s, "market_monitor_close", "15:20"))
        )

    def _effective_min_traded_value(self, local=None):
        """Scale the cumulative SAR-value liquidity floor by session progress.

        Saudi quotes expose cumulative traded value. Applying a full-day floor at
        10:30 is structurally harsher than applying it at 14:30, so the floor
        grows with elapsed continuous-trading time while never falling below a
        configured safety fraction.
        """
        local = local or self._local_now()
        base = max(0.0, float(getattr(self.s, "min_daily_traded_value", 2_000_000) or 0))
        if base <= 0:
            return 0.0
        open_m = self._minutes(getattr(self.s, "market_open", "10:00"))
        close_m = self._minutes(getattr(self.s, "market_close", "15:00"))
        now_m = local.hour * 60 + local.minute
        span = max(1, close_m - open_m)
        progress = max(0.0, min(1.0, (now_m - open_m) / span))
        floor = max(0.10, min(1.0, float(getattr(self.s, "liquidity_progress_floor", 0.25) or 0.25)))
        return base * max(floor, progress)

    def _daily_limit_pct(self, quote):
        """Resolve the applicable daily price fluctuation limit conservatively.

        Provider metadata wins. Otherwise use the normal Main Market default.
        We never infer a newly-listed 30% band merely from a large move.
        """
        raw = getattr(quote, "raw", None) or {}
        if isinstance(raw, dict):
            for key in (
                "daily_limit_pct", "price_limit_pct", "daily_fluctuation_limit_pct",
                "fluctuation_limit_pct", "dailyPriceLimitPct",
            ):
                try:
                    value = abs(float(raw.get(key)))
                    if 1.0 <= value <= 50.0:
                        return value
                except (TypeError, ValueError):
                    pass
            newly_listed = raw.get("is_newly_listed")
            trading_day = raw.get("listing_trading_day", raw.get("trading_day_number"))
            try:
                if bool(newly_listed) or (trading_day is not None and 1 <= int(trading_day) <= 3):
                    return float(getattr(self.s, "newly_listed_daily_price_limit_pct", 30.0))
            except (TypeError, ValueError):
                pass
        return float(getattr(self.s, "normal_daily_price_limit_pct", 10.0))

    def _multi_session_relative_strength(self, stock_daily, market_daily):
        """Completed-session RS over 3/5/10 sessions; no current daily bar lookahead."""
        if stock_daily is None or market_daily is None or stock_daily.empty or market_daily.empty:
            return 50.0, {}, ["RS متعدد الجلسات غير متاح؛ لا Bonus ولا Penalty"]

        def completed(df):
            local = df.copy()
            if "datetime" not in local.columns or "close" not in local.columns:
                return local.iloc[0:0]
            dates = local["datetime"].dt.tz_convert(self.tz).dt.date
            today = self._local_now().date()
            return local[dates < today].reset_index(drop=True)

        stock = completed(stock_daily)
        market = completed(market_daily)
        metrics = {}
        reasons = []
        score = 50.0
        for n, weight in ((3, 12.0), (5, 10.0), (10, 8.0)):
            if len(stock) <= n or len(market) <= n:
                continue
            sr = (float(stock.iloc[-1]["close"]) / float(stock.iloc[-(n + 1)]["close"]) - 1.0) * 100.0
            mr = (float(market.iloc[-1]["close"]) / float(market.iloc[-(n + 1)]["close"]) - 1.0) * 100.0
            rs = sr - mr
            metrics[f"rs_{n}d"] = round(rs, 3)
            if rs >= 3.0:
                score += weight
            elif rs >= 1.5:
                score += weight * 0.65
            elif rs >= 0.5:
                score += weight * 0.30
            elif rs <= -2.0:
                score -= weight * 0.70
            elif rs < 0:
                score -= weight * 0.25
        if metrics:
            reasons.append("RS متعدد الجلسات: " + ", ".join(f"{k.upper()}={v:+.2f}%" for k, v in metrics.items()))
        else:
            reasons.append("عينات Daily غير كافية لحساب RS 3D/5D/10D")
        return max(0.0, min(100.0, score)), metrics, reasons

    # =========================================================
    # QUOTE FRESHNESS
    # =========================================================

    def _quote_freshness(self, quote):
        if quote is None:
            return False, "missing_quote", None
        if getattr(quote, "price", 0) <= 0:
            return False, "invalid_price", None
        if getattr(quote, "updated_at", None) is None:
            return False, "missing_timestamp", None

        updated_at = quote.updated_at
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)

        age = (self._utc_now() - updated_at).total_seconds() / 60.0
        if age < -5:
            return False, "future_timestamp", age
        if age > self.s.data_max_delay_minutes:
            return False, "stale", age
        return True, "ok", age

    def _fresh_quote(self, quote):
        return self._quote_freshness(quote)[0]

    # =========================================================
    # UNIVERSE
    # =========================================================

    async def refresh(self):
        self.universe = normalize_universe(
            await self.p.companies(
                "TASI"
            )
        )
        self.news.bind_universe(self.universe)

        self.last_refresh = (
            self._utc_now()
        )

        self.scan_cursor = (
            min(
                self.scan_cursor,
                max(
                    0,
                    len(self.universe) - 1,
                ),
            )
            if self.universe
            else 0
        )

        state = self.store.state()

        state["meta"][
            "last_universe_refresh"
        ] = self.last_refresh.isoformat()

        state["meta"][
            "universe_size"
        ] = len(self.universe)

        self.store.save_state(
            state
        )

        print(
            f"[universe] "
            f"{len(self.universe)} companies"
        )

    async def _ensure_universe(self):
        if (
            self.universe
            and self.last_refresh
        ):
            age = (
                self._utc_now()
                - self.last_refresh
            ).total_seconds()

            if (
                age
                <= self.s.universe_refresh_seconds
            ):
                return

        try:
            await self.refresh()

        except Exception as exc:
            print(
                "[universe] refresh failed, "
                "continuing without metadata: "
                f"{exc}"
            )

    # =========================================================
    # STATE
    # =========================================================

    def is_paused(self):
        return bool(
            self.store.state().get(
                "paused",
                False,
            )
        )

    def set_paused(
        self,
        paused,
    ):
        state = self.store.state()

        state["paused"] = bool(
            paused
        )

        state["meta"][
            "paused_at"
        ] = self._utc_now().isoformat()

        self.store.save_state(
            state
        )

    def can_send(self):
        state = self.store.state()

        today = (
            self._local_now()
            .date()
            .isoformat()
        )

        return (
            not state.get(
                "paused",
                False,
            )
            and len(
                state["open_trades"]
            )
            < self.s.max_open_trades
            and state[
                "daily_signals"
            ].get(
                today,
                0,
            )
            < self.s.max_daily_signals
            and self.s.paper_mode
        )

    # =========================================================
    # PENDING SIGNAL CONFIRMATION
    # =========================================================

    def _clear_pending_signal(self):
        state = self.store.state()
        state["pending_signal"] = None
        self.store.save_state(state)

    def pending_signal(self):
        """Return a non-expired private preview without calling any market API."""
        state = self.store.state()
        pending = state.get("pending_signal")
        if not pending:
            return None

        expires_at = self._parse_datetime(pending.get("expires_at"))
        if expires_at is None or self._utc_now() >= expires_at:
            state["pending_signal"] = None
            self.store.save_state(state)
            return None

        signal = pending.get("signal")
        return dict(signal) if isinstance(signal, dict) else None

    def cancel_pending_signal(self):
        had_pending = self.pending_signal() is not None
        self._clear_pending_signal()
        return had_pending

    # =========================================================
    # V23 — ADMIN-ENABLED AUTOMATIC LEADER MONITOR
    # =========================================================

    def leader_monitor_status(self):
        state = self.store.state()
        cfg = dict(state.get("leader_monitor") or {})
        local = self._local_now()
        if cfg.get("day") != local.date().isoformat():
            return {"enabled": False, "day": cfg.get("day"), "last_run_at": cfg.get("last_run_at")}
        return {
            "enabled": bool(cfg.get("enabled", False)),
            "day": cfg.get("day"),
            "last_run_at": cfg.get("last_run_at"),
            "last_digest": cfg.get("last_digest"),
        }

    def enable_leader_monitor(self):
        state = self.store.state()
        local = self._local_now()
        state["leader_monitor"] = {
            "enabled": True,
            "day": local.date().isoformat(),
            "last_run_at": None,
            "last_digest": None,
        }
        self.store.save_state(state)
        return self.leader_monitor_status()

    def disable_leader_monitor(self):
        state = self.store.state()
        cfg = dict(state.get("leader_monitor") or {})
        cfg["enabled"] = False
        state["leader_monitor"] = cfg
        self.store.save_state(state)
        return self.leader_monitor_status()

    def _leader_monitor_due(self, *, force=False):
        local = self._local_now()
        if local.weekday() in (4, 5):
            return False, "السوق مغلق"
        status = self.leader_monitor_status()
        if not status.get("enabled"):
            return False, "المراقب غير مفعّل"
        now_min = local.hour * 60 + local.minute
        start = self._minutes(getattr(self.s, "signal_window_start", "10:30"))
        end = self._minutes(getattr(self.s, "signal_window_end", "14:50"))
        if not (start <= now_min <= end):
            return False, "خارج نافذة البحث"
        if force:
            return True, "تشغيل فوري"
        last = self._parse_datetime(status.get("last_run_at"))
        interval = max(15, int(getattr(self.s, "leader_monitor_interval_minutes", 30) or 30))
        if last is not None and (self._utc_now() - last.astimezone(timezone.utc)).total_seconds() < interval * 60:
            return False, "لم يحن موعد الفحص"
        return True, "مستحق"

    def _leader_monitor_digest(self, result, pending):
        local = self._local_now()
        header = (
            f"🛰️ السكان السعودي الآلي — {local.strftime('%H:%M')}\n"
            f"⏱ الفحص كل {int(getattr(self.s, 'leader_monitor_interval_minutes', 30) or 30)} دقيقة\n"
        )
        if pending:
            return header + "✅ وجد مرشح صفقة APPROVE — بانتظار تأكيدك."
        text = str(result or "").strip()
        marker = "📌 أقرب المرشحين:"
        if marker in text:
            closest = marker + text.split(marker, 1)[1]
        else:
            closest = text[-2200:] if len(text) > 2200 else text
        return (
            header
            + "🎯 قرار الصفقة: لا يوجد TRADE_READY الآن.\n"
            + "ℹ️ التسمية القديمة للتوافق: لا يوجد APPROVE الآن.\n"
            + "📌 الزبدة: القائد قد يبقى WATCH / WAIT_PULLBACK / NO_CHASE حتى تتحسن منطقة الدخول.\n\n"
            + closest[:3000]
        )

    async def run_leader_monitor(self, *, force=False):
        due, reason = self._leader_monitor_due(force=force)
        if not due:
            return False, reason
        # Never replace a manual/automatic setup that is already awaiting the
        # admin's decision. Confirmation expiry remains the single source of truth.
        if self.pending_signal() is not None:
            return False, "توجد صفقة معلقة تنتظر التأكيد"

        screen_limit = max(25, int(getattr(self.s, "leader_monitor_screen_limit", 50) or 50))
        detail_limit = max(4, int(getattr(self.s, "leader_monitor_detail_limit", 6) or 6))
        result = await self.scan_once(
            source="auto_leader_monitor",
            screen_limit_override=screen_limit,
            detail_limit_override=detail_limit,
            full_market=False,
            trade_horizon="intraday",
            intraday_logic="emerging",
        )
        pending = self.pending_signal()
        state = self.store.state()
        cfg = dict(state.get("leader_monitor") or {})
        cfg["last_run_at"] = self._utc_now().isoformat()
        snapshot = dict(getattr(self, "last_scan_snapshot", {}) or {})
        cfg["last_digest"] = snapshot.get("digest")
        state["leader_monitor"] = cfg
        self.store.save_state(state)

        if pending:
            await self.b.send_admin_signal_preview(
                pending,
                prefix=(
                    "🛰️ السكان السعودي الآلي\n"
                    "✅ TRADE_READY — مرشح صفقة الآن\n"
                    "📌 لم تُنشر الصفقة؛ القرار النهائي لك.\n\n"
                ),
            )
        else:
            await self.b.send_admin_text(self._leader_monitor_digest(result, pending))
        return True, result

    def saudi_scanner_status(self):
        state = self.store.state()
        cfg = dict(state.get("saudi_scanner") or {})
        today = self._local_now().date().isoformat()
        enabled = bool(cfg.get("enabled")) and cfg.get("day") == today
        return {"enabled": enabled, "day": cfg.get("day"), "last_run_at": cfg.get("last_run_at")}

    def enable_saudi_scanner(self):
        state = self.store.state()
        state["saudi_scanner"] = {"enabled": True, "day": self._local_now().date().isoformat(), "last_run_at": None}
        self.store.save_state(state)
        return self.saudi_scanner_status()

    def disable_saudi_scanner(self):
        state = self.store.state()
        cfg = dict(state.get("saudi_scanner") or {})
        cfg["enabled"] = False
        state["saudi_scanner"] = cfg
        self.store.save_state(state)
        return self.saudi_scanner_status()

    async def run_saudi_scanner(self, *, force=False):
        status = self.saudi_scanner_status()
        if not status.get("enabled"):
            return False, "السكان السعودي غير مفعّل"
        local = self._local_now()
        if local.weekday() in (4, 5):
            return False, "السوق مغلق"
        now_min = local.hour * 60 + local.minute
        start = self._minutes(getattr(self.s, "signal_window_start", "10:30"))
        end = self._minutes(getattr(self.s, "signal_window_end", "14:50"))
        if not (start <= now_min <= end):
            return False, "خارج نافذة إنشاء الصفقات؛ خدمة الأخبار/المتابعة تبقى شغالة"
        if not force and status.get("last_run_at"):
            last = self._parse_datetime(status.get("last_run_at"))
            interval = max(15, int(getattr(self.s, "saudi_scanner_interval_minutes", 30) or 30))
            if last and (self._utc_now() - last).total_seconds() < interval * 60:
                return False, "لم يحن موعد الفحص"
        if self.pending_signal() is not None:
            return False, "توجد صفقة TRADE_READY معلقة تنتظر التأكيد"
        screen = max(25, int(getattr(self.s, "saudi_scanner_screen_limit", 100) or 100))
        detail = max(4, int(getattr(self.s, "saudi_scanner_detail_limit", 10) or 10))
        parts = []
        for horizon, logic, label in (("intraday", "emerging", "⚡ اليومي"), ("two_day", "core", "⏭️ 1–2 جلسة"), ("multi_session", "core", "📅 2–5 جلسات")):
            if self.pending_signal() is not None:
                break
            result = await self.scan_once(source=f"auto_saudi_scanner_{horizon}", screen_limit_override=screen,
                                          detail_limit_override=detail, full_market=False,
                                          trade_horizon=horizon, intraday_logic=logic)
            parts.append(label + "\n" + str(result))
        state = self.store.state()
        cfg = dict(state.get("saudi_scanner") or {})
        cfg["last_run_at"] = self._utc_now().isoformat()
        state["saudi_scanner"] = cfg
        self.store.save_state(state)
        pending = self.pending_signal()
        if pending:
            await self.b.send_admin_signal_preview(pending, prefix="🛰️ السكان السعودي الآلي\n✅ TRADE_READY — القرار النهائي لك.\n\n")
        else:
            await self.b.send_admin_text("🛰️ خلاصة السكان السعودي\n\n" + "\n\n".join(parts)[:3900])
        return True, "\n\n".join(parts)


    # =========================================================
    # WASEEM 20 — PERSISTENT UNIFIED SAUDI SCANNER
    # =========================================================

    def waseem20_status(self):
        state = self.store.state()
        cfg = dict(state.get("waseem20_scanner") or {})
        return {
            "enabled": bool(cfg.get("enabled")),
            "last_run_at": cfg.get("last_run_at"),
            "last_alerts": dict(cfg.get("last_alerts") or {}),
            "first_seen": dict(cfg.get("first_seen") or {}),
        }

    def enable_waseem20(self):
        state = self.store.state()
        legacy = dict(state.get("saudi_scanner") or {})
        legacy["enabled"] = False
        state["saudi_scanner"] = legacy
        old_leader = dict(state.get("leader_monitor") or {})
        old_leader["enabled"] = False
        state["leader_monitor"] = old_leader
        w30 = dict(state.get("waseem30_scanner") or {})
        w30["enabled"] = False
        state["waseem30_scanner"] = w30
        cfg = dict(state.get("waseem20_scanner") or {})
        cfg["enabled"] = True
        cfg.setdefault("last_alerts", {})
        cfg.setdefault("first_seen", {})
        state["waseem20_scanner"] = cfg
        self.store.save_state(state)
        return self.waseem20_status()

    def disable_waseem20(self):
        state = self.store.state()
        cfg = dict(state.get("waseem20_scanner") or {})
        cfg["enabled"] = False
        state["waseem20_scanner"] = cfg
        self.store.save_state(state)
        return self.waseem20_status()

    @staticmethod
    def _waseem_horizon_ar(horizon):
        return {
            "intraday": "نفس الجلسة",
            "two_day": "1–2 جلسة",
            "multi_session": "2–5 جلسات",
        }.get(str(horizon), str(horizon or "غير محدد"))

    @staticmethod
    def _waseem_time_ar(value):
        if not value:
            return "غير متاح"
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(ZoneInfo("Asia/Riyadh")).strftime("%Y-%m-%d %H:%M")
        except Exception:
            return str(value)

    def _format_waseem20_alert(self, item, *, first_seen=None):
        plan = dict(item.get("plan") or {})
        auction = dict(item.get("auction") or {})
        reasons = "\n".join(f"• {x}" for x in list(item.get("reasons") or [])[:7]) or "• لا يوجد سبب مفصل"
        blockers = "\n".join(f"• {x}" for x in list(item.get("blockers") or [])[:5]) or "• لا توجد موانع مسجلة"
        unavailable = list(auction.get("unavailable_fields") or [])
        available = list(auction.get("available_fields") or [])
        auction_text = (
            f"جلسة البيانات: {auction.get('session','غير متاح')}\n"
            f"سعر المزاد الاسترشادي: {auction.get('indicative_price') if auction.get('indicative_price') is not None else 'غير متاح من المزود الحالي'}\n"
            f"تغير المزاد: {auction.get('indicative_change_pct') if auction.get('indicative_change_pct') is not None else 'غير متاح'}\n"
            f"حجم المزاد المتطابق: {auction.get('indicative_volume') if auction.get('indicative_volume') is not None else 'غير متاح'}\n"
            f"اختلال الطلب/العرض: {auction.get('imbalance') if auction.get('imbalance') is not None else 'غير متاح'}\n"
            f"أفضل طلب/عرض: {auction.get('best_bid') if auction.get('best_bid') is not None else 'غير متاح'} / "
            f"{auction.get('best_ask') if auction.get('best_ask') is not None else 'غير متاح'}\n"
            f"حقول مزاد متاحة: {', '.join(available) if available else 'لا يوجد'}\n"
            f"حقول غير متاحة: {', '.join(unavailable) if unavailable else 'لا يوجد'}"
        )
        if plan.get("available"):
            plan_text = (
                f"منطقة الدخول المخططة: {float(plan.get('entry_low',0)):.2f} – {float(plan.get('entry_high',0)):.2f}\n"
                f"سعر مرجعي للدخول: {float(plan.get('entry',0)):.2f}\n"
                f"وقف/إلغاء الفكرة: {float(plan.get('sl',0)):.2f}\n"
                f"TP1: {float(plan.get('tp1',0)):.2f}\n"
                f"TP2: {float(plan.get('tp2',0)):.2f}\n"
                f"TP3: {float(plan.get('tp3',0)):.2f}\n"
                f"R/R إلى TP1: 1 : {float(plan.get('rr_tp1',0)):.2f}"
            )
        else:
            plan_text = f"خطة الدخول/الأهداف: غير متاحة — {plan.get('reason','بيانات غير كافية')}"
        state = str(item.get("state") or "RADAR")
        verdict = "✅ مرشح صفقة" if state == "TRADE_READY" else "🟡 WAIT — راقب منطقة الدخول ولا تطارد السعر"
        cat = item.get("catalyst_headline") or "لا يوجد محفز عام مؤكد في قاعدة الأخبار الحالية"
        text = (
            "🧠 وسيم 20 — تنبيه فرصة سعودية\n\n"
            f"{verdict}\n"
            f"السهم: {item.get('name','—')} ({item.get('symbol','—')})\n"
            f"السعر المرصود: {float(item.get('price',0) or 0):.2f}\n"
            f"تغير الجلسة: {float(item.get('change_percent',0) or 0):+.2f}%\n"
            f"الأفق الذي اختاره المحرك: {self._waseem_horizon_ar(item.get('horizon'))}\n"
            f"أول اكتشاف لهذه الحالة: {self._waseem_time_ar(first_seen)}\n"
            f"وقت قرار المحرك: {self._waseem_time_ar(item.get('decision_time'))}\n"
            f"آخر سعر/Quote: {self._waseem_time_ar(item.get('quote_updated_at'))}\n"
            f"آخر شمعة تاريخية مستخدمة: {self._waseem_time_ar(item.get('historical_updated_at'))}\n\n"
            "📊 درجات وسيم 20\n"
            f"الإجمالي: {float(item.get('total_score',0)):.1f}/100\n"
            f"Money Flow: {float(item.get('money_flow_score',0)):.1f}/100\n"
            f"Leadership: {float(item.get('leadership_score',0)):.1f}/100\n"
            f"Persistence: {float(item.get('persistence_score',0)):.1f}/100\n"
            f"Catalyst: {float(item.get('catalyst_score',0)):.1f}/100\n"
            f"Structure: {float(item.get('structure_score',0)):.1f}/100\n"
            f"Entry: {float(item.get('entry_score',0)):.1f}/100\n"
            f"Target Feasibility: {float(item.get('target_feasibility_score',0)):.1f}/100\n"
            f"Risk Quality: {float(item.get('risk_score',0)):.1f}/100\n\n"
            "🎯 خطة السعر\n" + plan_text + "\n\n"
            "📰 الأخبار/المحفز\n"
            f"{cat}\n"
            f"المصدر: {item.get('catalyst_source') or 'غير متاح'}\n"
            f"وقت نشر الخبر: {self._waseem_time_ar(item.get('catalyst_published_at'))}\n\n"
            "🏷️ مزاد الافتتاح/الطلب والعرض\n" + auction_text + "\n\n"
            "📌 لماذا ظهر السهم؟\n" + reasons + "\n\n"
            "⚠️ لماذا WAIT/ما الذي يمنع الدخول؟\n" + blockers + "\n\n"
            "⚠️ Paper Trading فقط — الخطة لا تعني ضمان تحقق الهدف."
        )
        rlm = "\u200f"
        return "\n".join((rlm + line if line else line) for line in text.splitlines())[:4000]

    def _waseem_should_notify(self, item, previous):
        if not previous:
            return True
        if str(item.get("state")) != str(previous.get("state")):
            return True
        try:
            old_price = float(previous.get("price") or 0)
            new_price = float(item.get("price") or 0)
            if old_price > 0 and abs(new_price - old_price) / old_price >= 0.01:
                return True
            if abs(float(item.get("total_score") or 0) - float(previous.get("total_score") or 0)) >= 5:
                return True
            old_entry = float((previous.get("plan") or {}).get("entry") or 0)
            new_entry = float((item.get("plan") or {}).get("entry") or 0)
            if old_entry > 0 and abs(new_entry - old_entry) / old_entry >= 0.008:
                return True
        except Exception:
            return True
        return False

    async def run_waseem20_scanner(self, *, force=False):
        status = self.waseem20_status()
        if not status.get("enabled"):
            return False, "وسيم 20 غير مفعّل"
        local = self._local_now()
        if local.weekday() in (4, 5):
            return False, "إجازة أسبوعية — يبقى المحرك مفعّلًا لليوم التالي"
        now_min = local.hour * 60 + local.minute
        start = self._minutes(getattr(self.s, "waseem20_opening_auction_start", "09:30"))
        end = self._minutes(getattr(self.s, "waseem20_new_entry_end", "14:50"))
        if not (start <= now_min <= end):
            return False, "وسيم 20 مفعّل؛ خارج نافذة 09:30–14:50 يتم تحديث الأخبار والمتابعة فقط"
        if not force and status.get("last_run_at"):
            last = self._parse_datetime(status.get("last_run_at"))
            interval = max(15, int(getattr(self.s, "waseem20_interval_minutes", 15) or 15))
            if last and (self._utc_now() - last).total_seconds() < interval * 60:
                return False, "لم يحن موعد فحص وسيم 20"
        if self.pending_signal() is not None:
            return False, "توجد TRADE_READY تنتظر قرارك؛ لن يستبدلها وسيم 20"

        await self.refresh_news_if_due()
        screen = max(100, int(getattr(self.s, "waseem20_screen_limit", 300) or 300))
        detail = max(8, int(getattr(self.s, "waseem20_detail_limit", 12) or 12))
        result = await self.scan_once(
            source="waseem20_auto",
            screen_limit_override=screen,
            detail_limit_override=detail,
            full_market=True,
            trade_horizon="waseem20",
            intraday_logic="emerging",
        )

        state = self.store.state()
        cfg = dict(state.get("waseem20_scanner") or {})
        cfg["enabled"] = True
        cfg["last_run_at"] = self._utc_now().isoformat()
        last_alerts = dict(cfg.get("last_alerts") or {})
        first_seen = dict(cfg.get("first_seen") or {})

        alerts = sorted(list(self._waseem20_scan_alerts or []), key=lambda x: float(x.get("total_score", 0)), reverse=True)
        notified = 0
        for item in alerts[:4]:
            symbol = str(item.get("symbol") or "")
            if not symbol:
                continue
            previous = dict(last_alerts.get(symbol) or {})
            if symbol not in first_seen:
                first_seen[symbol] = item.get("decision_time") or self._utc_now().isoformat()
            if self._waseem_should_notify(item, previous):
                await self.b.send_admin_text(self._format_waseem20_alert(item, first_seen=first_seen.get(symbol)))
                notified += 1
            last_alerts[symbol] = item

        cfg["last_alerts"] = last_alerts
        cfg["first_seen"] = first_seen
        state["waseem20_scanner"] = cfg
        self.store.save_state(state)

        pending = self.pending_signal()
        if pending:
            await self.b.send_admin_signal_preview(
                pending,
                prefix="🧠 وسيم 20\n✅ TRADE_READY — الخطة اجتازت الفحص وبانتظار قرارك.\n\n",
            )
        return True, f"وسيم 20 أكمل الفحص؛ alerts={len(alerts)} notified={notified}\n{result}"

    # =========================================================
    # WASEEM 30 — EARLY HUNTER ENGINE (primary)
    # =========================================================

    def waseem30_status(self):
        state = self.store.state()
        cfg = dict(state.get("waseem30_scanner") or {})
        return {
            "enabled": bool(cfg.get("enabled")),
            "last_run_at": cfg.get("last_run_at"),
            "last_alerts": dict(cfg.get("last_alerts") or {}),
            "first_seen": dict(cfg.get("first_seen") or {}),
            "snapshots": dict(cfg.get("snapshots") or {}),
            "transitions": list(cfg.get("transitions") or []),
            "metrics": dict(cfg.get("metrics") or {}),
        }

    def enable_waseem30(self):
        state = self.store.state()
        # W30 is primary. Keep W20 installed but do not run both automatically.
        old20 = dict(state.get("waseem20_scanner") or {}); old20["enabled"] = False; state["waseem20_scanner"] = old20
        legacy = dict(state.get("saudi_scanner") or {}); legacy["enabled"] = False; state["saudi_scanner"] = legacy
        old_leader = dict(state.get("leader_monitor") or {}); old_leader["enabled"] = False; state["leader_monitor"] = old_leader
        cfg = dict(state.get("waseem30_scanner") or {})
        cfg["enabled"] = True
        cfg.setdefault("last_alerts", {}); cfg.setdefault("first_seen", {}); cfg.setdefault("snapshots", {})
        cfg.setdefault("transitions", []); cfg.setdefault("metrics", {})
        state["waseem30_scanner"] = cfg
        self.store.save_state(state)
        return self.waseem30_status()

    def disable_waseem30(self):
        state = self.store.state(); cfg = dict(state.get("waseem30_scanner") or {})
        cfg["enabled"] = False; state["waseem30_scanner"] = cfg; self.store.save_state(state)
        return self.waseem30_status()

    def _format_waseem30_alert(self, item, *, first_seen=None):
        plan = dict(item.get("plan") or {})
        state = str(item.get("state") or "EARLY_RADAR")
        headers = {
            "EARLY_RADAR": "🛰️ WASEEM 30 EARLY RADAR",
            "BUILDING": "🟡 WASEEM 30 BUILDING",
            "SETUP": "🟠 WASEEM 30 SETUP",
            "TRADE_READY": "🟢 WASEEM 30 TRADE READY",
            "WAIT_PULLBACK": "🟣 WASEEM 30 WAIT PULLBACK",
            "INVALIDATED": "🔴 WASEEM 30 INVALIDATED",
        }
        reasons = "\n".join(f"• {x}" for x in list(item.get("reasons") or [])[:6]) or "• لا يوجد سبب مفصل"
        blockers = "\n".join(f"• {x}" for x in list(item.get("blockers") or [])[:6]) or "• الحالة لا تحتاج مانع إضافي"
        lm = dict(item.get("liquidity_map") or {})
        ds = dict(item.get("data_status") or {})
        fs = first_seen if isinstance(first_seen, dict) else {"time": first_seen}
        plan_text = "خطة السعر: تُبنى بعد اكتمال Setup"
        if plan.get("available"):
            plan_text = (
                f"Entry Zone: {float(plan.get('entry_low',0)):.2f} – {float(plan.get('entry_high',0)):.2f}\n"
                f"Entry Ref: {float(plan.get('entry',0)):.2f} | SL: {float(plan.get('sl',0)):.2f}\n"
                f"TP1: {float(plan.get('tp1',0)):.2f} | TP2: {float(plan.get('tp2',0)):.2f} | TP3: {float(plan.get('tp3',0)):.2f}\n"
                f"R/R TP1: 1:{float(plan.get('rr_tp1',0)):.2f}"
            )
        text = (
            f"{headers.get(state, '🧠 WASEEM 30')}\n\n"
            f"السهم: {item.get('name','—')} ({item.get('symbol','—')})\n"
            f"السعر: {float(item.get('price',0) or 0):.2f} | التغير: {float(item.get('change_percent',0) or 0):+.2f}%\n"
            f"الحالة: {state} | Move Stage: {item.get('move_stage','UNKNOWN')}\n"
            f"نوع الدخول: {item.get('entry_type','NONE')} | الأفق: {self._waseem_horizon_ar(item.get('horizon'))}\n"
            f"Early Score: {float(item.get('early_score',0)):.1f}/100 | Priority: {float(item.get('total_score',0)):.1f}/100\n"
            f"Flow: {float(item.get('money_flow_score',0)):.1f} | Leadership: {float(item.get('leadership_score',0)):.1f} | Entry: {float(item.get('entry_score',0)):.1f}\n"
            f"Structure: {float(item.get('structure_score',0)):.1f} | Target: {float(item.get('target_feasibility_score',0)):.1f} | Risk: {float(item.get('risk_score',0)):.1f}\n\n"
            f"⏱ First Seen: {self._waseem_time_ar(fs.get('time'))} عند {float(fs.get('price',0) or 0):.2f} ({float(fs.get('change_percent',0) or 0):+.2f}%)\n"
            f"Decision: {self._waseem_time_ar(item.get('decision_time'))} | Quote: {self._waseem_time_ar(item.get('quote_updated_at'))}\n"
            f"Data Completeness: {float(item.get('data_completeness_score',0)):.0f}%\n\n"
            "💧 السيولة الداخلية/الخارجية والتنفيذ\n"
            f"Internal فوق/تحت: {lm.get('internal_liquidity_above_atr','—')} / {lm.get('internal_liquidity_below_atr','—')} ATR\n"
            f"External Up/Down: {lm.get('external_liquidity_up') or '—'} / {lm.get('external_liquidity_down') or '—'}\n"
            f"Bid/Ask: {lm.get('bid') or 'غير متاح'} / {lm.get('ask') or 'غير متاح'} | Spread: {('%.2f%%' % lm['spread_pct']) if lm.get('spread_pct') is not None else 'UNKNOWN'}\n\n"
            "🎯 " + plan_text + "\n\n"
            "📌 لماذا ظهر؟\n" + reasons + "\n\n"
            "⚠️ ما الذي ينقص/يمنع الدخول؟\n" + blockers + "\n\n"
            f"Data: BidAsk={ds.get('bid_ask','UNKNOWN')} | Auction={ds.get('auction','UNKNOWN')} | Catalyst={ds.get('catalyst','UNKNOWN')}\n"
            "⚠️ Paper Trading فقط — لا توجد أوامر حقيقية ولا ضمان لتحقيق الأهداف."
        )
        rlm = "\u200f"
        return "\n".join((rlm + line if line else line) for line in text.splitlines())[:4000]

    def _waseem30_should_notify(self, item, previous):
        if not previous: return True
        if str(item.get("state")) != str(previous.get("state")): return True
        if str(item.get("move_stage")) != str(previous.get("move_stage")): return True
        try:
            if abs(float(item.get("early_score") or 0) - float(previous.get("early_score") or 0)) >= 6: return True
            op=float(previous.get("price") or 0); np=float(item.get("price") or 0)
            if op>0 and abs(np-op)/op >= .012: return True
            oe=float((previous.get("plan") or {}).get("entry") or 0); ne=float((item.get("plan") or {}).get("entry") or 0)
            if oe>0 and abs(ne-oe)/oe >= .008: return True
            if str(item.get("catalyst_headline") or "") != str(previous.get("catalyst_headline") or ""): return True
        except Exception:
            return True
        return False

    async def run_waseem30_scanner(self, *, force=False):
        status = self.waseem30_status()
        if not status.get("enabled"): return False, "وسيم 30 غير مفعّل"
        local = self._local_now()
        if local.weekday() in (4,5): return False, "إجازة أسبوعية — يبقى المحرك مفعّلًا لليوم التالي"
        now_min = local.hour*60+local.minute
        start=self._minutes(getattr(self.s,"waseem30_opening_auction_start","09:30")); end=self._minutes(getattr(self.s,"waseem30_new_entry_end","14:50"))
        if not (start <= now_min <= end): return False, "وسيم 30 مفعّل؛ خارج نافذة إنشاء دخول جديد"
        if not force and status.get("last_run_at"):
            last=self._parse_datetime(status.get("last_run_at")); interval=max(15,int(getattr(self.s,"waseem30_interval_minutes",15) or 15))
            if last and (self._utc_now()-last).total_seconds() < interval*60: return False, "لم يحن موعد فحص وسيم 30"
        if self.pending_signal() is not None: return False, "توجد TRADE_READY تنتظر قرارك؛ لن يستبدلها وسيم 30"
        await self.refresh_news_if_due()
        result = await self.scan_once(source="waseem30_auto", screen_limit_override=max(100,int(getattr(self.s,"waseem30_screen_limit",300) or 300)),
                                     detail_limit_override=max(12,int(getattr(self.s,"waseem30_detail_limit",20) or 20)), full_market=True,
                                     trade_horizon="waseem30", intraday_logic="emerging")
        state=self.store.state(); cfg=dict(state.get("waseem30_scanner") or {}); cfg["enabled"]=True; cfg["last_run_at"]=self._utc_now().isoformat()
        last_alerts=dict(cfg.get("last_alerts") or {}); first_seen=dict(cfg.get("first_seen") or {}); snapshots=dict(cfg.get("snapshots") or {}); transitions=list(cfg.get("transitions") or [])
        alerts=sorted(list(self._waseem30_scan_alerts or []), key=lambda x:(float(x.get("early_score",0)),float(x.get("total_score",0))), reverse=True)
        notified=0
        for rank, item in enumerate(alerts):
            symbol=str(item.get("symbol") or "")
            if not symbol: continue
            prev=dict(last_alerts.get(symbol) or {})
            if symbol not in first_seen:
                first_seen[symbol]={"time":item.get("decision_time") or self._utc_now().isoformat(),"price":item.get("price"),"change_percent":item.get("change_percent"),
                                    "max_change_after_discovery":item.get("change_percent"),"ever_trade_ready":False}
            fs=dict(first_seen.get(symbol) or {})
            fs["max_change_after_discovery"] = max(float(fs.get("max_change_after_discovery", item.get("change_percent",0)) or 0), float(item.get("change_percent",0) or 0))
            if str(item.get("state")) == "TRADE_READY": fs["ever_trade_ready"] = True
            first_seen[symbol]=fs
            if prev and str(prev.get("state")) != str(item.get("state")):
                transitions.append({"symbol":symbol,"from":prev.get("state"),"to":item.get("state"),"time":item.get("decision_time"),"price":item.get("price"),"change_percent":item.get("change_percent")})
            # Persist every deeply analysed W30 candidate; only top-ranked meaningful changes are messaged.
            if rank < 6 and self._waseem30_should_notify(item, prev):
                await self.b.send_admin_text(self._format_waseem30_alert(item, first_seen=first_seen.get(symbol))); notified += 1
            last_alerts[symbol]=item
            if item.get("snapshot"): snapshots[symbol]=dict(item.get("snapshot") or {})
        # Bound persisted diagnostics.
        cfg["last_alerts"]=last_alerts; cfg["first_seen"]=first_seen; cfg["snapshots"]=snapshots; cfg["transitions"]=transitions[-1000:]
        if first_seen:
            rows=[v for v in first_seen.values() if isinstance(v,dict)]
            changes=[float(v.get("change_percent",0) or 0) for v in rows]
            moves=[float(v.get("max_change_after_discovery",v.get("change_percent",0)) or 0)-float(v.get("change_percent",0) or 0) for v in rows]
            ready=sum(1 for v in rows if v.get("ever_trade_ready"))
            wait_to_ready=sum(1 for t in transitions if t.get("to")=="TRADE_READY" and t.get("from") in {"EARLY_RADAR","BUILDING","SETUP","WAIT_PULLBACK"})
            cfg["metrics"]={"tracked":len(rows),"average_change_at_first_discovery":round(sum(changes)/len(changes),3) if changes else 0.0,
                            "average_move_after_discovery":round(sum(moves)/len(moves),3) if moves else 0.0,
                            "late_detection_rate":round(sum(1 for x in changes if x>=3.0)/len(changes)*100,2) if changes else 0.0,
                            "early_catch_rate":round(sum(1 for x in changes if x<=1.5)/len(changes)*100,2) if changes else 0.0,
                            "trade_ready_conversion_rate":round(ready/len(rows)*100,2) if rows else 0.0,
                            "wait_to_trade_ready_count":wait_to_ready,"transition_count":len(transitions)}
        state["waseem30_scanner"]=cfg; self.store.save_state(state)
        pending=self.pending_signal()
        if pending:
            await self.b.send_admin_signal_preview(pending,prefix="🧠 وسيم 30\n✅ TRADE_READY — اجتازت Core Conditions وبانتظار قرارك.\n\n")
        return True, f"وسيم 30 أكمل الفحص؛ alerts={len(alerts)} notified={notified}\n{result}"

    def _stage_pending_signal(self, signal):
        minutes = max(1, int(getattr(self.s, "signal_confirmation_expiry_minutes", 5)))
        now = self._utc_now()
        state = self.store.state()
        state["pending_signal"] = {
            "signal": signal.to_dict(),
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(minutes=minutes)).isoformat(),
        }
        self.store.save_state(state)

    async def confirm_pending_signal(self):
        """Publish the already-scanned setup. This performs zero market API calls."""
        signal = self.pending_signal()
        if not signal:
            return False, "⌛ لا توجد صفقة معلقة صالحة. أعد فحص الفرصة من القائمة."

        if not self.can_send():
            return False, "ℹ️ تعذر الإرسال: النظام متوقف أو تم بلوغ حد الصفقات/الإشارات."

        if not self.trade_manager.add(signal):
            self._clear_pending_signal()
            return False, "⚠️ تعذر تسجيل الصفقة؛ قد تكون هناك صفقة مفتوحة لنفس السهم أو تم بلوغ الحد."

        state = self.store.state()
        day = self._local_now().date().isoformat()
        state["daily_signals"][day] = state["daily_signals"].get(day, 0) + 1
        state["pending_signal"] = None
        self.store.save_state(state)

        # Public publication is intentionally text-only and single-message.
        # The bundled trade card is a static visual template and must never be
        # published with live trade data because it can contain sample values
        # that do not match the approved signal.
        signal_ids = await self.b.send_signal(
            signal_message(signal),
            trade=signal,
        )
        if not signal_ids:
            # No public destination accepted the signal. Roll back the paper trade
            # and daily count so a Telegram outage cannot create a hidden trade.
            self.trade_manager.remove_open(signal["symbol"])
            state = self.store.state()
            state["daily_signals"][day] = max(0, state["daily_signals"].get(day, 1) - 1)
            self.store.save_state(state)
            return False, "⚠️ فشل نشر الصفقة للقروب/القناة؛ لم تُسجل كصفقة مفتوحة."

        self.trade_manager.set_signal_message_ids(signal["symbol"], signal_ids)

        print(f"[signal] confirmed/sent {signal['symbol']} strategy={signal.get('strategy', '—')}")
        return True, (
            "✅ تم تأكيد ونشر الفرصة الورقية.\n"
            "🟡 الحالة: WAITING_ENTRY — لن تعتبر OPEN قبل لمس منطقة الدخول.\n"
            f"{signal.get('name', '—')} ({signal.get('symbol', '—')})\n"
            f"⭐ Score: {float(signal.get('score', 0)):.1f}/100"
        )

    # =========================================================
    # LEARNING MEMORY
    # =========================================================

    def learning_status(self):
        st=self.learning.stats()
        lines=[
            "🧠 حالة التعلم", "", f"الحالة: {st['status']}",
            f"الصفقات المكتملة: {st['samples']} / {st['min_samples']}",
            f"ناجحة: {st['wins']} | خاسرة: {st['losses']}",
            f"Win Rate: {st['win_rate']:.1f}%",
            f"Expectancy: {st['expectancy']:+.2f}%",
            f"Profit Factor: {st['profit_factor']:.2f}",
            f"Max Drawdown: {st['max_drawdown_pct']:.2f}%",
            f"Learning Adjustment: {st['adjustment']:+.2f}", "",
        ]
        groups=self.learning.group_summaries(6)
        if groups:
            lines.append("📚 أهم المجموعات:")
            for g in groups:
                parts=g['bucket'].split('|')
                label=' + '.join(parts[2:4]) if len(parts)>=4 else g['bucket']
                lines.append(f"• {label}: {g['samples']} صفقة | Win {g['win_rate']:.1f}% | Avg {g['avg_return']:+.2f}%")
            lines.append("")
        lines.append("التعلم محدود من -2 إلى +2 ولا يتجاوز موانع Judge.")
        return "\n".join(lines)

    def learning_export_path(self):
        data=self.learning.load(); self.learning.save(data)
        return str(self.learning.path)

    def learning_import(self, raw):
        return self.learning.import_bytes(raw)

    def learning_reset(self):
        self.learning.reset()

    def _record_learning_if_closed(self, trade):
        if trade and trade.get("result") in {"WIN","LOSS"} and getattr(self.s,"learning_enabled",True):
            try: self.learning.record(trade)
            except Exception as exc: print(f"[learning] record failed: {exc}")

    # =========================================================
    # CURSOR
    # =========================================================

    def _next_batch(
        self,
        size,
        cursor_name,
    ):
        if not self.universe:
            return []

        total = len(
            self.universe
        )

        size = min(
            max(
                1,
                int(size),
            ),
            total,
        )

        cursor = getattr(
            self,
            cursor_name,
        )

        end = (
            cursor + size
        )

        if end <= total:

            batch = self.universe[
                cursor:end
            ]

        else:

            batch = (
                self.universe[cursor:]
                + self.universe[
                    : end - total
                ]
            )

        setattr(
            self,
            cursor_name,
            end % total,
        )

        return batch

    # =========================================================
    # MARKET
    # =========================================================

    async def _market(
        self,
        force=False,
    ):
        now = self._utc_now()

        if (
            not force
            and self.last_market_summary
            is not None
            and self.last_market_summary_at
        ):
            age = (now - self.last_market_summary_at).total_seconds()
            if age < self.s.market_cache_seconds:
                return self.last_market_summary

        # Primary provider remains authoritative when it returns valid TASI
        # data. A provider exception/empty payload must not prevent Mubasher
        # from supplying the safe market fallback.
        try:
            data = await self.p.market_summary()
            if not isinstance(data, dict):
                data = {}
        except Exception as exc:
            print(f"[market] primary summary failed: {exc}")
            data = {"market_primary_error": str(exc)}

        data = await self._augment_market_totals(data, force=force)
        data = await self._recover_market_breadth(data, force=force)

        def _num(value):
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        index_value = _num(data.get("index_value", data.get("value", data.get("index", data.get("tasi")))))
        if index_value is None or index_value <= 0:
            print("[market] no valid TASI level after primary + Mubasher fallback")
            return None

        self.last_market_summary = data
        self.last_market_summary_at = now
        return data

    async def _augment_market_totals(self, data, force=False):
        if not isinstance(data, dict):
            data = {}
        if not bool(getattr(self.s, "market_totals_use_mubasher", True)):
            return data

        def _num(value):
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        now = self._utc_now()
        ttl = max(60, int(getattr(self.s, "market_totals_cache_seconds", 600) or 600))
        if (
            not force
            and self.last_market_totals is not None
            and self.last_market_totals_at is not None
            and (now - self.last_market_totals_at).total_seconds() < ttl
        ):
            result = self.last_market_totals
        else:
            result = await self.market_totals_client.fetch()
            self.last_market_totals = result
            self.last_market_totals_at = now

        current_index = _num(data.get("index_value", data.get("value", data.get("index", data.get("tasi")))))
        current_change_raw = data.get("change_percent", data.get("index_change_percent", data.get("change_pct")))
        current_change = _num(current_change_raw)
        current_adv = _num(data.get("advancers", data.get("advancing")))
        current_dec = _num(data.get("decliners", data.get("declining")))
        current_volume = _num(data.get("total_volume", data.get("volume")))
        current_value = _num(data.get("trading_value", data.get("value_traded", data.get("total_value"))))

        if hasattr(self.p, "last_call_provider_detail"):
            primary = self.p.last_call_provider_detail()
        elif hasattr(self.p, "active_provider_detail"):
            primary = self.p.active_provider_detail()
        else:
            primary = self.p.active_provider().upper() if hasattr(self.p, "active_provider") else "DATA"
        result_ok = bool(getattr(result, "ok", False))
        mub_index = _num(getattr(result, "index_value", None)) if result_ok else None
        mub_change = _num(getattr(result, "change_percent", None)) if result_ok else None
        mub_adv = _num(getattr(result, "advancers", None)) if result_ok else None
        mub_dec = _num(getattr(result, "decliners", None)) if result_ok else None
        mub_volume = _num(getattr(result, "volume", None)) if result_ok else None
        mub_value = _num(getattr(result, "trading_value", None)) if result_ok else None

        # V12 requirement: total volume and traded value come from Mubasher when
        # available. If Mubasher fails, preserve the provider values.
        if mub_volume is not None and mub_volume > 0:
            data["total_volume"] = mub_volume
        elif current_volume is not None:
            data["total_volume"] = current_volume

        if mub_value is not None and mub_value > 0:
            data["trading_value"] = mub_value
        elif current_value is not None:
            data["trading_value"] = current_value

        # V13 safety fix: SAHMK can return 0/empty TASI fields while the market
        # is open. Only then use Mubasher for the missing core field. Valid
        # primary values are never overwritten.
        primary_index_valid = current_index is not None and current_index > 0
        if primary_index_valid:
            data["index_value"] = current_index
            data["market_core_source"] = primary
        elif mub_index is not None and mub_index > 0:
            data["index_value"] = mub_index
            data["market_core_source"] = "MUBASHER"

        # A zero percentage is valid only when the provider also supplied a valid
        # index level. If the primary level is invalid, take Mubasher change too.
        if primary_index_valid and current_change is not None:
            data["change_percent"] = current_change
        elif mub_change is not None:
            data["change_percent"] = mub_change

        # 0/0 breadth during an active session is treated as unavailable, not as
        # neutral breadth. Use explicit Mubasher breadth only if its page exposes
        # it; otherwise leave it missing so the quality engine does not invent it.
        breadth_valid = (current_adv or 0) + (current_dec or 0) > 0
        if breadth_valid:
            data["advancers"] = current_adv
            data["decliners"] = current_dec
        elif (mub_adv or 0) + (mub_dec or 0) > 0:
            data["advancers"] = mub_adv
            data["decliners"] = mub_dec
            if data.get("market_core_source") != "MUBASHER":
                data["market_breadth_source"] = "MUBASHER"
        else:
            data["advancers"] = None
            data["decliners"] = None

        if result_ok:
            data["market_totals_source"] = "MUBASHER" if (mub_volume is not None or mub_value is not None) else primary
            data["market_totals_status"] = "ok"
            data["market_totals_reason"] = getattr(result, "reason", "ok")
        else:
            data["market_totals_source"] = primary
            data["market_totals_status"] = "fallback"
            data["market_totals_reason"] = getattr(result, "reason", "unavailable")

        return data

    def _breadth_snapshot_from_quotes(self, quotes, *, source, expected_count=None):
        """Build breadth only from fresh real quotes with meaningful coverage."""
        if isinstance(quotes, dict):
            items = list(quotes.values())
        else:
            items = list(quotes or [])
        fresh = []
        for quote in items:
            ok, _reason, _age = self._quote_freshness(quote)
            if ok:
                fresh.append(quote)
        expected = int(expected_count or len(self.universe) or len(items) or 0)
        coverage = len(fresh) / max(1, expected)
        min_samples = max(1, int(getattr(self.s, "market_breadth_min_samples", 80) or 80))
        min_coverage = float(getattr(self.s, "market_breadth_min_coverage", 0.65) or 0.65)
        if len(fresh) < min_samples or coverage < min_coverage:
            return None
        adv = sum(1 for q in fresh if float(getattr(q, "change_percent", 0) or 0) > 0.01)
        dec = sum(1 for q in fresh if float(getattr(q, "change_percent", 0) or 0) < -0.01)
        unchanged = len(fresh) - adv - dec
        if adv + dec <= 0:
            return None
        return {
            "advancers": adv,
            "decliners": dec,
            "breadth_unchanged": unchanged,
            "breadth_source": str(source),
            "breadth_samples": len(fresh),
            "breadth_expected": expected,
            "breadth_coverage": coverage,
        }

    def _store_market_breadth(self, snapshot):
        if not isinstance(snapshot, dict):
            return
        self.last_market_breadth = dict(snapshot)
        self.last_market_breadth_at = self._utc_now()
        if isinstance(getattr(self, "last_market_summary", None), dict):
            self.last_market_summary.update(snapshot)
            self.last_market_summary_at = self._utc_now()

    async def _recover_market_breadth(self, data, force=False):
        """Recover missing breadth using the cheapest reliable path first.

        Order:
        1) Existing valid breadth from routed summary / Mubasher.
        2) Tasilab /v1/market/status (documented advancing/declining endpoint).
        3) Recent validated breadth cache.
        4) Tasilab full-market quotes with minimum sample/coverage safeguards.
        5) Throttled Yahoo full-market snapshots as the final provider fallback.

        This never spends scarce SAHMK quota just to fill breadth and never
        fabricates 0/0 as a neutral market.
        """
        if not isinstance(data, dict):
            data = {}

        def _pair(payload):
            if not isinstance(payload, dict):
                return None, None
            try:
                adv = float(payload.get("advancers")) if payload.get("advancers") is not None else None
                dec = float(payload.get("decliners")) if payload.get("decliners") is not None else None
            except (TypeError, ValueError):
                return None, None
            return adv, dec

        adv, dec = _pair(data)
        if (adv or 0) + (dec or 0) > 0:
            if not data.get("breadth_source"):
                data["breadth_source"] = data.get("market_breadth_source") or (
                    self.p.active_provider_detail() if hasattr(self.p, "active_provider_detail") else "DATA"
                )
            return data

        universe = getattr(self, "universe", []) or []
        tasilab = getattr(self.p, "tasilab", None)

        # Prefer Tasilab's purpose-built market/status endpoint before any
        # expensive full-market snapshot. TasilabProvider allows this endpoint
        # to operate even if the SINGLE-QUOTE circuit is temporarily open.
        if bool(getattr(self.s, "market_breadth_tasilab_enabled", True)) and tasilab is not None:
            try:
                if hasattr(tasilab, "market_summary"):
                    status = await tasilab.market_summary()
                elif hasattr(tasilab, "market_status"):
                    status = await tasilab.market_status()
                else:
                    status = None
                s_adv, s_dec = _pair(status)
                if (s_adv or 0) + (s_dec or 0) > 0:
                    snapshot = {
                        "advancers": int(s_adv),
                        "decliners": int(s_dec),
                        "breadth_source": "TASILAB_MARKET_STATUS",
                        "breadth_coverage": 1.0,
                    }
                    try:
                        unchanged = int(float((status or {}).get("unchanged", 0) or 0))
                        if unchanged >= 0:
                            snapshot["breadth_unchanged"] = unchanged
                    except (TypeError, ValueError):
                        pass
                    self._store_market_breadth(snapshot)
                    data.update(snapshot)
                    print(
                        f"[market] breadth recovered source=TASILAB_MARKET_STATUS "
                        f"adv={snapshot['advancers']} dec={snapshot['decliners']}"
                    )
                    return data
            except Exception as exc:
                print(f"[market] Tasilab market/status breadth unavailable: {exc}")

        now = self._utc_now()
        ttl = max(60, int(getattr(self.s, "market_breadth_cache_seconds", 900) or 900))
        cached_breadth = getattr(self, "last_market_breadth", None)
        cached_breadth_at = getattr(self, "last_market_breadth_at", None)
        if cached_breadth and cached_breadth_at:
            age = (now - cached_breadth_at).total_seconds()
            if age < ttl:
                data.update(cached_breadth)
                data["breadth_cache_age_seconds"] = int(max(0, age))
                return data

        if not bool(getattr(self.s, "market_breadth_tasilab_enabled", True)) or not universe:
            return data
        if tasilab is None or not hasattr(tasilab, "quotes"):
            return data

        try:
            symbols = [str(x.get("symbol", "")).strip() for x in universe if str(x.get("symbol", "")).strip()]
            quotes = await tasilab.quotes(symbols)
            snapshot = self._breadth_snapshot_from_quotes(
                quotes, source="TASILAB_FULL_MARKET", expected_count=len(universe)
            )
            if snapshot:
                self._store_market_breadth(snapshot)
                data.update(snapshot)
                print(
                    f"[market] breadth recovered source=TASILAB_FULL_MARKET "
                    f"coverage={snapshot['breadth_coverage']:.1%} "
                    f"adv={snapshot['advancers']} dec={snapshot['decliners']}"
                )
        except Exception as exc:
            print(f"[market] Tasilab full-market breadth unavailable: {exc}")

        # Final provider fallback: Yahoo delayed snapshots. This is intentionally
        # throttled because it is much heavier than market/status and is used only
        # after routed summary, Mubasher/Tasilab status, cache and Tasilab full-market
        # have all failed. It never fabricates breadth and uses the same coverage gate.
        adv, dec = _pair(data)
        if (adv or 0) + (dec or 0) <= 0 and bool(getattr(self.s, "market_breadth_yahoo_fallback_enabled", True)):
            hist = getattr(self, "h", None)
            if hist is not None and hasattr(hist, "market_snapshots") and universe:
                now = self._utc_now()
                retry_seconds = max(300, int(getattr(self.s, "market_breadth_yahoo_retry_seconds", 900) or 900))
                last_try = getattr(self, "last_market_breadth_yahoo_attempt_at", None)
                allowed = last_try is None or (now - last_try).total_seconds() >= retry_seconds
                if allowed:
                    self.last_market_breadth_yahoo_attempt_at = now
                    try:
                        symbols = [str(x.get("symbol", "")).strip() for x in universe if str(x.get("symbol", "")).strip()]
                        rows = await hist.market_snapshots(symbols, concurrency=10)
                        snapshot = self._breadth_snapshot_from_quotes(
                            rows, source="YAHOO_FULL_MARKET", expected_count=len(universe)
                        )
                        if snapshot:
                            self._store_market_breadth(snapshot)
                            data.update(snapshot)
                            print(
                                f"[market] breadth recovered source=YAHOO_FULL_MARKET "
                                f"coverage={snapshot['breadth_coverage']:.1%} "
                                f"adv={snapshot['advancers']} dec={snapshot['decliners']}"
                            )
                    except Exception as exc:
                        print(f"[market] Yahoo breadth fallback unavailable: {exc}")
        return data

    # =========================================================
    # HISTORICAL HELPERS
    # =========================================================

    @staticmethod
    def _rows_to_df(
        payload,
    ):
        if not isinstance(
            payload,
            dict,
        ):
            return None

        rows = payload.get(
            "data",
            payload.get(
                "results",
                payload.get(
                    "historical",
                    [],
                ),
            ),
        )

        if (
            not isinstance(
                rows,
                list,
            )
            or len(rows) < 60
        ):
            return None

        df = pd.DataFrame(
            rows
        )

        rename_map = {}

        for column in df.columns:
            key = str(
                column
            ).lower()

            if key in (
                "o",
                "open",
            ):
                rename_map[
                    column
                ] = "open"

            elif key in (
                "h",
                "high",
            ):
                rename_map[
                    column
                ] = "high"

            elif key in (
                "l",
                "low",
            ):
                rename_map[
                    column
                ] = "low"

            elif key in (
                "c",
                "close",
            ):
                rename_map[
                    column
                ] = "close"

            elif key in (
                "v",
                "volume",
            ):
                rename_map[
                    column
                ] = "volume"

        df = df.rename(
            columns=rename_map
        )

        required = {
            "open",
            "high",
            "low",
            "close",
            "volume",
        }

        return (
            df
            if required.issubset(
                df.columns
            )
            else None
        )

    # =========================================================
    # STAGE-1 SEARCH DIAGNOSTICS
    # =========================================================

    @staticmethod
    def _stage1_source_label(source):
        labels = {
            "top_volume": "Top Volume",
            "top_volume_secondary": "Tasilab Top Volume",
            "top_value": "Top Traded Value",
            "top_gainers": "Top Gainers",
            "watchlist": "Watchlist",
            "catalyst_watch": "Catalyst",
            "persistent_watch": "Persistent Leader",
            "persistent_leader": "Persistence",
            "acceleration": "Acceleration",
            "yahoo_full_market_fill": "Yahoo Full-Market Fill",
        }
        return labels.get(str(source or "").strip(), str(source or "").strip() or "غير محدد")

    def _candidate_discovery_text(self, sources):
        clean = []
        for source in list(sources or []):
            label = self._stage1_source_label(source)
            if label and label not in clean:
                clean.append(label)
        return " + ".join(clean) if clean else "ترتيب Stage-1 العام"

    def _stage1_diagnostics_text(self, diag, requested, selected, selection_source=""):
        diag = dict(diag or {})
        sources = dict(diag.get("sources", {}) or {})
        actual_requested = max(1, int(requested or diag.get("requested") or 1))
        actual_selected = max(0, int(selected or 0))
        coverage_pct = min(100.0, (actual_selected / actual_requested) * 100.0)

        source_labels = {
            "volume": "Top Volume",
            "value": "Top Traded Value",
            "gainers": "Top Gainers",
            "watch": "Catalyst / Persistent Watchlist",
        }
        status_labels = {
            "ok": "✅ يعمل",
            "disabled": "◻️ معطل بالإعدادات",
            "idle": "◻️ لا توجد رموز مراقبة",
            "throttled": "⚠️ Rate Limit",
            "unavailable": "❌ غير متاح",
            "fallback_unavailable": "⚠️ غير متاح بعد التحويل الاحتياطي",
            "pending": "⚪ غير محسوم",
        }
        rows = []
        for key in ("volume", "value", "gainers", "watch"):
            info = dict(sources.get(key, {}) or {})
            if not info:
                continue
            status = str(info.get("status", "pending"))
            count = int(info.get("count", 0) or 0)
            provider = str(info.get("provider", "") or "")
            suffix = f" — {count}" if status == "ok" else ""
            if provider and provider not in {"SAHMK", "router"}:
                suffix += f" ({provider})"
            error = str(info.get("error", "") or "").replace("\n", " ").strip()
            if error and status != "ok":
                suffix += f" — {error[:70]}"
            rows.append(f"{source_labels[key]}: {status_labels.get(status, '⚠️ ' + status)}{suffix}")

        provider_quality = str(diag.get("quality", "UNKNOWN") or "UNKNOWN").upper()
        degraded_statuses = {"throttled", "unavailable", "fallback_unavailable", "pending"}
        source_degraded = any(
            bool(info.get("enabled")) and str(info.get("status")) in degraded_statuses
            for info in sources.values()
            if isinstance(info, dict)
        )
        # FULL means genuinely complete, not merely rounded close to 100%.
        # Example: 271/272 == 99.63% must remain DEGRADED and display 99.6%.
        full = (
            actual_selected >= actual_requested
            and coverage_pct >= 100.0
            and provider_quality == "FULL"
            and not source_degraded
        )
        quality_text = "🟢 FULL COVERAGE" if full else "🟡 DEGRADED COVERAGE"
        cache_note = " — ♻️ Cache" if bool(diag.get("cached")) else ""
        mode_note = f"\n🧩 مسار الاكتشاف: {selection_source}" if selection_source else ""
        body = "\n".join(rows) if rows else "⚪ تفاصيل مصادر Stage-1 غير متاحة"
        return (
            "📡 مصادر اكتشاف المرشحين:\n"
            + body
            + f"\n🛡️ جودة الفحص: {quality_text}{cache_note}"
            + f"\n📊 Coverage: {actual_selected}/{actual_requested} ({coverage_pct:.1f}%)"
            + mode_note
        )

    # =========================================================
    # MANUAL SIGNAL
    # =========================================================

    async def scan_once(
        self,
        source="telegram",
        screen_limit_override=None,
        detail_limit_override=None,
        full_market=False,
        trade_horizon="intraday",
        intraday_logic="core",
    ):
        if self.scan_lock.locked():
            return (
                "⏳ يوجد فحص يدوي "
                "جارٍ حاليًا."
            )

        async with self.scan_lock:

            local_now = self._local_now()
            trade_horizon = str(trade_horizon or "intraday").strip().lower()
            if trade_horizon in {"waseem20", "waseem30"}:
                self._waseem20_scan_alerts = []
                self._waseem30_scan_alerts = [] if trade_horizon == "waseem30" else getattr(self, "_waseem30_scan_alerts", [])
            if trade_horizon not in {"intraday", "two_day", "multi_session", "waseem20", "waseem30"}:
                trade_horizon = "intraday"
            intraday_logic = str(intraday_logic or "core").strip().lower()
            if intraday_logic not in {"core", "emerging"}:
                intraday_logic = "core"
            if trade_horizon != "intraday":
                intraday_logic = "core"
            if trade_horizon == "intraday" and not bool(getattr(self.s, "intraday_enabled", True)):
                return "⛔ مسار التداول اليومي متوقف من الإعدادات."
            if trade_horizon == "two_day" and not bool(getattr(self.s, "two_day_enabled", True)):
                return "⛔ مسار فرص اليومين متوقف من الإعدادات."
            if trade_horizon == "multi_session" and not bool(getattr(self.s, "multi_session_enabled", True)):
                return "⛔ مسار متعدد الجلسات متوقف من الإعدادات."
            if trade_horizon in {"waseem20", "waseem30"} and not bool(getattr(self.s, "intraday_enabled", True)):
                return "⛔ محرك وسيم 20 متوقف لأن التداول غير مفعّل من الإعدادات."
            print(
                f"[manual-scan] request source={source} horizon={trade_horizon} "
                f"screen={screen_limit_override} detail={detail_limit_override} "
                f"full_market={full_market} local={local_now.strftime('%H:%M:%S')}"
            )

            self.last_scan = (
                self._utc_now()
            )

            state = self.store.state()

            state["meta"][
                "last_scan"
            ] = self.last_scan.isoformat()

            state["meta"][
                "last_scan_source"
            ] = source

            self.store.save_state(
                state
            )

            # -------------------------------------------------
            # SAFETY
            # -------------------------------------------------

            if self.is_paused():

                return (
                    "⏸️ النظام متوقف مؤقتًا. "
                    "استخدم /resume أولًا."
                )

            if not self.s.paper_mode:

                return (
                    "🛑 PAPER_MODE غير مفعّل؛ "
                    "تم منع إنشاء الصفقة."
                )

            if not self.can_send():

                return (
                    "ℹ️ تم بلوغ حد الصفقات "
                    "المفتوحة أو الإشارات اليومية."
                )

            preopen_waseem = False
            if trade_horizon in {"waseem20", "waseem30"}:
                minute_now = local_now.hour * 60 + local_now.minute
                auction_start = self._minutes(getattr(self.s, "waseem20_opening_auction_start", "09:30"))
                market_open_m = self._minutes(getattr(self.s, "market_open", "10:00"))
                preopen_waseem = local_now.weekday() not in (4, 5) and auction_start <= minute_now < market_open_m

            if (
                not self.s.allow_off_hours_scan
                and not self.market_is_open()
                and not preopen_waseem
            ):
                return (
                    "🌙 السوق السعودي مغلق حاليًا أو خارج نافذة محرك التداول.\n"
                    f"وسيم 20 يبدأ من مزاد الافتتاح {getattr(self.s, 'waseem20_opening_auction_start', '09:30')}، "
                    f"والتداول المستمر {self.s.market_open}–{self.s.market_close} بتوقيت الرياض.\n"
                    "خارج ذلك يحدّث الأخبار والمراقبة فقط ولا ينشئ صفقة من أسعار قديمة."
                )

            # Saudi-market quality window: avoid the noisy opening phase and
            # stop new entries before the closing auction. This affects only
            # signal creation; trade monitoring/reporting remain unchanged.
            if not self.s.allow_off_hours_scan:
                local = self._local_now()
                minute = local.hour * 60 + local.minute
                if trade_horizon in {"waseem20", "waseem30"}:
                    start_text = getattr(self.s, "waseem20_opening_auction_start", "09:30")
                    end_text = getattr(self.s, "waseem20_new_entry_end", "14:50")
                else:
                    start_text = getattr(self.s, "signal_window_start", "10:30")
                    end_text = getattr(self.s, "manual_search_window_end", "14:50")
                start = self._minutes(start_text)
                end = self._minutes(end_text)
                if not (start <= minute <= end):
                    print(f"[manual-scan] blocked by signal window local={local.strftime('%H:%M:%S')} allowed={start_text}-{end_text}")
                    return (
                        "🛡️ فلتر جودة السوق السعودي مفعل.\n"
                        f"نافذة إنشاء الإشارات: {start_text}–"
                        f"{end_text} بتوقيت الرياض.\n"
                        "خارجها يجمع النظام/يراقب فقط ولا يطارد حركة الافتتاح أو الإغلاق."
                    )

            # Provider-level market status catches exchange holidays/special closures
            # that a weekday/time-only gate cannot know about. Unknown status falls
            # back to the local Saudi trading-hours rules above.
            if hasattr(self.p, "market_is_open"):
                try:
                    provider_open = await self.p.market_is_open()
                except Exception as exc:
                    print(f"[market-status] unavailable: {exc}")
                    provider_open = None
                if provider_open is False and not preopen_waseem:
                    return (
                        "🌙 مزود السوق يؤكد أن جلسة تداول السعودية مغلقة حاليًا.\n"
                        "لن يتم إنشاء إشارة جديدة من بيانات جلسة مغلقة/إجازة."
                    )

            # A new explicit scan supersedes any older private preview.
            self._clear_pending_signal()

            # -------------------------------------------------
            # UNIVERSE
            # -------------------------------------------------

            before_ensure = list(self.universe)
            await self._ensure_universe()

            if full_market:
                # _ensure_universe may already have paid for a fresh paginated
                # SAHMK company refresh. Do not immediately repeat those calls.
                # When a plausible complete runtime list is available, treat it
                # as authoritative so old/delisted bootstrap symbols do not live
                # forever. Only union with the cache when the refresh looks partial.
                before = before_ensure or list(self.universe)
                try:
                    age = None
                    if self.last_refresh is not None:
                        age = (self._utc_now() - self.last_refresh).total_seconds()
                    if age is None or age > 60:
                        await self.refresh()
                    refreshed = list(self.universe)
                    plausible_floor = max(200, int(len(before) * 0.80)) if before else 200
                    if len(refreshed) >= plausible_floor:
                        self.universe = refreshed
                        print(
                            f"[full-market] authoritative equity universe cached={len(before)} "
                            f"refreshed={len(refreshed)}"
                        )
                    else:
                        merged = {}
                        for item in before + refreshed:
                            sym = str(item.get("symbol", "")).strip()
                            if sym:
                                merged[sym] = {**merged.get(sym, {}), **item}
                        self.universe = list(merged.values())
                        print(
                            f"[full-market] partial universe refresh={len(refreshed)}; "
                            f"safe union={len(self.universe)}"
                        )
                except Exception as exc:
                    self.universe = before or list(self.universe)
                    print(f"[full-market] universe refresh unavailable; using cached {len(self.universe)} symbols: {exc}")

            self.news.bind_universe(self.universe)
            universe_by_symbol = {
                str(item.get("symbol", "")).strip(): item
                for item in self.universe
                if item.get("symbol")
            }

            # -------------------------------------------------
            # MARKET REGIME
            # -------------------------------------------------

            market_data = (
                await self._market()
            )

            market_ctx = tasi_context(market_data)
            regime = market_ctx["regime"]
            market_quality = self.market_quality_engine.evaluate(market_data)
            if market_quality.state == "NO_TRADE" and not preopen_waseem:
                print(f"[manual-scan] market-quality NO_TRADE reasons={market_quality.reasons}")
                return (
                    "⛔ جودة بيانات/حالة TASI لا تسمح بإنشاء صفقة جديدة حاليًا.\n"
                    + "السبب: " + " | ".join(market_quality.reasons[:3])
                )
            elif market_quality.state == "NO_TRADE" and preopen_waseem:
                print("[waseem20] pre-open market quality unavailable; continuing as catalyst/auction intelligence only")

            multi_market_daily = None
            if trade_horizon in {"two_day", "multi_session", "waseem20"} and self.h is not None and hasattr(self.h, "tasi_daily"):
                try:
                    multi_market_daily, _ = await self.h.tasi_daily()
                except Exception as exc:
                    print(f"[multi-session] TASI daily RS context unavailable: {exc!r}")

            requested_screen = int(screen_limit_override if screen_limit_override is not None else self.s.manual_quotes_per_signal)
            if full_market:
                screen_limit = max(1, len(self.universe))
            else:
                screen_limit = min(max(1, requested_screen), 100)

            detail_cap = 20 if full_market else 15
            detail_limit = min(
                max(
                    1,
                    int(
                        (detail_limit_override if detail_limit_override is not None else self.s.detail_quotes_per_signal)
                    ),
                ),
                detail_cap,
            )

            # -------------------------------------------------
            # SAUDI-NATIVE STAGE-1 CANDIDATE DISCOVERY
            # -------------------------------------------------

            # Preserve a bounded set of catalysts and previous persistent
            # leaders so the next scan can detect acceleration/decay even if
            # the name temporarily falls outside one activity ranking.
            watch_limit = max(0, int(getattr(self.s, "stage1_watchlist_limit", 6) or 6))
            news_watch_symbols = []
            leader_watch_symbols = []
            watch_symbols = []
            if watch_limit:
                news_watch_symbols = list(self.news.watch_symbols(watch_limit))
                leader_watch_symbols = list(self.leadership_tracker.leader_symbols(watch_limit))
                watch_symbols.extend(news_watch_symbols)
                watch_symbols.extend(leader_watch_symbols)
                watch_symbols = list(dict.fromkeys(str(x).strip() for x in watch_symbols if str(x).strip()))[:watch_limit]
            news_watch_set = {str(x).strip() for x in news_watch_symbols if str(x).strip()}
            leader_watch_set = {str(x).strip() for x in leader_watch_symbols if str(x).strip()}

            stage1_diag = {}
            try:
                if hasattr(self.p, "active_candidate_quotes"):
                    screening_quotes = await self.p.active_candidate_quotes(
                        screen_limit,
                        "TASI",
                        watch_symbols=watch_symbols,
                    )
                    selection_source = "volume+value+gainers+watch"
                    if hasattr(self.p, "candidate_pool_diagnostics"):
                        stage1_diag = self.p.candidate_pool_diagnostics()
                else:
                    screening_quotes = await self.p.top_volume_quotes(
                        screen_limit,
                        "TASI",
                    )
                    selection_source = "top_volume_legacy"
                    stage1_diag = {
                        "requested": screen_limit,
                        "selected": len(screening_quotes),
                        "coverage_ratio": len(screening_quotes) / max(1, screen_limit),
                        "quality": "FULL" if len(screening_quotes) >= screen_limit else "DEGRADED",
                        "cached": False,
                        "mode": "top_volume_legacy",
                        "sources": {
                            "volume": {"enabled": True, "status": "ok", "count": len(screening_quotes), "provider": "router"},
                        },
                    }

            except Exception as exc:

                print(
                    "[manual-scan] "
                    "candidate-pool failed: "
                    f"{exc}"
                )

                screening_quotes = []
                stage1_diag = {
                    "requested": screen_limit,
                    "selected": 0,
                    "coverage_ratio": 0.0,
                    "quality": "DEGRADED",
                    "cached": False,
                    "mode": "candidate_pool_failed",
                    "sources": {},
                }

                selection_source = (
                    "fallback"
                )

            # Full-market mode must actually cover the listed universe, not just
            # the provider's top-volume/top-value rows. Fill missing symbols with
            # delayed Yahoo stage-1 snapshots; finalists are refreshed from the
            # primary router before any trade decision.
            if full_market and self.h is not None and hasattr(self.h, "market_snapshots"):
                have = {str(getattr(q, "symbol", "")).strip() for q in screening_quotes}
                missing = [str(x.get("symbol", "")).strip() for x in self.universe if str(x.get("symbol", "")).strip() not in have]
                if missing:
                    print(f"[full-market] provider rows={len(screening_quotes)}; Yahoo stage1 filling {len(missing)} symbols")
                    yahoo_rows = await self.h.market_snapshots(missing, concurrency=10)
                    merged = list(screening_quotes)
                    seen = {str(getattr(q, "symbol", "")).strip() for q in merged}
                    for q in yahoo_rows:
                        sym = str(getattr(q, "symbol", "")).strip()
                        if sym and sym not in seen:
                            try:
                                raw = dict(getattr(q, "raw", None) or {})
                                tags = list(raw.get("stage1_sources", []) or [])
                                if "yahoo_full_market_fill" not in tags:
                                    tags.append("yahoo_full_market_fill")
                                raw["stage1_sources"] = tags
                                q.raw = raw
                            except Exception:
                                pass
                            merged.append(q); seen.add(sym)
                    screening_quotes = merged[:screen_limit]
                    selection_source = "full_market_provider+yahoo"
                    print(f"[full-market] stage1 coverage={len(screening_quotes)}/{screen_limit}")

            # -------------------------------------------------
            # FALLBACK
            # -------------------------------------------------

            if not screening_quotes:

                fallback_items = (
                    self._next_batch(
                        detail_limit,
                        "scan_cursor",
                    )
                )

                fallback_symbols = [
                    str(
                        x.get(
                            "symbol",
                            "",
                        )
                    ).strip()
                    for x in fallback_items
                    if x.get(
                        "symbol"
                    )
                ]

                details = await self.p.quotes(
                    fallback_symbols
                )

                screening_quotes = list(
                    details.values()
                )
                selection_source = "fallback_single_quotes"
                stage1_diag = {
                    "requested": screen_limit,
                    "selected": len(screening_quotes),
                    "coverage_ratio": len(screening_quotes) / max(1, screen_limit),
                    "quality": "DEGRADED",
                    "cached": False,
                    "mode": "fallback_single_quotes",
                    "sources": {},
                }

            # -------------------------------------------------
            # FRESH DATA
            # -------------------------------------------------

            fresh_screening = []
            freshness_rejected = {}
            for q in screening_quotes:
                ok, reason, age = self._quote_freshness(q)
                symbol = getattr(q, "symbol", "?") if q is not None else "?"
                # During the official opening auction WASEEM may use a previous-close
                # quote only for a news/catalyst WATCH plan. It can never become
                # TRADE_READY before 10:00, and the message exposes the data timestamp.
                preopen_context_ok = bool(preopen_waseem and reason == "stale" and age is not None and age <= 24*60 and str(symbol) in news_watch_set)
                if ok or preopen_context_ok:
                    fresh_screening.append(q)
                else:
                    freshness_rejected[reason] = freshness_rejected.get(reason, 0) + 1
                    age_text = f" age={age:.1f}m" if age is not None else ""
                    print(f"[freshness] reject {symbol}: {reason}{age_text}")

            # Persist scan-to-scan Relative Strength so later 11:00/12:00/13:00
            # scans can distinguish persistent leaders from opening spikes.
            self.leadership_tracker.update(
                fresh_screening, float(market_ctx.get("change_percent", 0) or 0), now=self._utc_now()
            )

            # Build a real-data sector participation proxy from the same fresh
            # liquid names already fetched for this scan. We do not fabricate a
            # sector score when metadata/sample size is insufficient.
            sector_samples = {}
            for q in fresh_screening:
                meta = universe_by_symbol.get(str(getattr(q, "symbol", "")).strip(), {})
                sector_name = str(meta.get("sector", "") or "").strip()
                if sector_name:
                    sector_samples.setdefault(sector_name, []).append(float(getattr(q, "change_percent", 0) or 0))
            sector_strength = {}
            for name, vals in sector_samples.items():
                if len(vals) < 3:
                    continue
                sector_strength[name] = {
                    "change_percent": float(median(vals)),
                    "samples": len(vals),
                    "breadth": sum(1 for v in vals if v > 0) / len(vals),
                }

            # In full-market mode, broad fresh coverage is more representative
            # of Saudi market breadth than a stale/partial summary endpoint. Use
            # it to refresh advancers/decliners and the regime, but only when the
            # sample covers a meaningful majority of the listed universe.
            if full_market and self.universe:
                coverage = len(fresh_screening) / max(1, len(self.universe))
                if len(fresh_screening) >= 80 and coverage >= 0.65:
                    adv = sum(1 for q in fresh_screening if float(getattr(q, "change_percent", 0) or 0) > 0.01)
                    dec = sum(1 for q in fresh_screening if float(getattr(q, "change_percent", 0) or 0) < -0.01)
                    unchanged = len(fresh_screening) - adv - dec
                    enriched_market = dict(market_data or {})
                    enriched_market["advancers"] = adv
                    enriched_market["decliners"] = dec
                    enriched_market["breadth_source"] = "FULL_MARKET_SCAN"
                    enriched_market["breadth_unchanged"] = unchanged
                    enriched_market["breadth_samples"] = len(fresh_screening)
                    enriched_market["breadth_expected"] = len(self.universe)
                    enriched_market["breadth_coverage"] = coverage
                    self._store_market_breadth({
                        "advancers": adv,
                        "decliners": dec,
                        "breadth_unchanged": unchanged,
                        "breadth_source": "FULL_MARKET_SCAN",
                        "breadth_samples": len(fresh_screening),
                        "breadth_expected": len(self.universe),
                        "breadth_coverage": coverage,
                    })
                    market_data = enriched_market
                    market_ctx = tasi_context(market_data)
                    regime = market_ctx["regime"]
                    market_quality = self.market_quality_engine.evaluate(market_data)
                    print(
                        f"[full-market] breadth recalibrated coverage={coverage:.1%} "
                        f"adv={adv} dec={dec} unchanged={unchanged} regime={regime} "
                        f"quality={market_quality.state} required={market_quality.required_score:.1f}"
                    )

            provider_name = (
                self.p.active_provider()
                if hasattr(self.p, "active_provider")
                else "unknown"
            )

            print(
                "[manual-scan] "
                f"source={source} "
                f"provider={provider_name} "
                f"selection={selection_source} "
                f"screened={len(fresh_screening)}/{len(screening_quotes)} "
                f"universe={len(self.universe)} "
                f"rejected={freshness_rejected}"
            )

            # -------------------------------------------------
            # FAST SCORE
            # -------------------------------------------------

            # Saudi-native Stage-1: this is a RANKER, not a hard gate.
            # The active-universe endpoints have already done the cheap liquidity
            # selection. Requiring every candidate to clear a US-style fast-score
            # threshold caused entire TASI scans to stop before technical analysis.
            ranked_candidates = []
            preferred = []
            threshold = max(55.0, float(self.s.min_score) - 25.0)

            for quote in fresh_screening:
                try:
                    candidate = fast_score(quote, regime, float(market_ctx.get("change_percent", 0) or 0))

                    # V10 discovery overlay: reward *persistent/accelerating* Saudi
                    # leaders without converting these observations into hard
                    # trade gates. This is what lets a Thimar/Fisheries-style
                    # mover climb into Deep Analysis after a quiet opening.
                    persistence_score, decay, persistence_reasons = self.leadership_tracker.persistence(quote.symbol)
                    acceleration, acceleration_reasons = self.leadership_tracker.acceleration(quote.symbol)
                    catalyst = self.news.for_symbol(quote.symbol, now=self._utc_now())

                    p_cap = max(0.0, float(getattr(self.s, "stage1_persistence_bonus_max", 6.0) or 6.0))
                    a_cap = max(0.0, float(getattr(self.s, "stage1_acceleration_bonus_max", 8.0) or 8.0))
                    persistence_boost = max(-4.0, min(p_cap, (float(persistence_score) - 50.0) / 7.5))
                    if acceleration >= 0:
                        acceleration_boost = min(a_cap, float(acceleration) * 3.0)
                    else:
                        acceleration_boost = max(-8.0, float(acceleration) * 2.0)
                    if decay >= 3.0:
                        acceleration_boost -= min(6.0, decay)

                    catalyst_score = float(catalyst.get("score", 0.0) or 0.0)
                    if catalyst_score > 0:
                        catalyst_boost = min(4.0, catalyst_score * 1.5)
                    elif catalyst_score < 0:
                        catalyst_boost = max(-4.0, catalyst_score)
                    elif catalyst.get("available") and catalyst.get("impact") == "HIGH":
                        catalyst_boost = 1.5  # material context merits analysis, not automatic BUY
                    else:
                        catalyst_boost = 0.0

                    classic_stage1_score = max(0.0, min(100.0, candidate.score + persistence_boost + acceleration_boost + catalyst_boost))
                    emerging_snapshot = None
                    if (trade_horizon == "intraday" and intraday_logic == "emerging") or trade_horizon in {"waseem20", "waseem30"}:
                        emerging_snapshot = stage1_emerging_score(
                            quote, float(market_ctx.get("change_percent", 0) or 0),
                            acceleration=float(acceleration or 0.0), persistence=float(persistence_score or 50.0),
                            min_traded_value=self._effective_min_traded_value(),
                            daily_limit_pct=self._daily_limit_pct(quote),
                            near_limit_buffer_pct=float(getattr(self.s, "near_limit_buffer_pct", 0.75) or 0.75),
                        )
                        if trade_horizon == "waseem30":
                            w30_cfg = dict(self.store.state().get("waseem30_scanner") or {})
                            prev30 = dict((w30_cfg.get("snapshots") or {}).get(str(quote.symbol)) or {})
                            early_rank, early_reasons, early_snap = stage1_waseem30_score(
                                quote, float(market_ctx.get("change_percent", 0) or 0),
                                previous=prev30, min_traded_value=self._effective_min_traded_value(),
                            )
                            # W30 prioritizes abnormal flow/RS acceleration before a large price jump.
                            candidate.score = max(0.0, min(100.0, classic_stage1_score * 0.20 + emerging_snapshot.score * 0.20 + early_rank * 0.60))
                            candidate.reasons.extend(early_reasons[:3])
                            raw30 = dict(getattr(quote, "raw", None) or {})
                            raw30["waseem30_stage1_score"] = round(float(early_rank), 2)
                            raw30["waseem30_stage1_snapshot"] = early_snap
                            quote.raw = raw30
                        else:
                            # Legacy behavior retained for WASEEM20 and Emerging Leader.
                            candidate.score = max(0.0, min(100.0, classic_stage1_score * 0.35 + emerging_snapshot.score * 0.65))
                            candidate.reasons.extend(emerging_snapshot.reasons[:3])
                    else:
                        candidate.score = classic_stage1_score
                    if abs(persistence_boost) >= 0.5:
                        candidate.reasons.extend(persistence_reasons[:1])
                    if abs(acceleration_boost) >= 0.5:
                        candidate.reasons.extend(acceleration_reasons[:1])
                    if catalyst_boost:
                        candidate.reasons.append(f"catalyst_context={catalyst_boost:+.1f}")

                    # Keep transparent discovery provenance on the candidate so
                    # Telegram can explain *why this stock entered Deep Analysis*.
                    raw = dict(getattr(quote, "raw", None) or {})
                    if emerging_snapshot is not None:
                        raw["emerging_stage1_score"] = round(float(emerging_snapshot.score), 2)
                        raw["emerging_state"] = str(emerging_snapshot.state)
                        raw["emerging_relative_strength"] = round(float(emerging_snapshot.relative_strength), 3)
                        raw["emerging_acceleration"] = round(float(emerging_snapshot.acceleration), 3)
                    sources = list(raw.get("stage1_sources", []) or [])
                    qsym = str(getattr(quote, "symbol", "") or "").strip()
                    if qsym in news_watch_set and "catalyst_watch" not in sources:
                        sources.append("catalyst_watch")
                    if qsym in leader_watch_set and "persistent_watch" not in sources:
                        sources.append("persistent_watch")
                    if persistence_boost >= 0.5 and "persistent_leader" not in sources:
                        sources.append("persistent_leader")
                    if acceleration_boost >= 0.5 and "acceleration" not in sources:
                        sources.append("acceleration")
                    if emerging_snapshot is not None and emerging_snapshot.score >= float(getattr(self.s, "emerging_leader_min_score", 68.0) or 68.0):
                        if "emerging_leader" not in sources:
                            sources.append("emerging_leader")
                    raw["stage1_sources"] = list(dict.fromkeys(sources))
                    quote.raw = raw
                    if sources:
                        candidate.reasons.append("stage1:" + "+".join(sources))

                    ranked_candidates.append(candidate)
                    if candidate.score >= threshold:
                        preferred.append(candidate)
                except Exception as exc:
                    print(f"[score] {quote.symbol} failed: {exc}")

            ranked_candidates.sort(
                key=lambda c: (c.score, c.quote.value, c.quote.volume),
                reverse=True,
            )
            preferred.sort(
                key=lambda c: (c.score, c.quote.value, c.quote.volume),
                reverse=True,
            )

            # Fill the deep-analysis slots with the highest-ranked active names even
            # when their coarse score is below the preferred threshold. Judge/Hunter
            # remain responsible for the real trading decision.
            finalists = preferred[:detail_limit]
            chosen = {c.quote.symbol for c in finalists}
            if len(finalists) < detail_limit:
                for candidate in ranked_candidates:
                    if candidate.quote.symbol in chosen:
                        continue
                    finalists.append(candidate)
                    chosen.add(candidate.quote.symbol)
                    if len(finalists) >= detail_limit:
                        break

            print(
                "[manual-scan] stage1 "
                f"fresh={len(fresh_screening)} "
                f"preferred_threshold={threshold:.1f} "
                f"preferred={len(preferred)} "
                f"finalists={len(finalists)}/{detail_limit} "
                f"intraday_logic={intraday_logic if trade_horizon == 'intraday' else 'n/a'}"
            )

            stage1_diag_text = self._stage1_diagnostics_text(
                stage1_diag, screen_limit, len(screening_quotes), selection_source
            )
            finalist_sources = {
                str(c.quote.symbol): list((getattr(c.quote, "raw", None) or {}).get("stage1_sources", []) or [])
                for c in finalists
            }

            if not finalists:
                return (
                    "🔎 اكتمل الفحص اليدوي.\n"
                    f"🎯 أسهم نشطة مستهدفة: {len(screening_quotes)}\n"
                    f"✅ بيانات حديثة صالحة: {len(fresh_screening)}\n"
                    "⚠️ لم تتوفر بيانات كافية لإرسال أي سهم للتحليل العميق.\n\n"
                    + stage1_diag_text
                )

            # -------------------------------------------------
            # DETAILED QUOTES
            # -------------------------------------------------

            finalist_symbols = [c.quote.symbol for c in finalists]
            detailed_quotes = await self.p.quotes(finalist_symbols)
            detailed_quotes = dict(detailed_quotes or {})

            # V18 deep-quote recovery. A wide/full-market Stage-1 scan can be
            # healthy while a separate detail endpoint returns no rows or stale
            # timestamps. Do not drop every finalist immediately. Recover in a
            # conservative chain:
            #   primary/router detail quotes -> monitor path (Tasilab-first) ->
            #   the already-fresh Stage-1 quote used to rank the candidate.
            # The Stage-1 fallback is accepted only when it passes the same
            # freshness validator, so no synthetic/stale price is invented.
            detail_recovery = {"monitor": 0, "stage1": 0, "unresolved": 0}
            missing_symbols = [
                symbol for symbol in finalist_symbols
                if not self._fresh_quote(detailed_quotes.get(symbol))
            ]

            if missing_symbols and hasattr(self.p, "monitor_quotes"):
                try:
                    recovered = await self.p.monitor_quotes(missing_symbols)
                    for symbol, recovered_quote in dict(recovered or {}).items():
                        if symbol in missing_symbols and self._fresh_quote(recovered_quote):
                            detailed_quotes[symbol] = recovered_quote
                            detail_recovery["monitor"] += 1
                except Exception as exc:
                    print(f"[manual-scan] detail recovery monitor path failed: {exc}")

            stage1_by_symbol = {
                str(c.quote.symbol): c.quote for c in finalists
            }
            for symbol in finalist_symbols:
                if self._fresh_quote(detailed_quotes.get(symbol)):
                    continue
                stage1_quote = stage1_by_symbol.get(str(symbol))
                if self._fresh_quote(stage1_quote):
                    try:
                        raw = dict(getattr(stage1_quote, "raw", None) or {})
                        raw["deep_quote_recovery"] = "stage1_fresh_fallback"
                        stage1_quote.raw = raw
                    except Exception:
                        pass
                    detailed_quotes[symbol] = stage1_quote
                    detail_recovery["stage1"] += 1
                else:
                    detail_recovery["unresolved"] += 1

            usable_detailed_quotes = {}
            for symbol, quote in detailed_quotes.items():
                if symbol not in finalist_symbols:
                    continue
                ok, reason, age = self._quote_freshness(quote)
                preopen_context_ok = bool(preopen_waseem and reason == "stale" and age is not None and age <= 24*60 and str(symbol) in news_watch_set)
                if ok or preopen_context_ok:
                    usable_detailed_quotes[symbol] = quote
            detailed_quotes = usable_detailed_quotes

            print(
                "[manual-scan] detailed "
                f"usable={len(detailed_quotes)}/{len(finalists)} "
                f"recovered_monitor={detail_recovery['monitor']} "
                f"recovered_stage1={detail_recovery['stage1']} "
                f"unresolved={detail_recovery['unresolved']}"
            )

            engine = SignalEngine(
                self.s,
                self.store.history(),
            )

            # -------------------------------------------------
            # PROFESSIONAL ANALYSIS
            # -------------------------------------------------
            scan_diag = {"TRADE_READY": 0, "WAIT": 0, "SETUP": 0, "LEADER": 0, "RADAR": 0, "WAIT_PULLBACK": 0, "NO_CHASE": 0, "INVALIDATED": 0, "DATA_SKIP": 0, "POST_BUILD_DROP": 0}
            skip_reasons = {}
            near_candidates = []

            def mark_skip(reason):
                scan_diag["DATA_SKIP"] += 1
                skip_reasons[reason] = skip_reasons.get(reason, 0) + 1

            for preliminary_candidate in finalists:

                symbol = (
                    preliminary_candidate
                    .quote
                    .symbol
                )
                discovery_sources = list(finalist_sources.get(str(symbol), []) or [])
                discovery_text = self._candidate_discovery_text(discovery_sources)

                quote = detailed_quotes.get(
                    symbol
                )

                q_ok, q_reason, q_age = self._quote_freshness(quote) if quote is not None else (False, "missing_quote", None)
                preopen_detail_ok = bool(preopen_waseem and q_reason == "stale" and q_age is not None and q_age <= 24*60 and str(symbol) in news_watch_set)
                if quote is None or (not q_ok and not preopen_detail_ok):
                    mark_skip("تفاصيل السعر مفقودة/قديمة")
                    continue

                candidate = fast_score(
                    quote,
                    regime,
                    float(market_ctx.get("change_percent", 0) or 0),
                )

                item = universe_by_symbol.get(
                    symbol,
                    {},
                )

                sector = item.get(
                    "sector",
                    "",
                )

                try:

                    if self.h is None:

                        print(
                            f"[analysis] {symbol}: "
                            "historical provider "
                            "unavailable"
                        )

                        mark_skip("مزود البيانات التاريخية غير متاح")
                        continue

                    datasets = await self.h.datasets(
                        symbol
                    )

                    intraday_df = datasets.get(
                        "intraday"
                    )

                    daily_df = datasets.get(
                        "daily"
                    )

                    # Both the intraday entry frame and the daily context must
                    # be consistent with the current SAHMK quote. Daily validation
                    # remains loose enough for delayed research data; intraday is
                    # intentionally tighter because it drives the entry decision.
                    if not self.h.validate_against_quote(
                        daily_df, quote.price, self.s.historical_max_price_gap_pct
                    ):
                        print(f"[analysis] {symbol}: daily historical price validation failed")
                        mark_skip("فجوة السعر مع البيانات اليومية")
                        continue

                    intraday_gap_limit = min(
                        float(self.s.historical_max_price_gap_pct),
                        float(getattr(self.s, "intraday_max_price_gap_pct", 4.0)),
                    )
                    if (not preopen_waseem) and not self.h.validate_against_quote(
                        intraday_df, quote.price, intraday_gap_limit
                    ):
                        print(f"[analysis] {symbol}: intraday price validation failed")
                        mark_skip("فجوة السعر مع بيانات 15 دقيقة")
                        continue

                    if (
                        intraday_df is None
                        or len(intraday_df) < self.s.intraday_min_bars
                        or daily_df is None
                        or len(daily_df) < self.s.swing_min_bars
                    ):
                        mark_skip("عدد الشموع التاريخية غير كافٍ")
                        continue

                    hist_stamp = self.h.last_stamp(intraday_df)
                    if hist_stamp is None:
                        mark_skip("وقت آخر شمعة غير متاح")
                        continue
                    hist_age = (self._utc_now() - hist_stamp.astimezone(timezone.utc)).total_seconds() / 60.0
                    max_hist_age = float(getattr(self.s, "historical_intraday_max_age_minutes", 45))
                    if hist_age < -5 or (hist_age > max_hist_age and not preopen_waseem):
                        print(f"[analysis] {symbol}: intraday historical stale age={hist_age:.1f}m")
                        mark_skip("بيانات 15 دقيقة قديمة")
                        continue

                    h1_df = resample_ohlcv(intraday_df, "60min")
                    # EMA20 on the higher frame needs enough completed history.
                    # Fail explicitly rather than letting a short/partial 60m
                    # series masquerade as a weak technical setup.
                    if h1_df is None or len(h1_df) < 25:
                        print(f"[analysis] {symbol}: insufficient 60m confirmation bars ({0 if h1_df is None else len(h1_df)})")
                        mark_skip("بيانات 60 دقيقة غير كافية")
                        continue
                    candidate_market_ctx = dict(market_ctx)
                    sector_info = sector_strength.get(sector) if sector else None
                    if sector_info:
                        candidate_market_ctx["sector_strength_available"] = True
                        candidate_market_ctx["sector_strength_pct"] = float(sector_info["change_percent"])
                        candidate_market_ctx["sector_strength_samples"] = int(sector_info["samples"])
                        candidate_market_ctx["sector_strength_breadth"] = float(sector_info.get("breadth", 0.5))
                    else:
                        candidate_market_ctx["sector_strength_available"] = False

                    if trade_horizon in {"two_day", "multi_session"}:
                        assessment = assess_multi_session(
                            daily_df, regime, higher_tf_df=h1_df, intraday_df=intraday_df,
                            market_context=candidate_market_ctx,
                        )
                    else:
                        assessment = assess_intraday(
                            intraday_df, regime, higher_tf_df=h1_df, daily_df=daily_df,
                            market_context=candidate_market_ctx,
                        )
                    signal = None
                    if assessment is not None:
                        if trade_horizon == "waseem30":
                            assessment.strategy = "WASEEM30_EARLY_HUNTER"
                            assessment.trade_type = "وسيم 30 — Early Hunter"
                            assessment.reasons.insert(0, "المحرك: وسيم 30 — صيد مبكر + تأكيد + Anti-Chase")
                        elif trade_horizon == "waseem20":
                            assessment.strategy = "WASEEM20_UNIFIED"
                            assessment.trade_type = "وسيم 20 — أفق ذكي موحّد"
                            assessment.reasons.insert(0, "المحرك: وسيم 20 — Pre-Open + Intraday + 1–2 + 2–5 جلسات")
                        elif trade_horizon == "intraday" and intraday_logic == "emerging":
                            assessment.strategy = "SAUDI_INTRADAY_EMERGING_LEADER"
                            assessment.trade_type = "تداول يومي — صائد القادة"
                            assessment.reasons.insert(0, "المحرك: Emerging Leader Hunter")
                        elif trade_horizon == "intraday":
                            assessment.reasons.insert(0, "المحرك: Core Quality Engine")
                        persistence_score, momentum_decay, persistence_reasons = self.leadership_tracker.persistence(symbol)
                        if trade_horizon in {"two_day", "multi_session", "waseem20"}:
                            multi_rs_score, multi_rs_metrics, multi_rs_reasons = self._multi_session_relative_strength(
                                daily_df, multi_market_daily
                            )
                            persistence_score = (float(persistence_score) * 0.35) + (float(multi_rs_score) * 0.65)
                            persistence_reasons.extend(multi_rs_reasons)
                            assessment.features.update(multi_rs_metrics)
                        catalyst = self.news.for_symbol(symbol, now=self._utc_now())
                        sector_pct = float((sector_strength.get(sector) or {}).get("change_percent", 0) or 0) if sector else None
                        leadership_score, leadership_reasons = compute_leadership_score(
                            stock_change_pct=float(getattr(quote, "change_percent", 0) or 0),
                            market_change_pct=float(market_ctx.get("change_percent", 0) or 0),
                            traded_value=float(getattr(quote, "value", 0) or 0),
                            min_traded_value=self._effective_min_traded_value(),
                            sector_change_pct=sector_pct,
                            persistence_score=persistence_score,
                            catalyst_score=float(catalyst.get("score", 0) or 0),
                        )
                        daily_limit_pct = self._daily_limit_pct(quote)
                        near_limit_buffer = float(getattr(self.s, "near_limit_buffer_pct", 0.75) or 0.75)
                        entry_features = assessment.features
                        if trade_horizon in {"two_day", "multi_session"}:
                            entry_features = latest_features(intraday_df) or assessment.features
                        entry_quality_score, entry_reasons = leadership_entry_quality(
                            entry_features,
                            change_pct=float(getattr(quote, "change_percent", 0) or 0),
                            daily_limit_pct=daily_limit_pct,
                            near_buffer_pct=near_limit_buffer,
                        )
                        current_limit_state = classify_limit_state(
                            float(getattr(quote, "change_percent", 0) or 0),
                            daily_limit_pct=daily_limit_pct,
                            near_buffer_pct=near_limit_buffer,
                        )
                        emerging_mtf_score = None
                        emerging_exec_state = None
                        if (trade_horizon == "intraday" and intraday_logic == "emerging") or trade_horizon in {"waseem20", "waseem30"}:
                            emerging_mtf_score, emerging_mtf_reasons = mtf_consensus_score(assessment.features)
                            emerging_exec_state = execution_state(
                                leadership_score=leadership_score, entry_quality_score=entry_quality_score,
                                mtf_score=emerging_mtf_score, limit_state=current_limit_state,
                                features=assessment.features,
                            )
                            assessment.features["emerging_mtf_score"] = float(emerging_mtf_score)
                            assessment.features["emerging_execution_state"] = str(emerging_exec_state)
                            assessment.reasons.extend(emerging_mtf_reasons[:2])
                            assessment.reasons.append(f"Emerging Leader State={emerging_exec_state}")
                        assessment.reasons.extend(leadership_reasons[:3] + persistence_reasons[:2] + entry_reasons[:2])
                        if trade_horizon in {"two_day", "multi_session"}:
                            assessment.strategy = "SAUDI_TWO_DAY_NATIVE" if trade_horizon == "two_day" else "SAUDI_MULTI_SESSION_NATIVE"
                            assessment.trade_type = "فرصة 1–2 جلسة" if trade_horizon == "two_day" else "متعدد الجلسات 2–5"
                            hunter = build_multi_session_hunter(
                                assessment, leadership_score=leadership_score,
                                entry_quality_score=entry_quality_score, persistence_score=persistence_score,
                            )
                        else:
                            hunter = build_intraday_hunter(
                                assessment, leadership_score=leadership_score,
                                entry_quality_score=entry_quality_score, persistence_score=persistence_score,
                            )
                            if intraday_logic == "emerging" and emerging_mtf_score is not None:
                                mtf_weight = max(0.0, min(0.25, float(getattr(self.s, "emerging_mtf_weight", 0.12) or 0.12)))
                                max_boost = max(0.0, float(getattr(self.s, "emerging_max_hunter_boost", 8.0) or 8.0))
                                mtf_delta = (float(emerging_mtf_score) - 50.0) * mtf_weight
                                mtf_delta = max(-max_boost, min(max_boost, mtf_delta))
                                # This modifies discovery/quality confidence only. The same
                                # liquidity, anti-chase, setup, RR and Judge blockers remain.
                                hunter.score = max(0.0, min(100.0, float(hunter.score) + mtf_delta))
                                hunter.reasons.append(f"MTF Emerging overlay {mtf_delta:+.1f}")
                        # Learning is context-matched and bounded to ±2. It cannot
                        # override any Judge blocker or data/liquidity hard gate.
                        learning_context = {
                            "strategy": assessment.strategy,
                            "direction": "BUY",
                            "market_state": market_quality.state,
                            "liquidity_state": "UNKNOWN",
                        }
                        learning_stats = self.learning.stats(learning_context) if getattr(self.s, "learning_enabled", True) else {"adjustment": 0.0}
                        sector_info = sector_strength.get(sector) if sector else None
                        sector_exposure_count = sum(
                            1 for t in self.store.state().get("open_trades", [])
                            if sector and str(t.get("sector", "")) == str(sector)
                            and t.get("status") in {"WAITING_ENTRY", "OPEN"}
                        )
                        judge_result = judge_candidate(
                            hunter, market_quality,
                            traded_value=float(getattr(quote, "value", 0) or 0),
                            min_traded_value=self._effective_min_traded_value(),
                            sector_strength_available=bool(sector_info),
                            sector_strength_pct=float((sector_info or {}).get("change_percent", 0) or 0),
                            sector_strength_breadth=float((sector_info or {}).get("breadth", 0.5) or 0.5),
                            learning_stats=learning_stats, sector_exposure_count=sector_exposure_count,
                            bid=getattr(quote,"bid",None), ask=getattr(quote,"ask",None),
                            stock_change_pct=float(getattr(quote, "change_percent", 0) or 0),
                            market_change_pct=float(market_ctx.get("change_percent", 0) or 0),
                            leadership_score=leadership_score, entry_quality_score=entry_quality_score,
                            persistence_score=persistence_score, catalyst_context=catalyst,
                            limit_state=current_limit_state, momentum_decay=momentum_decay, horizon=trade_horizon,
                        )
                        # Re-evaluate learning against the resolved liquidity bucket.
                        if getattr(self.s, "learning_enabled", True):
                            learning_stats = self.learning.stats({
                                "strategy": assessment.strategy, "direction": "BUY",
                                "market_state": judge_result.market_state,
                                "liquidity_state": judge_result.liquidity_state,
                            })
                            judge_result = judge_candidate(
                                hunter, market_quality,
                                traded_value=float(getattr(quote, "value", 0) or 0),
                                min_traded_value=self._effective_min_traded_value(),
                                sector_strength_available=bool(sector_info),
                                sector_strength_pct=float((sector_info or {}).get("change_percent", 0) or 0),
                                sector_strength_breadth=float((sector_info or {}).get("breadth", 0.5) or 0.5),
                                learning_stats=learning_stats, sector_exposure_count=sector_exposure_count,
                                bid=getattr(quote,"bid",None), ask=getattr(quote,"ask",None),
                                stock_change_pct=float(getattr(quote, "change_percent", 0) or 0),
                                market_change_pct=float(market_ctx.get("change_percent", 0) or 0),
                                leadership_score=leadership_score, entry_quality_score=entry_quality_score,
                                persistence_score=persistence_score, catalyst_context=catalyst,
                                limit_state=current_limit_state, momentum_decay=momentum_decay, horizon=trade_horizon,
                            )
                        print(
                            f"[judge] {symbol} horizon={trade_horizon} leadership={leadership_score:.1f} "
                            f"entry={entry_quality_score:.1f} persistence={persistence_score:.1f} "
                            f"judge={judge_result.score:.1f}/{judge_result.required_score:.1f} legacy={judge_result.decision}"
                        )
                        if trade_horizon == "waseem30":
                            w30_state = self.store.state()
                            w30_cfg = dict(w30_state.get("waseem30_scanner") or {})
                            previous_snapshot = dict((w30_cfg.get("snapshots") or {}).get(str(symbol)) or {})
                            native = evaluate_waseem30(
                                features=assessment.features, quote=quote, market_context=candidate_market_ctx,
                                catalyst_context=catalyst, leadership_score=leadership_score,
                                persistence_score=persistence_score,
                                min_traded_value=self._effective_min_traded_value(),
                                local_now=self._local_now(), liquidity_state=judge_result.liquidity_state,
                                limit_state=current_limit_state, previous_snapshot=previous_snapshot,
                            )
                        elif trade_horizon == "waseem20":
                            native = evaluate_waseem20(
                                features=assessment.features, quote=quote, market_context=candidate_market_ctx,
                                catalyst_context=catalyst, leadership_score=leadership_score,
                                persistence_score=persistence_score,
                                min_traded_value=self._effective_min_traded_value(),
                                local_now=self._local_now(), liquidity_state=judge_result.liquidity_state,
                                limit_state=current_limit_state,
                            )
                            assessment.features["waseem_entry_anchor"] = float(native.entry_anchor or 0.0)
                            assessment.features["waseem_pullback_score"] = float(native.pullback_score)
                            assessment.features["waseem_auction_available_count"] = float(len(native.auction.available_fields))
                            assessment.features["waseem_auction_unavailable_count"] = float(len(native.auction.unavailable_fields))
                            # WASEEM decides the horizon from the same data pass.
                            label = "وسيم 30" if trade_horizon == "waseem30" else "وسيم 20"
                            assessment.trade_type = {
                                "intraday": f"{label} — داخل الجلسة",
                                "two_day": f"{label} — 1–2 جلسة",
                                "multi_session": f"{label} — 2–5 جلسات",
                            }.get(native.horizon, label)
                        else:
                            native = evaluate_saudi_opportunity(
                                horizon=trade_horizon, features=assessment.features, quote=quote,
                                market_context=candidate_market_ctx,
                                min_traded_value=self._effective_min_traded_value(),
                                leadership_score=leadership_score, entry_quality_score=entry_quality_score,
                                persistence_score=persistence_score, catalyst_context=catalyst,
                                liquidity_state=judge_result.liquidity_state, limit_state=current_limit_state,
                                judge_blockers=judge_result.blockers,
                            )
                        assessment.reasons = list(native.reasons) + list(assessment.reasons)
                        assessment.features.update({
                            "saudi_market_score": native.market_score,
                            "saudi_money_flow_score": native.money_flow_score,
                            "saudi_structure_score": native.structure_score,
                            "saudi_entry_score": native.entry_score,
                            "saudi_target_feasibility": native.target_feasibility_score,
                            "saudi_risk_score": native.risk_score,
                        })
                        print(
                            f"[saudi-native] {symbol} horizon={getattr(native, 'horizon', trade_horizon)} state={native.state} "
                            f"total={native.total_score:.1f} flow={native.money_flow_score:.1f} "
                            f"leader={native.leadership_score:.1f} structure={native.structure_score:.1f} "
                            f"entry={native.entry_score:.1f} target={native.target_feasibility_score:.1f}"
                        )
                        scan_diag[native.state] = scan_diag.get(native.state, 0) + 1
                        why = (native.blockers or native.reasons or judge_result.blockers or judge_result.reasons or ["لا توجد أفضلية كافية"])[0]
                        why = (
                            f"MF{native.money_flow_score:.0f}/L{native.leadership_score:.0f}/S{native.structure_score:.0f}/"
                            f"E{native.entry_score:.0f}/T{native.target_feasibility_score:.0f} | {why}"
                        )
                        near_candidates.append((
                            float(native.total_score), symbol, float(native.total_score), 70.0,
                            native.state, why, discovery_text,
                        ))
                        if trade_horizon in {"waseem20", "waseem30"} and native.state in {"TRADE_READY", "WAIT", "EARLY_RADAR", "BUILDING", "SETUP", "WAIT_PULLBACK", "INVALIDATED"}:
                            wait_plan = build_wait_plan(assessment.features, quote, native, min_rr=self.s.min_rr)
                            cat_items = list(catalyst.get("items", []) or [])
                            cat_item = (cat_items[-1] if cat_items else {})
                            cat_headline = str(cat_item.get("headline", "") or "")
                            target_alerts = self._waseem30_scan_alerts if trade_horizon == "waseem30" else self._waseem20_scan_alerts
                            target_alerts.append({
                                "symbol": symbol,
                                "name": getattr(quote, "name", "") or symbol,
                                "price": float(getattr(quote, "price", 0) or 0),
                                "change_percent": float(getattr(quote, "change_percent", 0) or 0),
                                "quote_updated_at": (quote.updated_at.isoformat() if getattr(quote, "updated_at", None) else ""),
                                "historical_updated_at": (hist_stamp.isoformat() if hist_stamp else ""),
                                "decision_time": self._utc_now().isoformat(),
                                "state": native.state,
                                "horizon": native.horizon,
                                "horizon_sessions": native.horizon_sessions,
                                "total_score": native.total_score,
                                "early_score": float(getattr(native, "early_score", native.total_score)),
                                "move_stage": str(getattr(native, "move_stage", "UNKNOWN")),
                                "entry_type": str(getattr(native, "entry_type", "NONE")),
                                "data_completeness_score": float(getattr(native, "data_completeness_score", 100.0)),
                                "data_status": dict(getattr(native, "data_status", {}) or {}),
                                "liquidity_map": dict(getattr(native, "liquidity_map", {}) or {}),
                                "early_components": dict(getattr(native, "early_components", {}) or {}),
                                "snapshot": dict(getattr(native, "snapshot", {}) or {}),
                                "market_score": native.market_score,
                                "money_flow_score": native.money_flow_score,
                                "leadership_score": native.leadership_score,
                                "catalyst_score": native.catalyst_score,
                                "structure_score": native.structure_score,
                                "entry_score": native.entry_score,
                                "target_feasibility_score": native.target_feasibility_score,
                                "risk_score": native.risk_score,
                                "persistence_score": persistence_score,
                                "reasons": list(native.reasons),
                                "blockers": list(native.blockers),
                                "plan": wait_plan,
                                "auction": native.auction.to_dict(),
                                "catalyst_headline": cat_headline,
                                "catalyst_source": str(cat_item.get("source", "") or ""),
                                "catalyst_published_at": str(cat_item.get("published_at", "") or ""),
                                "catalyst_available": bool(catalyst.get("available")),
                                "data_source": str(getattr(quote, "raw", {}) or {}).replace("\n", " ")[:0],
                                "discovery": discovery_text,
                            })
                        if native.state == "TRADE_READY":
                            # Native decision is the only public meaning of a ready trade.
                            # Legacy Judge remains diagnostic evidence, not a contradictory final label.
                            judge_result.decision = "APPROVE"
                            if trade_horizon in {"waseem20", "waseem30"}:
                                judge_result.horizon = native.horizon
                                judge_result.leadership_score = native.leadership_score
                                judge_result.entry_quality_score = native.entry_score
                                judge_result.persistence_score = persistence_score
                                judge_result.catalyst_score = float(catalyst.get("score", 0) or 0)
                            signal, build_reason = engine.build_assessment_with_diagnostics(
                                candidate, regime, sector, assessment,
                                quote_updated_at=(quote.updated_at.isoformat() if quote.updated_at else ""),
                                historical_updated_at=(hist_stamp.isoformat() if hist_stamp else ""),
                                judge_decision=judge_result, native_decision=native,
                            )
                            if signal is None:
                                scan_diag["TRADE_READY"] = max(0, scan_diag["TRADE_READY"] - 1)
                                scan_diag["POST_BUILD_DROP"] += 1
                                near_candidates[-1] = (
                                    float(native.total_score), symbol, float(native.total_score), 70.0,
                                    "SETUP", f"POST_BUILD_DROP | {build_reason}", discovery_text,
                                )
                                print(f"[post-build-drop] {symbol}: {build_reason}")
                        else:
                            print(f"[saudi-native-{native.state.lower()}] {symbol}: " + " | ".join(native.blockers or native.reasons))

                except Exception as exc:

                    print(
                        f"[analysis] {symbol} "
                        f"failed: {exc}"
                    )
                    mark_skip("خطأ تحليل داخلي")
                    continue

                if not signal:
                    continue

                # -------------------------------------------------
                # PRIVATE PREVIEW / MANUAL CONFIRMATION
                # -------------------------------------------------

                # Do NOT add the paper trade and do NOT publish yet.
                # Store the exact scan result for a short confirmation window.
                # Confirming later reuses this object and makes zero market API calls.
                self._stage_pending_signal(signal)

                print(
                    f"[signal] staged for admin confirmation "
                    f"{symbol} strategy={signal.strategy}"
                )

                return (
                    "✅ TRADE_READY — تم اكتشاف صفقة ورقية قابلة للتنفيذ وبانتظار تأكيدك.\n"
                    f"{signal.name} ({signal.symbol})\n"
                    f"📍 اكتشاف: {discovery_text}\n"
                    f"🔥 Leadership: {signal.leadership_score:.1f}/100\n"
                    f"🎯 Entry Quality: {signal.entry_quality_score:.1f}/100\n"
                    f"⏱ Persistence: {signal.persistence_score:.1f}/100\n"
                    f"🏹 Hunter: {signal.hunter_score:.1f}/100\n"
                    f"⚖️ Judge: {signal.judge_score:.1f}/100 | المطلوب {signal.required_score:.1f}\n"
                    f"🧭 نوع الصفقة: {signal.trade_type}\n"
                    f"📊 حالة الاحتمالية: {signal.probability_status}\n"
                    f"⏳ صلاحية التأكيد: {int(getattr(self.s, 'signal_confirmation_expiry_minutes', 5))} دقائق.\n"
                    "لم تُسجل أو تُنشر الصفقة بعد.\n\n"
                    + stage1_diag_text
                )

            near_candidates.sort(reverse=True, key=lambda x: x[0])
            closest = ""
            if near_candidates:
                rows=[]
                for _, sym, sc, req, decn, why, discovery in near_candidates[:3]:
                    rows.append(
                        f"• {sym}: {decn} — {sc:.1f}/{req:.1f} — {why}\n"
                        f"  📍 اكتشاف: {discovery}"
                    )
                closest = "\n\n📌 أقرب المرشحين:\n" + "\n".join(rows)
            skipped_detail = ""
            if skip_reasons:
                skipped_detail = "\n⚪ أسباب تجاوز البيانات: " + "، ".join(
                    f"{k} ({v})" for k, v in sorted(skip_reasons.items(), key=lambda kv: kv[1], reverse=True)[:3]
                )
            evaluated_count = sum(scan_diag.get(k, 0) for k in (
                "TRADE_READY", "WAIT", "SETUP", "LEADER", "RADAR", "WAIT_PULLBACK", "NO_CHASE", "INVALIDATED"
            ))
            if evaluated_count == 0 and scan_diag["DATA_SKIP"] > 0:
                outcome_text = (
                    "⚠️ لم يكتمل التحليل العميق لأي مرشح بسبب نقص/تقادم "
                    "البيانات؛ لا يمكن تأكيد وجود أو عدم وجود صفقة حاليًا.\n"
                )
            elif scan_diag["DATA_SKIP"] > 0:
                outcome_text = (
                    "لم توجد صفقة TRADE_READY في هذا الفحص، مع وجود مرشحين تعذر تحليلهم بسبب البيانات.\n"
                )
            else:
                outcome_text = "لم توجد صفقة TRADE_READY الآن؛ تظهر أدناه أفضل الحالات القريبة وأسباب عدم جاهزيتها.\n"

            recovery_note = ""
            recovered_total = detail_recovery.get("monitor", 0) + detail_recovery.get("stage1", 0)
            if recovered_total or detail_recovery.get("unresolved", 0):
                recovery_note = (
                    f"🩹 استعادة تفاصيل السعر: {recovered_total} "
                    f"(مزود احتياطي {detail_recovery.get('monitor', 0)} + "
                    f"Stage-1 حديث {detail_recovery.get('stage1', 0)}) | "
                    f"غير محلول: {detail_recovery.get('unresolved', 0)}\n"
                )

            return (
                "🔎 اكتمل الفحص اليدوي.\n"
                f"🧭 المسار: {('🧠 وسيم 20 — المحرك السعودي الموحّد' if trade_horizon == 'waseem20' else (('⚡ تداول يومي — ' + ('🚀 صائد القادة' if intraday_logic == 'emerging' else '🇸🇦 المحرك السعودي')) if trade_horizon == 'intraday' else ('⏭️ فرص يومين' if trade_horizon == 'two_day' else '📅 متعدد الجلسات')))}\n"
                f"🎯 المطلوب فرزه: {screen_limit}\n"
                f"📥 ما أعاده المزود/المزودان: {len(screening_quotes)}\n"
                f"✅ الأسهم ببيانات حديثة: {len(fresh_screening)}\n"
                f"⚡ أعلى من عتبة الفرز المفضلة: {len(preferred)}\n"
                f"🔬 المرشحون بتفاصيل كاملة: {len(detailed_quotes)}\n"
                + recovery_note
                + f"✅ TRADE_READY: {scan_diag['TRADE_READY']} | 🟡 WAIT: {scan_diag['WAIT']} | 🟡 SETUP: {scan_diag['SETUP']} | 🔥 LEADER: {scan_diag['LEADER']}\n"
                + f"👀 RADAR: {scan_diag['RADAR']} | ⏳ WAIT_PULLBACK: {scan_diag['WAIT_PULLBACK']} | ⛔ NO_CHASE: {scan_diag['NO_CHASE']}\n"
                + f"❌ INVALIDATED: {scan_diag['INVALIDATED']} | 🧱 سقط بعد بناء الصفقة: {scan_diag['POST_BUILD_DROP']}\n"
                + f"⚪ تجاوز بسبب البيانات/السعر: {scan_diag['DATA_SKIP']}\n"
                + outcome_text
                + "\n"
                + stage1_diag_text
                + skipped_detail
                + closest
            )

    # =========================================================
    # PRICE UPDATE
    # =========================================================

    @staticmethod
    def _profit_milestones(pct, sent_levels, step_pct=1.0):
        """Return newly crossed positive profit milestones in ascending order."""
        try:
            pct = float(pct)
            step_pct = max(0.1, float(step_pct))
        except (TypeError, ValueError):
            return []
        if pct < step_pct:
            return []
        sent = set(sent_levels or [])
        max_level = int(pct // step_pct)
        levels = []
        for idx in range(1, max_level + 1):
            level = round(idx * step_pct, 6)
            if level not in sent:
                levels.append(level)
        return levels

    def _price_update_due(
        self,
        trade,
    ):
        """
        هل حان موعد نشر تحديث السعر؟

        أول تحديث لن يخرج قبل مرور المدة
        من وقت اكتشاف الصفقة.
        """

        interval = max(
            5,
            int(
                getattr(
                    self.s,
                    "trade_price_update_minutes",
                    30,
                )
            ),
        )

        anchor = (
            trade.get(
                "last_price_public_update_at"
            )
            or trade.get(
                "discovered_at"
            )
        )

        when = self._parse_datetime(
            anchor
        )

        if when is None:
            return True

        elapsed = (
            self._utc_now()
            - when
        ).total_seconds() / 60

        return (
            elapsed >= interval
        )

    def _price_update_status(
        self,
        trade,
        price,
    ):
        entry = float(
            trade["entry"]
        )

        tp1 = float(
            trade["tp1"]
        )

        tp2 = float(
            trade["tp2"]
        )

        tp3 = float(
            trade["tp3"]
        )

        sl = float(
            trade.get(
                "trailing_stop"
            )
            or trade["sl"]
        )

        if price >= tp3:
            return (
                "🎯 عند/فوق الهدف الثالث"
            )

        if price >= tp2:
            return (
                "🎯 بين الهدف الثاني "
                "والثالث"
            )

        if price >= tp1:
            return (
                "🎯 بين الهدف الأول "
                "والثاني"
            )

        if entry > 0:

            distance_tp1 = (
                abs(
                    tp1 - price
                )
                / entry
                * 100
            )

            distance_sl = (
                abs(
                    price - sl
                )
                / entry
                * 100
            )

            if (
                price < tp1
                and distance_tp1 <= 0.5
            ):
                return (
                    "🟡 قريب من الهدف الأول"
                )

            if (
                price > sl
                and distance_sl
                <= self.s.near_sl_warning_pct
            ):
                return (
                    "🟠 قريب من وقف الخسارة"
                )

        if price >= entry:
            return (
                "🟢 أعلى من سعر الدخول"
            )

        return (
            "🟠 أقل من سعر الدخول"
        )

    def _price_update_text(
        self,
        trade,
        quote,
    ):
        entry = float(
            trade["entry"]
        )

        price = float(
            quote.price
        )

        pct = (
            (
                price - entry
            )
            / entry
            * 100
            if entry
            else 0
        )

        local_time = (
            self._local_now()
            .strftime(
                "%H:%M"
            )
        )

        trade_type = (
            trade.get(
                "trade_type"
            )
            or "—"
        )

        return (
            "📊 تحديث صفقة مفتوحة\n\n"

            f"السهم: "
            f"{trade.get('name', '')}\n"

            f"الرمز: "
            f"{trade.get('symbol', '')}\n"

            f"🧭 نوع الصفقة: "
            f"{trade_type}\n\n"

            f"💰 سعر الدخول: "
            f"{entry:.2f}\n"

            f"📍 السعر الحالي: "
            f"{price:.2f}\n"

            f"📈 التغير من الدخول: "
            f"{pct:+.2f}%\n"

            f"💵 الربح/الخسارة (سهم واحد): "
            f"{price - entry:+.2f} ر.س\n\n"

            f"🎯 الهدف الأول: "
            f"{float(trade['tp1']):.2f}\n"

            f"🎯 الهدف الثاني: "
            f"{float(trade['tp2']):.2f}\n"

            f"🎯 الهدف الثالث: "
            f"{float(trade['tp3']):.2f}\n"

            f"🛑 وقف الخسارة: "
            f"{float(trade.get('trailing_stop') or trade['sl']):.2f}\n\n"

            f"📌 الحالة: "
            f"{self._price_update_status(trade, price)}\n\n"

            f"🕒 آخر تحديث: "
            f"{local_time} بتوقيت الرياض\n"

            "📡 بيانات سهمك متأخرة حسب الباقة"
        )

    def _trade_update_image(self, trade, price, kind="OPEN"):
        symbol = str((trade or {}).get("symbol") or "trade")
        stamp = self._utc_now().strftime("%Y%m%d%H%M%S%f")
        path = Path(getattr(self.s, "state_dir", "data")) / "telegram_updates" / f"{symbol}_{kind.lower()}_{stamp}.png"
        titles = {
            "PROFIT": "PROFIT UPDATE",
            "OPEN": "OPEN TRADE UPDATE",
            "TARGET": "TARGET UPDATE",
        }
        return build_trade_update_card(trade, float(price), str(path), title=titles.get(kind, "TRADE UPDATE"))

    async def _send_dynamic_profit_update(self, trade, price, text, kind="PROFIT"):
        image_path = self._trade_update_image(trade, price, kind=kind)
        try:
            return await self.b.send_profit(
                text,
                trade=trade,
                image_path=image_path,
            )
        finally:
            try:
                Path(image_path).unlink(missing_ok=True)
            except OSError:
                pass

    # =========================================================
    # TRADE MONITOR
    # =========================================================

    async def monitor_once(self):
        """
        Monitor open Paper Trades only.

        NEVER creates a new trade.
        """

        if (
            self.monitor_lock.locked()
            or not self.market_is_monitorable()
        ):
            return

        async with self.monitor_lock:

            self.last_monitor = (
                self._utc_now()
            )

            state = (
                self.store.state()
            )

            if not state[
                "open_trades"
            ]:
                return

            trades = state[
                "open_trades"
            ]

            # Monitor every open trade each cycle. With MAX_OPEN_TRADES=5 this
            # removes the old round-robin gap where a trade could wait ~30 min.
            # ProviderRouter.monitor_quotes prefers Tasilab bulk data, protecting
            # the scarce SAHMK daily quota.
            selected = list(trades)
            symbols = [str(t.get("symbol", "")).strip() for t in selected if t.get("symbol")]
            if hasattr(self.p, "monitor_quotes"):
                quote_map = await self.p.monitor_quotes(symbols)
            else:
                quote_map = await self.p.quotes(symbols)

            for trade in selected:

                symbol = trade[
                    "symbol"
                ]

                try:
                    updated_bar = None
                    current_quote = quote_map.get(symbol)
                    if trade.get("status") == "OPEN" and self._fresh_quote(current_quote):
                        self.trade_manager.mark_observed_session(
                            symbol, getattr(current_quote, "updated_at", None)
                        )

                    # WAITING_ENTRY is not an open trade yet. Never assume a fill
                    # merely because the setup was published.
                    if trade.get("status") == "WAITING_ENTRY":
                        expires=self._parse_datetime(trade.get("entry_expires_at"))
                        if expires is not None and self._utc_now() >= expires:
                            expired=self.trade_manager.expire_waiting(symbol)
                            if expired:
                                await self.b.send_entry(expired_entry_message(expired), expired)
                            continue

                        activated=None
                        # Snapshot activation is exact when price is observed inside zone.
                        if self._fresh_quote(current_quote):
                            activated, ev=self.trade_manager.activate_entry(
                                symbol, current_quote.price,
                                when=(current_quote.updated_at.isoformat() if current_quote.updated_at else None),
                                source="market_quote",
                            )
                            if ev and activated:
                                await self.b.send_entry(entry_message(activated, activated["entry"], activated.get("entry_time")), activated)
                                trade=activated
                        # If delayed snapshot missed the touch, use a completed bar
                        # intersection conservatively. No TP/SL is inferred in the same
                        # activation bar because intrabar order is unknown.
                        if not activated and self._fresh_quote(current_quote) and self.h is not None and hasattr(self.h, "intraday"):
                            try:
                                entry_df,_=await self.h.intraday(symbol)
                                if entry_df is None or entry_df.empty:
                                    raise ValueError("entry bar feed unavailable")
                                entry_stamp=self.h.last_stamp(entry_df) if hasattr(self.h,"last_stamp") else None
                                if entry_stamp is None:
                                    raise ValueError("entry bar timestamp missing")
                                entry_age=(self._utc_now()-entry_stamp.astimezone(timezone.utc)).total_seconds()/60.0
                                if entry_age < -5 or entry_age > float(getattr(self.s,"historical_intraday_max_age_minutes",30)):
                                    raise ValueError(f"entry bar feed stale age={entry_age:.1f}m")
                                if hasattr(self.h,"validate_against_quote") and not self.h.validate_against_quote(
                                    entry_df,current_quote.price,float(getattr(self.s,"intraday_max_price_gap_pct",2.5))
                                ):
                                    raise ValueError("entry bar price gap exceeds allowed threshold")
                                cutoff=self._parse_datetime(trade.get("published_at") or trade.get("discovered_at"))
                                if entry_df is not None and not entry_df.empty:
                                    for _,row in entry_df.iterrows():
                                        bt=row.get("datetime")
                                        if hasattr(bt,"to_pydatetime"): bt=bt.to_pydatetime()
                                        if bt is None: continue
                                        if bt.tzinfo is None: bt=bt.replace(tzinfo=timezone.utc)
                                        if cutoff and bt.astimezone(timezone.utc) <= cutoff: continue
                                        activated,ev=self.trade_manager.activate_entry_bar(symbol,row["high"],row["low"],row["close"],bt.astimezone(timezone.utc).isoformat())
                                        if ev and activated:
                                            if "ENTRY" in ev:
                                                await self.b.send_entry(entry_message(activated, activated["entry"], activated.get("entry_time")), activated)
                                            if "SL" in ev:
                                                await self.b.send_loss_for_trade(activated,float(activated.get("exit") or activated.get("sl")))
                                                self._record_learning_if_closed(activated)
                                            trade=activated
                                            break
                            except Exception as exc:
                                print(f"[entry-watch] {symbol} bar activation unavailable: {exc}")
                        if trade.get("status") != "OPEN":
                            continue

                    # First reconcile completed 15m OHLC bars so a target/stop
                    # touched between delayed snapshots is not silently missed.
                    # Yahoo remains research/reconciliation only; live quote
                    # freshness is still required for ongoing snapshot updates.
                    closed_by_bar = False
                    if self.h is not None and hasattr(self.h, "intraday"):
                        try:
                            bar_df, _ = await self.h.intraday(symbol)
                            if bar_df is not None and not bar_df.empty:
                                hist_stamp = self.h.last_stamp(bar_df) if hasattr(self.h, "last_stamp") else None
                                if hist_stamp is None:
                                    raise ValueError("missing completed-bar timestamp")
                                hist_age = (self._utc_now() - hist_stamp.astimezone(timezone.utc)).total_seconds() / 60.0
                                if hist_age < -5 or hist_age > float(getattr(self.s, "historical_intraday_max_age_minutes", 30)):
                                    raise ValueError(f"completed-bar feed stale age={hist_age:.1f}m")
                                if (
                                    current_quote is not None
                                    and getattr(current_quote, "price", 0) > 0
                                    and hasattr(self.h, "validate_against_quote")
                                    and not self.h.validate_against_quote(
                                        bar_df, current_quote.price, float(getattr(self.s, "intraday_max_price_gap_pct", 2.5))
                                    )
                                ):
                                    raise ValueError("completed-bar price gap exceeds allowed threshold")

                                cutoff = self._parse_datetime(
                                    trade.get("last_bar_checked_at") or trade.get("discovered_at")
                                )
                                for _, row in bar_df.iterrows():
                                    bar_dt = row.get("datetime")
                                    if hasattr(bar_dt, "to_pydatetime"):
                                        bar_dt = bar_dt.to_pydatetime()
                                    if bar_dt is None:
                                        continue
                                    if bar_dt.tzinfo is None:
                                        bar_dt = bar_dt.replace(tzinfo=timezone.utc)
                                    if cutoff is not None and bar_dt.astimezone(timezone.utc) <= cutoff:
                                        continue
                                    updated_bar, bar_events = self.trade_manager.update_bar(
                                        symbol, row["high"], row["low"], row["close"],
                                        bar_dt.astimezone(timezone.utc).isoformat(),
                                    )
                                    if not updated_bar:
                                        break
                                    for event in bar_events:
                                        if event == "CLOSE_TP3":
                                            await self._send_dynamic_profit_update(
                                                updated_bar,
                                                float(updated_bar["tp3"]),
                                                tp_message(updated_bar, "TP3", float(updated_bar["tp3"])),
                                                kind="TARGET",
                                            )
                                        elif event == "SL":
                                            await self.b.send_loss_for_trade(
                                                updated_bar, float(updated_bar.get("exit") or updated_bar.get("sl"))
                                            )
                                        elif event in {"TP1", "TP2"}:
                                            target_price = float(updated_bar[event.lower()])
                                            await self._send_dynamic_profit_update(
                                                updated_bar,
                                                target_price,
                                                tp_message(updated_bar, event, target_price),
                                                kind="TARGET",
                                            )
                                    if updated_bar.get("status") == "OPEN":
                                        self.trade_manager.apply_trailing(updated_bar, float(row["close"]), atr=None)
                                    else:
                                        closed_by_bar = True
                                        break
                        except Exception as exc:
                            print(f"[monitor-bars] {symbol} reconciliation unavailable: {exc}")

                    if closed_by_bar:
                        self._record_learning_if_closed(updated_bar)
                        continue

                    quote = current_quote

                    if not self._fresh_quote(
                        quote
                    ):

                        print(
                            f"[monitor] {symbol}: "
                            "stale/missing timestamp"
                        )

                        continue

                    # -----------------------------------------
                    # UPDATE TRADE
                    # -----------------------------------------

                    updated, events = (
                        self.trade_manager.update(
                            symbol,
                            quote.price,
                        )
                    )

                    if not updated:
                        continue

                    # -----------------------------------------
                    # TP / SL EVENTS
                    # -----------------------------------------

                    for event in events:

                        if event == "CLOSE_TP3":

                            await self._send_dynamic_profit_update(
                                updated,
                                quote.price,
                                tp_message(updated, "TP3", quote.price),
                                kind="TARGET",
                            )

                        elif event == "SL":

                            await self.b.send_loss_for_trade(
                                updated,
                                quote.price,
                            )

                        elif event in {
                            "TP1",
                            "TP2",
                        }:

                            await self._send_dynamic_profit_update(
                                updated,
                                quote.price,
                                tp_message(updated, event, quote.price),
                                kind="TARGET",
                            )

                    # -----------------------------------------
                    # OPEN TRADE ONLY
                    # -----------------------------------------

                    if updated.get("status") != "OPEN":
                        self._record_learning_if_closed(updated)
                        continue

                    # -----------------------------------------
                    # TRAILING
                    # -----------------------------------------

                    self.trade_manager.apply_trailing(
                        updated,
                        quote.price,
                        atr=None,
                    )

                    # -----------------------------------------
                    # PROFIT %
                    # -----------------------------------------

                    entry = float(
                        updated[
                            "entry"
                        ]
                    )

                    pct = (
                        (
                            quote.price
                            - entry
                        )
                        / entry
                        * 100
                    )

                    sent = set(
                        updated.get(
                            "profit_alerts_sent",
                            [],
                        )
                    )

                    # -----------------------------------------
                    # PROFIT ALERTS — V15 EVERY +1% STEP
                    # -----------------------------------------

                    try:
                        step_pct = float(getattr(self.s, "profit_alert_step_pct", 1.0) or 1.0)
                    except (TypeError, ValueError):
                        step_pct = 1.0
                    step_pct = max(0.1, step_pct)

                    # Alert once for every newly crossed positive step.  Example
                    # with step=1: +1%, +2%, +3% ... .  If delayed data jumps
                    # across multiple levels in one monitor cycle, each crossed
                    # level is emitted once so no milestone is silently lost.
                    crossed_levels = self._profit_milestones(
                        pct,
                        sent,
                        step_pct=step_pct,
                    )

                    for threshold in crossed_levels:
                        await self._send_dynamic_profit_update(
                            updated,
                            quote.price,
                            profit_message(
                                updated,
                                quote.price,
                                quote.price - entry,
                                milestone_pct=threshold,
                            ),
                            kind="PROFIT",
                        )
                        sent.add(threshold)

                    # -----------------------------------------
                    # NEAR STOP
                    # -----------------------------------------

                    stop = float(
                        updated.get(
                            "trailing_stop"
                        )
                        or updated[
                            "sl"
                        ]
                    )

                    distance_pct = (
                        abs(
                            quote.price
                            - stop
                        )
                        / entry
                        * 100
                    )

                    if (
                        quote.price > stop
                        and distance_pct
                        <= self.s.near_sl_warning_pct
                        and not updated.get(
                            "near_sl_warning_sent"
                        )
                    ):

                        await self.b.send_near_sl(
                            updated,
                            quote.price,
                        )

                        updated[
                            "near_sl_warning_sent"
                        ] = True

                    # -----------------------------------------
                    # PERIODIC PRICE UPDATE
                    # -----------------------------------------

                    if self._price_update_due(
                        updated
                    ):

                        await self._send_dynamic_profit_update(
                            updated,
                            quote.price,
                            self._price_update_text(updated, quote),
                            kind="OPEN",
                        )

                        updated[
                            "last_price_public_update_at"
                        ] = (
                            self._utc_now()
                            .isoformat()
                        )

                        print(
                            "[monitor] periodic "
                            "price update sent "
                            f"{symbol}"
                        )

                    # -----------------------------------------
                    # SAVE MONITOR STATE
                    # -----------------------------------------

                    current = (
                        self.store.state()
                    )

                    for item in current[
                        "open_trades"
                    ]:

                        if (
                            item[
                                "symbol"
                            ]
                            == symbol
                        ):

                            item[
                                "profit_alerts_sent"
                            ] = sorted(
                                sent
                            )

                            item[
                                "near_sl_warning_sent"
                            ] = updated.get(
                                "near_sl_warning_sent",
                                False,
                            )

                            item[
                                "trailing_stop"
                            ] = updated.get(
                                "trailing_stop"
                            )

                            item[
                                "last_price_public_update_at"
                            ] = updated.get(
                                "last_price_public_update_at"
                            )

                    self.store.save_state(
                        current
                    )

                except Exception as exc:

                    print(
                        f"[monitor] {symbol} "
                        f"failed: {exc}"
                    )

    # =========================================================
    # SCHEDULED TASKS
    # =========================================================

    async def scheduled_tasks(self):

        await self._scheduled_market_data_warmup()

        # V23: discovery remains manual by default. It becomes periodic only
        # after the admin explicitly enables the leader monitor for that day.
        await self._scheduled_leader_monitor()
        await self._scheduled_saudi_scanner()
        await self._scheduled_waseem30()
        await self._scheduled_waseem20()

        await self.monitor_once()

        await (
            self._scheduled_market_close_message()
        )

        await self._scheduled_horizon_time_exits()

        # Daily/weekly reports are private on-demand only; the scheduler is
        # structurally forbidden from publishing them.
        await self.refresh_news_if_due()

    async def _scheduled_saudi_scanner(self):
        status = self.saudi_scanner_status()
        if not status.get("enabled"):
            return
        if status.get("day") != self._local_now().date().isoformat():
            self.disable_saudi_scanner()
            return
        try:
            await self.run_saudi_scanner(force=False)
        except Exception as exc:
            print(f"[saudi-scanner] {exc!r}")

    async def _scheduled_waseem30(self):
        if not self.waseem30_status().get("enabled"):
            return
        try:
            await self.run_waseem30_scanner(force=False)
        except Exception as exc:
            print(f"[waseem30] scheduled scan failed: {exc!r}")

    async def _scheduled_waseem20(self):
        if not self.waseem20_status().get("enabled"):
            return
        try:
            await self.run_waseem20_scanner(force=False)
        except Exception as exc:
            print(f"[waseem20] scheduled scan failed: {exc!r}")

    async def _scheduled_leader_monitor(self):
        status = self.leader_monitor_status()
        local = self._local_now()
        # A one-day switch: never silently carry automatic discovery into the
        # next Saudi trading day.
        if status.get("enabled") and status.get("day") != local.date().isoformat():
            self.disable_leader_monitor()
            return
        try:
            await self.run_leader_monitor(force=False)
        except Exception as exc:
            print(f"[leader-monitor] scheduled scan failed: {exc!r}")

    async def _scheduled_market_data_warmup(self):
        """Prime TASI context once per Saudi session from 10:15 onward.

        This does not create, preview, or publish a signal. It only warms the
        market snapshot so the first 10:30 manual scan does not start cold.
        """
        local = self._local_now()
        if local.weekday() in (4, 5):
            return
        now_min = local.hour * 60 + local.minute
        start = self._minutes(getattr(self.s, "market_data_start", "10:15"))
        end = self._minutes(getattr(self.s, "market_monitor_close", "15:20"))
        if not (start <= now_min < end):
            return
        key = local.date().isoformat()
        if self.last_market_warmup_key == key:
            return
        data = await self._market(force=True)
        if data:
            self.last_market_warmup_key = key
            print(
                f"[market] session warm-up ready at {local.strftime('%H:%M:%S')} "
                f"core={data.get('market_core_source', 'DATA')} "
                f"totals={data.get('market_totals_source', 'DATA')}"
            )

    # =========================================================
    # MARKET CLOSE
    # =========================================================

    async def _scheduled_market_close_message(
        self,
    ):
        local = (
            self._local_now()
        )

        if local.weekday() in (
            4,
            5,
        ):
            return

        key = (
            local.date()
            .isoformat()
        )

        if (
            key
            == self.last_market_close_key
        ):
            return

        current_minute = (
            local.hour * 60
            + local.minute
        )

        if (
            current_minute
            < self._minutes(
                getattr(self.s, "market_monitor_close", "15:20")
            )
        ):
            return

        self.last_market_close_key = key

        await self.b.send_market_close(
            local.strftime(
                "%Y-%m-%d %H:%M %Z"
            )
        )

    async def _scheduled_horizon_time_exits(self):
        """Resolve horizon-specific closes without inventing a closing price.

        Intraday trades are closed only after the delayed feed can reasonably
        contain the closing-auction / trade-at-last price. Multi-session trades
        are allowed to remain open and are time-exited only after the configured
        number of *observed* Saudi sessions. If the quote is stale/pre-close, the
        close is deferred rather than fabricated.
        """
        local = self._local_now()
        if local.weekday() in (4, 5):
            return
        now_min = local.hour * 60 + local.minute
        if now_min < self._minutes(getattr(self.s, "intraday_close_reconcile_after", "15:35")):
            return
        now_utc = self._utc_now()
        if self.last_horizon_exit_attempt_at and (now_utc - self.last_horizon_exit_attempt_at).total_seconds() < 20 * 60:
            return
        self.last_horizon_exit_attempt_at = now_utc

        state = self.store.state()
        open_items = [t for t in state.get("open_trades", []) if t.get("status") == "OPEN"]
        if not open_items:
            return
        max_sessions = max(2, int(getattr(self.s, "multi_session_max_days", 5) or 5))
        candidates = [
            t for t in open_items
            if str(t.get("trade_horizon", "intraday")) == "intraday"
            or (str(t.get("trade_horizon", "intraday")) == "two_day" and int(t.get("sessions_held", 0) or 0) >= 2)
            or (str(t.get("trade_horizon", "intraday")) == "multi_session" and int(t.get("sessions_held", 0) or 0) >= max_sessions)
        ]
        if not candidates:
            return
        symbols = [str(t.get("symbol")) for t in candidates if t.get("symbol")]
        try:
            quote_map = await (self.p.monitor_quotes(symbols) if hasattr(self.p, "monitor_quotes") else self.p.quotes(symbols))
        except Exception as exc:
            print(f"[horizon-exit] quote refresh failed: {exc!r}")
            return

        close_min = self._minutes(getattr(self.s, "close_quote_min_market_time", "15:10"))
        for item in candidates:
            symbol = str(item.get("symbol"))
            quote = quote_map.get(symbol)
            if not self._fresh_quote(quote) or getattr(quote, "updated_at", None) is None:
                print(f"[horizon-exit] {symbol} deferred: close quote unavailable/stale")
                continue
            qlocal = quote.updated_at.astimezone(self.tz) if quote.updated_at.tzinfo else quote.updated_at.replace(tzinfo=timezone.utc).astimezone(self.tz)
            qmin = qlocal.hour * 60 + qlocal.minute
            if qlocal.date() != local.date() or qmin < close_min:
                print(f"[horizon-exit] {symbol} deferred: quote_time={qlocal.isoformat()} is pre-close")
                continue
            sessions = self.trade_manager.mark_observed_session(symbol, quote.updated_at)
            horizon = str(item.get("trade_horizon", "intraday"))
            if horizon == "intraday":
                reason = "INTRADAY_SESSION_END"
            elif horizon == "two_day" and sessions >= 2:
                reason = "TWO_DAY_MAX_HORIZON"
            elif horizon == "multi_session" and sessions >= max_sessions:
                reason = "MULTI_SESSION_MAX_HORIZON"
            else:
                continue
            closed = self.trade_manager.time_exit(
                symbol, quote.price, reason=reason, when=quote.updated_at.isoformat()
            )
            if closed:
                await self.b.send_time_exit(closed)
                self._record_learning_if_closed(closed)
                print(
                    f"[horizon-exit] {symbol} horizon={horizon} sessions={sessions} "
                    f"price={quote.price:.2f} result={closed.get('result_pct', 0):+.2f}%"
                )

    # =========================================================
    # SCHEDULED DAILY REPORT
    # =========================================================

    async def _scheduled_daily_report(self):
        """Disabled by design: reports are private/admin/on-demand only."""
        return None

    async def _scheduled_weekly_report(self):
        """Disabled by design: reports are private/admin/on-demand only."""
        return None

    # =========================================================
    # MARKET TEXT
    # =========================================================

    async def market_text(self):

        data = await self._market()

        if not data:
            return "⚠️ بيانات السوق غير متاحة حاليًا."

        def pick(*keys, default=None):
            for key in keys:
                value = data.get(key) if isinstance(data, dict) else None
                if value not in (None, ""):
                    return value
            raw = data.get("raw", {}) if isinstance(data, dict) else {}
            if isinstance(raw, dict):
                for key in keys:
                    value = raw.get(key)
                    if value not in (None, ""):
                        return value
            return default

        def num(value):
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        index_value = num(pick("index_value", "value", "index", "tasi", "tasi_value", "last", "close"))
        change = num(pick("index_change_percent", "change_percent", "change_pct"))
        adv_value = data.get("advancers") if isinstance(data, dict) else None
        dec_value = data.get("decliners") if isinstance(data, dict) else None
        adv = "غير متاح" if adv_value is None else adv_value
        dec = "غير متاح" if dec_value is None else dec_value
        volume = num(pick("total_volume", "volume", "market_volume"))
        traded_value = num(pick("trading_value", "value_traded", "total_value", "market_value"))

        index_text = f"{index_value:,.2f}" if index_value is not None else "غير متاح"
        change_text = f"{change:+.2f}%" if change is not None else "غير متاح"
        volume_text = f"{volume:,.0f} سهم" if volume is not None and volume > 0 else "غير متاح"
        value_text = f"{traded_value:,.0f} ر.س" if traded_value is not None and traded_value > 0 else "غير متاح"

        active_source = (
            self.p.active_provider_detail()
            if hasattr(self.p, 'active_provider_detail')
            else (self.p.active_provider().upper() if hasattr(self.p, 'active_provider') else 'DATA')
        )
        core_source = str(data.get("market_core_source") or active_source)
        totals_source = str(data.get("market_totals_source") or active_source)
        breadth_source = str(data.get("breadth_source") or data.get("market_breadth_source") or "غير متاح")
        breadth_cov = data.get("breadth_coverage")
        try:
            breadth_cov_text = f" ({float(breadth_cov) * 100:.1f}%)" if breadth_cov is not None else ""
        except (TypeError, ValueError):
            breadth_cov_text = ""
        return (
            "📊 حالة السوق السعودي\n\n"
            f"TASI: {index_text}\n"
            f"التغير: {change_text}\n"
            f"Market Regime: {classify_tasi(data)}\n\n"
            f"🟢 الأسهم الصاعدة: {adv}\n"
            f"🔴 الأسهم الهابطة: {dec}\n"
            f"📦 حجم التداول: {volume_text}\n"
            f"💰 قيمة التداول: {value_text}\n\n"
            f"📡 المصدر النشط: {active_source}\n"
            f"📈 مصدر TASI/التغير: {core_source}\n"
            f"📊 مصدر الحجم/القيمة: {totals_source}\n"
            f"🧭 مصدر الصاعدة/الهابطة: {breadth_source}{breadth_cov_text}\n"
            "⚠️ إنشاء الإشارات يدوي من قائمة البحث فقط."
        )

    @staticmethod
    def _request_reason(item):
        caller = str(item.get("caller", "unknown"))
        mapping = {
            "market_summary": "حالة السوق",
            "quote": "سعر/متابعة سهم",
            "quotes": "فحص مجموعة أسهم",
            "top_volume": "ترتيب الأسهم النشطة",
            "companies": "تحديث Universe",
            "sectors": "قوة القطاعات",
            "diagnose": "اختبار المزود",
            "_get": "طلب داخلي للمزود",
        }
        return mapping.get(caller, caller.replace("_", " "))

    def api_usage_text(self, provider_name="all"):
        stats = self.p.stats() if hasattr(self.p, "stats") else {}
        safe = int(getattr(self.s, "sahmk_daily_switch_limit", 95))
        hard = int(getattr(self.s, "sahmk_local_daily_limit", 100))
        used = int(stats.get("sahmk_daily_requests", stats.get("daily_requests", 0)) or 0)
        remaining_safe = max(0, safe - used)
        tasi = int(stats.get("tasilab_requests", 0) or 0)
        tasi_success = int(stats.get("tasilab_successful_requests", 0) or 0)
        tasi_minute = int(stats.get("tasilab_requests_last_minute", 0) or 0)
        active_raw = str(stats.get("active_provider", "—")).lower()
        active = str(stats.get("active_provider_detail", active_raw.upper()))
        provider_order = str(stats.get("provider_order", "SAHMK → Tasilab"))
        fallback = "المسار التالي حسب السياسة: " + provider_order

        sahmk_blocked = bool(stats.get("sahmk_blocked_for_day", False))
        sahmk_block_reason = str(stats.get("sahmk_block_reason") or "")
        sahmk_block_type = str(stats.get("sahmk_block_type") or "")
        if sahmk_blocked:
            if sahmk_block_type == "IP_DAILY":
                sahmk_status = "🔴 محظور على IP حتى بداية اليوم السعودي الجديد"
            else:
                sahmk_status = "🔴 متوقف لبقية اليوم حسب حد SAHMK"
        elif used < 76:
            sahmk_status = "🟢 طبيعي"
        elif used < 89:
            sahmk_status = "🟡 تخفيف البحث الثقيل"
        elif used < safe:
            sahmk_status = "🟠 قرب الحد الآمن"
        else:
            sahmk_status = "🔴 تم بلوغ الحد الآمن"

        tasilab_status = "🟢 متاح"
        if stats.get("tasilab_circuit_open"):
            tasilab_status = "🔴 Circuit مفتوح مؤقتًا"
        elif int(stats.get("tasilab_rate_limits", 0) or 0) > 0:
            tasilab_status = "🟡 سبق تسجيل Rate Limit"
        elif int(stats.get("tasilab_errors", 0) or 0) > 0:
            tasilab_status = "🟡 يعمل مع أخطاء مسجلة"

        switch_at = stats.get("last_switch_at")
        switch_reason = stats.get("last_switch_reason")
        switch_text = (
            f"{switch_at} — {switch_reason}"
            if switch_at and switch_reason
            else "لم يحدث تحويل يومي بعد"
        )
        keys_text = f"SAHMK: {used}/{safe}"

        if provider_name == "sahmk":
            return (
                "📡 استهلاك SAHMK اليوم\n\n"
                f"{keys_text}\n"
                f"الحد: {hard} فعلي / {safe} آمن\n"
                f"الحالة: {sahmk_status}\n"
                f"المصدر النشط: {active}\n"
                f"ترتيب المزودات: {provider_order}"
            )

        if provider_name == "tasilab":
            return (
                "📡 استهلاك Tasilab اليوم\n\n"
                f"طلبات اليوم: {tasi} محاولة | {tasi_success} ناجحة\n"
                f"آخر 60 ثانية: {tasi_minute} طلب\n"
                "الحد المنشور: 120 طلب/دقيقة خلال فترة التأسيس\n"
                f"Rate limits: {stats.get('tasilab_rate_limits', 0)}\n"
                f"Errors: {stats.get('tasilab_errors', 0)}\n"
                f"الحالة: {tasilab_status}\n"
                f"المصدر النشط: {active}"
            )

        return (
            "📡 استهلاك مزودي البيانات\n\n"
            "SAHMK\n"
            f"{keys_text}\n"
            f"الحد: {safe} آمن / {hard} فعلي\n"
            f"الحالة: {sahmk_status}\n\n"
            "Tasilab\n"
            f"{tasi} محاولة اليوم | {tasi_success} ناجحة\n"
            f"آخر 60 ثانية: {tasi_minute}/120 محليًا\n"
            f"الحالة: {tasilab_status}\n\n"
            "المصدر النشط الآن:\n"
            f"{active}\n\n"
            "ترتيب المزودات:\n"
            f"{provider_order}\n\n"
            "آخر تحويل:\n"
            f"{switch_text}"
            + (f"\n\nحالة SAHMK الفعلية:\n{sahmk_block_reason}" if sahmk_blocked and sahmk_block_reason else "")
        )

    def api_request_log_text(self):
        stats = self.p.stats() if hasattr(self.p, "stats") else {}
        combined = []
        for provider, key in (("SAHMK", "sahmk_recent_requests"), ("Tasilab", "tasilab_recent_requests")):
            for item in stats.get(key, []) or []:
                combined.append({
                    "provider": provider,
                    "time": str(item.get("time", "—")),
                    "path": str(item.get("path", "—")),
                    "status": item.get("status", "—"),
                    "reason": self._request_reason(item),
                })

        combined.sort(key=lambda x: x["time"])
        recent = combined[-10:]
        if not recent:
            return "🧾 سجل الطلبات\n\nلا توجد طلبات مسجلة منذ تشغيل الخدمة."

        lines = ["🧾 آخر 10 طلبات", ""]
        for item in recent:
            lines.append(
                f"• {item['time']} | {item['provider']} | HTTP {item['status']}\n"
                f"  {item['path']}\n"
                f"  السبب: {item['reason']}"
            )
        lines.append("\n🔐 لا يتم عرض مفاتيح API أو Tokens.")
        return "\n".join(lines)

    # =========================================================
    # OPEN TRADES
    # =========================================================

    def open_trades_text(self, horizon=None):

        trades = list(self.store.state()[
            "open_trades"
        ])
        if horizon in {"intraday", "two_day", "multi_session"}:
            trades = [t for t in trades if str(t.get("trade_horizon", "intraday")) == horizon]

        if not trades:
            label = " اليومية" if horizon == "intraday" else (" يومين" if horizon == "two_day" else " متعددة الجلسات" if horizon == "multi_session" else "")
            return f"📭 لا توجد صفقات{label} مفتوحة حاليًا."

        title = (
            "📂 الصفقات المفتوحة — ⚡ يومي" if horizon == "intraday" else
            "📂 الصفقات المفتوحة — ⏭️ 1–2 جلسة" if horizon == "two_day" else
            "📂 الصفقات المفتوحة — 📅 متعدد الجلسات" if horizon == "multi_session" else
            "📂 الصفقات المفتوحة"
        )
        lines = [title, ""]

        for trade in trades:

            status=trade.get("status","—")
            entry_label = (
                f"تنفيذ فعلي: {float(trade['entry']):.2f}"
                if status == "OPEN" else
                f"بانتظار الدخول: {float(trade['entry_low']):.2f}–{float(trade['entry_high']):.2f}"
            )
            lines.append(
                f"{trade['name']} ({trade['symbol']})\n"
                f"🧭 النوع: {trade.get('trade_type','—')}\n"
                f"⏱ الأفق: {'يومي' if str(trade.get('trade_horizon','intraday')) == 'intraday' else '1–2 جلسة' if str(trade.get('trade_horizon')) == 'two_day' else 'متعدد الجلسات'}\n"
                f"📌 الحالة: {status}\n"
                f"{entry_label} | الحالي: {float(trade.get('current_price',trade['entry'])):.2f}\n"
                f"SL: {float(trade['sl']):.2f} | TP1: {float(trade['tp1']):.2f} | "
                f"TP2: {float(trade['tp2']):.2f} | TP3: {float(trade['tp3']):.2f}"
            )

        return "\n\n".join(
            lines
        )

    # =========================================================
    # WEEKLY REPORT
    # =========================================================

    def _report_history(self, period):
        history = list(self.store.history())
        now_local = self._local_now()
        if period == "daily":
            target_date = now_local.date()
            selected = []
            for item in history:
                stamp = item.get("exit_time") or item.get("discovered_at")
                when = self._parse_datetime(stamp)
                if when and when.astimezone(self.tz).date() == target_date:
                    selected.append(item)
            return selected

        cutoff = self._utc_now() - timedelta(days=7)
        selected = []
        for item in history:
            stamp = item.get("exit_time") or item.get("discovered_at")
            when = self._parse_datetime(stamp)
            if when and when >= cutoff:
                selected.append(item)
        return selected

    def _report_metrics(self, period, history):
        local = self._local_now()
        wins = [x for x in history if x.get("result") == "WIN"]
        losses = [x for x in history if x.get("result") == "LOSS"]
        open_trades = list(self.store.state().get("open_trades", []))
        if period == "daily":
            period_open = [
                t for t in open_trades
                if (self._parse_datetime(t.get("discovered_at")) and
                    self._parse_datetime(t.get("discovered_at")).astimezone(self.tz).date() == local.date())
            ]
            title = "اليومي"
            period_label = local.strftime("%d-%m-%Y")
            profit_label = "اليوم"
        else:
            cutoff = self._utc_now() - timedelta(days=7)
            period_open = [
                t for t in open_trades
                if (self._parse_datetime(t.get("discovered_at")) and
                    self._parse_datetime(t.get("discovered_at")) >= cutoff)
            ]
            title = "الأسبوعي"
            start_date = (local.date() - timedelta(days=6)).strftime("%d-%m")
            period_label = f"{start_date} – {local.strftime('%d-%m-%Y')}"
            profit_label = "الأسبوع"
        waiting_entry = sum(1 for t in period_open if t.get("status") == "WAITING_ENTRY")
        active_open = sum(1 for t in period_open if t.get("status") == "OPEN")
        missed = sum(1 for x in history if x.get("result") == "MISSED_ENTRY")
        settled = len(wins) + len(losses)
        win_rate = (len(wins) / settled * 100.0) if settled else 0.0
        settled_history = wins + losses
        gross_win = sum(max(0.0, float(x.get("result_pct") or 0.0)) for x in settled_history)
        gross_loss = abs(sum(min(0.0, float(x.get("result_pct") or 0.0)) for x in settled_history))
        net = sum(float(x.get("result_pct") or 0.0) for x in settled_history)

        def _one_share_pnl(item):
            try:
                entry = float(item.get("entry") or item.get("entry_price") or 0.0)
                exit_price = float(item.get("exit") or item.get("exit_price") or item.get("last_price") or 0.0)
            except (TypeError, ValueError):
                return 0.0
            return (exit_price - entry) if entry > 0 and exit_price > 0 else 0.0

        gross_win_sar = sum(max(0.0, _one_share_pnl(x)) for x in wins)
        gross_loss_sar = abs(sum(min(0.0, _one_share_pnl(x)) for x in losses))
        net_sar = gross_win_sar - gross_loss_sar

        row_source = list(history) + list(period_open)
        rows = []
        seen = set()
        for item in reversed(row_source):
            symbol = str(item.get("symbol") or item.get("ticker") or "—")
            ident = str(item.get("id") or item.get("trade_id") or "") + ":" + symbol + ":" + str(item.get("discovered_at") or "")
            if ident in seen:
                continue
            seen.add(ident)
            try:
                entry = float(item.get("entry") or item.get("entry_price") or 0.0)
            except (TypeError, ValueError):
                entry = 0.0
            try:
                high = float(item.get("highest_price") or item.get("max_price") or item.get("last_price") or item.get("exit") or 0.0)
            except (TypeError, ValueError):
                high = 0.0
            try:
                best = float(item.get("best_profit_pct") or item.get("max_profit_pct") or item.get("result_pct") or 0.0)
            except (TypeError, ValueError):
                best = 0.0
            rows.append({
                "symbol": symbol,
                "type": str(item.get("trade_horizon") or item.get("type") or "—"),
                "entry": entry,
                "high": high,
                "best_pct": best,
                "status": str(item.get("result") or item.get("status") or "OPEN"),
            })
            if len(rows) >= 4:
                break
        rows.reverse()
        return {
            "period": period, "title": title, "period_label": period_label, "profit_label": profit_label,
            "wins": len(wins), "losses": len(losses), "waiting_entry": waiting_entry,
            "active_open": active_open, "pending": waiting_entry + active_open, "missed": missed,
            "win_rate": win_rate, "gross_win": gross_win, "gross_loss": gross_loss, "net": net,
            "gross_win_sar": gross_win_sar, "gross_loss_sar": gross_loss_sar, "net_sar": net_sar,
            "total_trades": settled + waiting_entry + active_open, "settled": settled, "rows": rows,
        }

    def _report_text(self, period, history):
        m = self._report_metrics(period, history)
        return (
            "✨ نتائج ALLUQMANU_TASI ✨\n"
            f"📊 تقرير تداول TASI {m['title']}\n"
            f"▫️ {m['period_label']} ▫️\n\n"
            f"✅ أرباح {m['profit_label']}: +{m['gross_win']:.2f}%\n"
            f"❌ خسائر {m['profit_label']}: -{m['gross_loss']:.2f}%\n"
            f"📈 صافي الأداء الفني: {m['net']:+.2f}%\n\n"
            "🎯 معيار نجاح الإشارة: بلوغ أهداف السعر المحددة للصفقة\n"
            f"✅ إشارات وصلت للهدف: {m['wins']}\n"
            f"🟢 الصفقات الناجحة: {m['wins']}\n"
            f"🔴 الصفقات الخاسرة: {m['losses']}\n"
            f"🟡 بانتظار الدخول: {m['waiting_entry']}\n"
            f"🟢 صفقات مفتوحة بعد التنفيذ: {m['active_open']}\n"
            f"⌛ فرص فاتت دون دخول: {m['missed']}\n"
            f"⏳ قيد المتابعة الإجمالي: {m['pending']}\n"
            f"📊 نسبة النجاح (المغلقة فقط): {m['win_rate']:.1f}%\n\n"
            "📌 الأسهم: النجاح يُحتسب حسب الأهداف وإدارة الصفقة، والخسارة حسب إغلاق الصفقة ووقفها الفعلي.\n"
            "⚠️ Paper Trading فقط."
        )

    async def daily_report(self, send=True, private=False):
        history = self._report_history("daily")
        text = self._report_text("daily", history)
        metrics = self._report_metrics("daily", history)
        image_path = build_report_card(metrics, str(Path(getattr(self.s, "state_dir", "data")) / "telegram" / "daily_report_live.png"))
        if private:
            await self.b.send_admin_report(text=text, image_path=image_path)
            return "✅ تم إرسال التقرير اليومي التجريبي في الخاص."
        if send and not private:
            # Hard safety: never publish reports to public destinations.
            return "🔒 التقرير اليومي خاص فقط. اطلبه من لوحة المشرف في الخاص."
        return text

    async def weekly_report(self, send=True, private=False):
        history = self._report_history("weekly")
        text = self._report_text("weekly", history)
        metrics = self._report_metrics("weekly", history)
        image_path = build_report_card(metrics, str(Path(getattr(self.s, "state_dir", "data")) / "telegram" / "weekly_report_live.png"))
        if private:
            await self.b.send_admin_report(text=text, image_path=image_path)
            return "✅ تم إرسال التقرير الأسبوعي التجريبي في الخاص."
        if send and not private:
            return "🔒 التقرير الأسبوعي خاص فقط. اطلبه من لوحة المشرف في الخاص."
        return text

    # =========================================================
    # PERFORMANCE
    # =========================================================

    def performance_text(self, horizon=None):

        history = list(self.store.history())
        if horizon in {"intraday", "two_day", "multi_session"}:
            history = [x for x in history if str(x.get("trade_horizon", "intraday")) == horizon]

        wins = [
            x
            for x in history
            if x.get(
                "result"
            )
            == "WIN"
        ]

        losses = [
            x
            for x in history
            if x.get(
                "result"
            )
            == "LOSS"
        ]

        closed = (
            len(wins)
            + len(losses)
        )

        win_rate = (
            len(wins)
            / closed
            * 100
            if closed
            else 0
        )

        settled_history = wins + losses
        avg = (
            sum(float(x.get("result_pct") or 0) for x in settled_history)
            / len(settled_history)
            if settled_history
            else 0
        )

        gross_win = sum(
            max(
                0,
                float(
                    x.get(
                        "result_pct"
                    )
                    or 0
                ),
            )
            for x in settled_history
        )

        gross_loss = abs(
            sum(
                min(
                    0,
                    float(
                        x.get(
                            "result_pct"
                        )
                        or 0
                    ),
                )
                for x in history
            )
        )

        pf = (
            gross_win
            / gross_loss
            if gross_loss
            else 0
        )

        return (
            "📈 أداء Paper Trading\n"
            + ("⚡ التداول اليومي\n\n" if horizon == "intraday" else "⏭️ فرص 1–2 جلسة\n\n" if horizon == "two_day" else "📅 متعدد الجلسات\n\n" if horizon == "multi_session" else "كل المسارات\n\n")
            + f"الصفقات المغلقة: "
            f"{closed}\n"

            f"الرابحة: "
            f"{len(wins)}\n"

            f"الخاسرة: "
            f"{len(losses)}\n"

            f"Win Rate: "
            f"{win_rate:.1f}%\n"

            f"متوسط العائد: "
            f"{avg:+.2f}%\n"

            f"Profit Factor: "
            f"{pf:.2f}\n"

            f"الصفقات المفتوحة: "
            f"{len([t for t in self.store.state()['open_trades'] if horizon not in {'intraday', 'two_day', 'multi_session'} or str(t.get('trade_horizon', 'intraday')) == horizon])}"
        )

    # =========================================================
    # STATUS
    # =========================================================

    def status_text(self):

        state = (
            self.store.state()
        )
        market = self.last_market_summary if isinstance(self.last_market_summary, dict) else {}
        breadth_ok = (market.get("advancers") is not None and market.get("decliners") is not None)
        breadth_status = (
            f"{market.get('advancers')}/{market.get('decliners')} ({market.get('breadth_source', 'DATA')})"
            if breadth_ok else "غير متاح — سيُستعاد عند أول snapshot صالح"
        )

        return (
            "🤖 حالة النظام\n\n"

            "New Signals: "
            "MANUAL (/signal)\n"

            "Scheduler: "
            "MONITOR ONLY\n"

            f"Market: "
            f"{'OPEN' if self.market_is_open() else 'CLOSED'}\n"

            f"SAHMK Plan: "
            f"{self.s.sahmk_plan.upper()}\n"

            f"Data Providers: "
            f"{self.p.provider_order_text() if hasattr(self.p, 'provider_order_text') else 'SAHMK → Tasilab'}\n"

            f"Active Data Source: "
            f"{self.p.active_provider_detail() if hasattr(self.p, 'active_provider_detail') else self.p.active_provider().upper()}\n"

            f"Paper Mode: "
            f"{'ON' if self.s.paper_mode else 'OFF'}\n"

            f"Paused: "
            f"{'YES' if state.get('paused') else 'NO'}\n"

            f"Universe: "
            f"{len(self.universe)}\n"

            f"Open Trades: "
            f"{len(state['open_trades'])}\n"

            f"Market Breadth: {breadth_status}\n"

            f"Price Update: كل "
            f"{getattr(self.s, 'trade_price_update_minutes', 30)} دقيقة\n"

            f"Last Manual Scan: "
            f"{state['meta'].get('last_scan', '—')}\n"

            f"Last Trade Monitor: "
            f"{self.last_monitor.isoformat() if self.last_monitor else '—'}"
        )

    # =========================================================
    # HEALTH
    # =========================================================

    async def health_text(self):

        state = (
            self.store.state()
        )

        telegram_ok = False

        try:

            await self.b.signal.get_me()

            telegram_ok = True

        except Exception as exc:

            print(
                "[health] telegram failed: "
                f"{exc}"
            )

        stats = (
            self.p.stats()
            if hasattr(
                self.p,
                "stats",
            )
            else {}
        )

        keys_text = (
            f"SAHMK: {stats.get('daily_requests', '—')}/{stats.get('daily_limit', '—')}"
        )

        return (
            "🟢 SYSTEM HEALTH\n\n"

            f"Telegram: "
            f"{'OK' if telegram_ok else 'ERROR'}\n"

            f"{keys_text}\n"

            f"Active SAHMK server remaining: "
            f"{stats.get('remaining', '—')}\n"

            f"SAHMK 429: "
            f"{stats.get('rate_limits', '—')} | "
            f"State: {stats.get('sahmk_runtime_state', 'AVAILABLE')}\n"

            f"Errors: "
            f"{stats.get('errors', '—')}\n"

            f"Active Provider: "
            f"{stats.get('active_provider_detail', str(stats.get('active_provider', 'sahmk')).upper())}\n"

            f"Provider Order: "
            f"{stats.get('provider_order', 'SAHMK → Tasilab')}\n"

            f"Tasilab Requests: "
            f"{stats.get('tasilab_requests', 0)} attempts / "
            f"{stats.get('tasilab_successful_requests', 0)} success | "
            f"last 60s: {stats.get('tasilab_requests_last_minute', 0)}/120\n"
            f"Tasilab Market Status: "
            f"last_ok={stats.get('tasilab_market_status_last_success', '—')} | "
            f"last_error={stats.get('tasilab_market_status_last_error', '—') or '—'}\n"
            f"Tasilab Bulk Cooldown: "
            f"{stats.get('tasilab_bulk_cooldown_remaining', 0)}s | "
            f"Quote Circuit: "
            f"{'OPEN' if stats.get('tasilab_circuit_open', False) else 'CLOSED'}\n"

            f"Universe Source: "
            f"{stats.get('universe_source', '—')} "
            f"({stats.get('universe_cache_size', 0)})\n"

            "Scheduler: "
            "RUNNING WHEN SERVICE IS AWAKE\n"

            f"Paper Mode: "
            f"{'ON' if self.s.paper_mode else 'OFF'}\n"

            f"Universe: "
            f"{len(self.universe)}\n"

            f"Open Trades: "
            f"{len(state['open_trades'])}\n"

            f"Last Manual Scan: "
            f"{state['meta'].get('last_scan', '—')}\n"

            f"Last Universe Update: "
            f"{state['meta'].get('last_universe_refresh', '—')}"
        )

    # =========================================================
    # SETTINGS
    # =========================================================

    def settings_text(self):

        return (
            "⚙️ الإعدادات الآمنة\n\n"

            f"SAHMK Plan: "
            f"{self.s.sahmk_plan.upper()}\n"

            f"Data Providers: {self.p.provider_order_text() if hasattr(self.p, 'provider_order_text') else 'SAHMK → Tasilab'}\n"
            f"Market Data Warm-up: {getattr(self.s, 'market_data_start', '10:15')}\n"
            f"Signal Window: {getattr(self.s, 'signal_window_start', '10:30')}–{getattr(self.s, 'signal_window_end', '14:50')}\n"
            f"Breadth Recovery: {'TASILAB + FULL-MARKET CACHE' if getattr(self.s, 'market_breadth_tasilab_enabled', True) else 'CACHE ONLY'}\n"

            f"Active-stock screen: "
            f"{self.s.manual_quotes_per_signal}\n"

            f"Detailed finalists: "
            f"{self.s.detail_quotes_per_signal}\n"

            f"Min Score: "
            f"{self.s.min_score}\n"

            f"Min Validated Probability: "
            f"{self.s.min_probability}%\n"

            f"Max Daily Signals: "
            f"{self.s.max_daily_signals}\n"

            f"Max Open Trades: "
            f"{self.s.max_open_trades}\n"

            f"Monitor Quotes/Cycle: "
            f"{self.s.trade_monitor_quotes_per_cycle}\n"

            f"Monitor Interval: "
            f"{self.s.scan_interval_seconds}s\n"

            f"Public Price Update: "
            f"{getattr(self.s, 'trade_price_update_minutes', 30)} min\n"

            f"Data Max Delay: "
            f"{self.s.data_max_delay_minutes} min\n"

            f"Min R/R: "
            f"{self.s.min_rr}\n"

            "Position Sizing: OFF — المشروع تحليل فني ولا يعتمد على رأس مال شخصي\n"

            f"Paper Mode: "
            f"{'ON' if self.s.paper_mode else 'OFF'}\n"

            "Secrets: HIDDEN"
        )

    # =========================================================
    # RISK
    # =========================================================

    def risk_text(self):

        return (
            "🛡️ إدارة المخاطر الفنية\n\n"
            "لا يعتمد المشروع على رأس مال أو حجم مركز شخصي.\n"
            "المخاطرة تُقاس فنيًا من مسافة الدخول إلى وقف الخسارة وجودة R/R.\n\n"
            f"الحد الأدنى R/R: "
            f"{self.s.min_rr}\n"

            "الحد الأقصى للصفقات "
            "المفتوحة: "
            f"{self.s.max_open_trades}\n"

            f"Trailing Stop: "
            f"{'ON' if self.s.trailing_stop_enabled else 'OFF'}\n"

            "الوضع: Paper Trading فقط"
        )
