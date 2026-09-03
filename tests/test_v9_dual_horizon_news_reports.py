from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.news.engine import NewsCatalystEngine
from app.strategy.leadership import LeadershipTracker, entry_quality, leadership_score, limit_state
from app.strategy.two_stage import HunterDecision, judge
from app.trades.manager import TradeManager


class MemoryStore:
    def __init__(self):
        self._state = {"open_trades": [], "meta": {}}
        self._history = []

    def state(self):
        return self._state

    def save_state(self, state):
        self._state = state

    def history(self):
        return self._history

    def save_history(self, history):
        self._history = history


class Settings(SimpleNamespace):
    def __init__(self, **kwargs):
        defaults = dict(
            max_open_trades=5,
            fee_bps=15.5,
            slippage_bps=5,
            min_rr=1.8,
            timezone="Asia/Riyadh",
            signal_window_end="14:50",
            entry_wait_expiry_minutes=180,
            news_enabled=True,
            news_saudi_exchange_url="https://www.saudiexchange.sa/example",
            news_timeout_seconds=1,
            news_bootstrap_lookback_hours=96,
            news_max_items=200,
            news_cache_file="/tmp/test_tasi_news_v9.json",
        )
        defaults.update(kwargs)
        super().__init__(**defaults)


def test_limit_state_respects_new_listing_30pct_band():
    assert limit_state(9.90, daily_limit_pct=10.0) == "LIMIT_UP"
    assert limit_state(9.90, daily_limit_pct=30.0) == "NORMAL"
    assert limit_state(29.90, daily_limit_pct=30.0) == "LIMIT_UP"


def test_leadership_is_separate_from_executable_entry_at_limit_up():
    leadership, _ = leadership_score(
        stock_change_pct=9.9,
        market_change_pct=-0.4,
        traded_value=30_000_000,
        min_traded_value=2_000_000,
        persistence_score=90,
    )
    quality, reasons = entry_quality(
        {"close": 20, "active_vwap": 18, "structure_state": "HH_HL", "is_breakout": 1, "close_position": .9},
        change_pct=9.9,
        daily_limit_pct=10,
    )
    assert leadership >= 75
    assert quality <= 20
    assert any("الحد الأعلى" in x for x in reasons)


def test_leadership_tracker_rewards_persistence_and_detects_decay():
    store = MemoryStore()
    tracker = LeadershipTracker(store)
    base = datetime(2026, 8, 30, 7, 30, tzinfo=timezone.utc)
    q = SimpleNamespace(symbol="1302", change_percent=2.0)
    tracker.update([q], -0.4, now=base)
    q.change_percent = 3.0
    tracker.update([q], -0.5, now=base.replace(minute=45))
    q.change_percent = 4.0
    tracker.update([q], -0.6, now=base.replace(hour=8, minute=0))
    strong, decay, _ = tracker.persistence("1302")
    assert strong > 60
    assert decay == pytest.approx(0.0)

    q.change_percent = 0.5
    tracker.update([q], -0.3, now=base.replace(hour=8, minute=15))
    weakened, decay, reasons = tracker.persistence("1302")
    assert decay >= 2.0
    assert weakened < strong
    assert any("Momentum Decay" in x for x in reasons)


def test_news_engine_resolves_company_name_and_keeps_results_context_only(tmp_path):
    settings = Settings(news_cache_file=str(tmp_path / "news.json"))
    engine = NewsCatalystEngine(settings)
    engine.bind_universe([{"symbol": "1302", "name": "بوان", "name_en": "Bawan"}])
    html = '<a href="/announcement/123">تعلن شركة بوان عن توقيع عقد جديد</a>'
    items = engine._parse_html(html, datetime.now(timezone.utc))
    assert len(items) == 1
    assert items[0].symbol == "1302"
    assert items[0].impact == "HIGH"
    assert 0 < items[0].score <= 2.5

    category, impact, score, direction, _ = engine.classify("تعلن الشركة عن النتائج المالية السنوية")
    assert category == "FINANCIAL_RESULTS"
    assert impact == "HIGH"
    assert score == 0.0
    assert direction == "CONTEXT"


def test_judge_blocks_limit_up_even_when_leadership_is_high():
    hunter = HunterDecision(
        "BUY_CANDIDATE", 86.0, [], [],
        {
            "time_adjusted_rvol": 1.8,
            "is_breakout": 1.0,
            "close_position": .9,
            "failed_breakout": 0.0,
            "structure_state": "HH_HL",
            "resistance_distance_atr": 1.0,
            "vwap_distance_atr": .5,
            "ema20_distance_atr": .8,
            "adx14": 25,
            "di_spread": 5,
        },
        "A", 98.0, 18.0, 90.0, "intraday"
    )
    market = SimpleNamespace(
        state="NORMAL", required_score=70.0, volatility_state="NORMAL",
        data_quality="GOOD", reasons=[]
    )
    result = judge(
        hunter, market, traded_value=20_000_000, min_traded_value=2_000_000,
        stock_change_pct=9.9, market_change_pct=-0.2,
        leadership_score=98, entry_quality_score=18, persistence_score=90,
        limit_state="LIMIT_UP", horizon="intraday",
    )
    assert result.decision == "REJECT"
    assert any("NO_EXECUTABLE_ENTRY" in x for x in result.blockers)


def test_multi_session_judge_is_a_distinct_horizon():
    hunter = HunterDecision(
        "BUY_CANDIDATE", 80, [], [],
        {"time_adjusted_rvol": 1.2, "is_breakout": 1, "close_position": .8,
         "failed_breakout": 0, "structure_state": "HH_HL", "resistance_distance_atr": 1,
         "vwap_distance_atr": .3, "ema20_distance_atr": .5, "adx14": 23, "di_spread": 4},
        "A", 80, 78, 75, "multi_session"
    )
    market = SimpleNamespace(state="NORMAL", required_score=70.0, volatility_state="NORMAL", data_quality="GOOD", reasons=[])
    result = judge(
        hunter, market, traded_value=10_000_000, min_traded_value=2_000_000,
        stock_change_pct=2.0, market_change_pct=0.0,
        leadership_score=80, entry_quality_score=78, persistence_score=75,
        horizon="multi_session",
    )
    assert result.horizon == "multi_session"
    assert result.required_score >= 72.0


def _signal(horizon="intraday"):
    return {
        "symbol": "1302", "name": "بوان", "trade_horizon": horizon,
        "entry": 40.0, "entry_low": 39.8, "entry_high": 40.2,
        "sl": 39.0, "tp1": 42.0, "tp2": 43.0, "tp3": 44.0,
    }


def test_trade_manager_tracks_observed_sessions_and_time_exit():
    store = MemoryStore()
    mgr = TradeManager(store, Settings())
    assert mgr.add(_signal("multi_session"))
    trade, events = mgr.activate_entry("1302", 40.0, when="2026-08-30T07:45:00+00:00")
    assert events == ["ENTRY"]
    assert trade["sessions_held"] == 1
    mgr.mark_observed_session("1302", "2026-08-31T08:00:00+00:00")
    mgr.mark_observed_session("1302", "2026-09-01T08:00:00+00:00")
    assert store.state()["open_trades"][0]["sessions_held"] == 3
    closed = mgr.time_exit("1302", 41.0, reason="MULTI_SESSION_MAX_HORIZON", when="2026-09-01T12:20:00+00:00")
    assert closed["status"] == "CLOSED_TIME_EXIT"
    assert closed["time_exit_reason"] == "MULTI_SESSION_MAX_HORIZON"
    assert not store.state()["open_trades"]
    assert len(store.history()) == 1


def test_v9_source_guards_reports_and_keeps_learning_overlay_on_second_judge():
    root = Path(__file__).resolve().parents[1]
    service = (root / "app/service.py").read_text(encoding="utf-8")
    bots = (root / "app/telegram/bots.py").read_text(encoding="utf-8")
    settings = (root / "app/config/settings.py").read_text(encoding="utf-8")

    scheduled = service[service.index("async def scheduled_tasks"):service.index("# MARKET CLOSE")]
    assert "_scheduled_daily_report" not in scheduled
    assert "_scheduled_weekly_report" not in scheduled
    assert "_scheduled_horizon_time_exits" in scheduled

    report_block = bots[bots.index("async def send_report"):bots.index("# MARKET CLOSE")]
    assert "send_admin_report" in report_block
    assert "_broadcast_" not in report_block
    assert "daily_report_enabled: bool = False" in settings
    assert "weekly_report_enabled: bool = False" in settings

    # Initial Judge + learning-resolved Judge both carry the same V9 context.
    analysis_block = service[service.index("judge_result = judge_candidate"):service.index("print(\n                            f\"[judge]")]
    assert analysis_block.count("leadership_score=leadership_score") >= 2
    assert analysis_block.count("catalyst_context=catalyst") >= 2
    assert analysis_block.count("horizon=trade_horizon") >= 2


def test_telegram_menu_separates_intraday_and_multi_session():
    bots = (Path(__file__).resolve().parents[1] / "app/telegram/bots.py").read_text(encoding="utf-8")
    for label in (
        "⚡ تداول يومي", "📅 متعدد الجلسات",
        "⚡ يومي — بحث 25", "🌐 يومي — السوق كامل",
        "⚡ متعدد — بحث 25", "🌐 متعدد — السوق كامل",
        "⚡ أداء اليومي", "📅 أداء متعدد الجلسات",
    ):
        assert label in bots


def test_run_search_accepts_trade_horizon_keyword():
    bots = (Path(__file__).resolve().parents[1] / "app/telegram/bots.py").read_text(encoding="utf-8")
    signature = 'async def _run_search(self, update, screen_limit, detail_limit, label, *, full_market=False, trade_horizon="intraday"):'
    assert signature in bots
    assert 'trade_horizon=trade_horizon' in bots


def test_stage1_discovery_uses_gainers_value_acceleration_and_watchlists():
    root = Path(__file__).resolve().parents[1]
    service = (root / "app/service.py").read_text(encoding="utf-8")
    router = (root / "app/data/provider_router.py").read_text(encoding="utf-8")
    sahmk = (root / "app/data/providers/sahmk.py").read_text(encoding="utf-8")
    leadership = (root / "app/strategy/leadership.py").read_text(encoding="utf-8")
    news = (root / "app/news/engine.py").read_text(encoding="utf-8")

    assert "/market/gainers/" in sahmk
    assert "async def active_candidate_quotes" in router
    assert 'selection_source = "volume+value+gainers+watch"' in service
    assert "self.leadership_tracker.acceleration(quote.symbol)" in service
    assert "self.news.watch_symbols(watch_limit)" in service
    assert "self.leadership_tracker.leader_symbols(watch_limit)" in service
    assert "def leader_symbols" in leadership
    assert "def watch_symbols" in news


def test_leadership_tracker_exposes_recent_leaders_for_next_scan():
    store = MemoryStore()
    tracker = LeadershipTracker(store)
    base = datetime(2026, 8, 30, 8, 0, tzinfo=timezone.utc)
    a = SimpleNamespace(symbol="4160", change_percent=1.0)
    b = SimpleNamespace(symbol="6050", change_percent=0.5)
    tracker.update([a, b], -0.2, now=base)
    a.change_percent = 5.0
    b.change_percent = 3.5
    tracker.update([a, b], -0.5, now=base.replace(minute=30))
    leaders = tracker.leader_symbols(2)
    assert leaders[0] == "4160"
    assert set(leaders) == {"4160", "6050"}


def test_news_status_exposes_dynamic_source_degradation_guard():
    root = Path(__file__).resolve().parents[1]
    news = (root / "app/news/engine.py").read_text(encoding="utf-8")
    service = (root / "app/service.py").read_text(encoding="utf-8")
    assert "EMPTY_DYNAMIC_SOURCE" in news
    assert "official_page_returned_no_parseable_announcement_items" in news
    assert "صحة المصدر" in service
    assert "سبب التدهور" in service


def test_open_trades_are_filterable_by_horizon_and_menu_exposes_both():
    root = Path(__file__).resolve().parents[1]
    service = (root / "app/service.py").read_text(encoding="utf-8")
    bots = (root / "app/telegram/bots.py").read_text(encoding="utf-8")
    assert "def open_trades_text(self, horizon=None)" in service
    assert '"⚡ المفتوحة اليومية"' in bots
    assert '"📅 المفتوحة متعدد الجلسات"' in bots
    assert 'self.service.open_trades_text("intraday")' in bots
    assert 'self.service.open_trades_text("multi_session")' in bots


def test_health_exposes_horizon_and_news_state_without_fetching_market_data():
    root = Path(__file__).resolve().parents[1]
    web = (root / "app/web.py").read_text(encoding="utf-8")
    assert '"intraday_enabled"' in web
    assert '"multi_session_enabled"' in web
    assert '"news_source_state"' in web
    assert 'news_stats = _service.news.status()' in web


def test_news_unknown_publication_time_never_becomes_trading_catalyst(tmp_path):
    from app.news.engine import CatalystSnapshot, NewsCatalystEngine
    cfg = SimpleNamespace(
        news_saudi_exchange_url="https://example.test",
        news_timeout_seconds=1,
        news_bootstrap_lookback_hours=96,
        news_max_items=20,
        news_cache_file=str(tmp_path / "news.json"),
        news_enabled=True,
    )
    engine = NewsCatalystEngine(cfg)
    now = datetime(2026, 8, 30, 8, 0, tzinfo=timezone.utc)
    engine._items = [CatalystSnapshot(
        symbol="4160", headline="تعلن الشركة عن توقيع عقد", source="SAUDI_EXCHANGE",
        url="https://example.test/a", published_at=None, category="MATERIAL_EVENT",
        impact="HIGH", score=2.5, direction="POSITIVE", corporate_action=False,
        fetched_at=now.isoformat(), announcement_id="x",
    )]
    assert engine.for_symbol("4160", now=now)["available"] is False
    assert engine.watch_symbols(5) == []
    status = engine.status()
    assert status["display_only_unknown_time_items"] == 1
    assert status["verified_time_items"] == 0
