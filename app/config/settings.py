from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # =========================================================
    # TELEGRAM
    # =========================================================

    signal_bot_name: str = "TASI_KSA_signal_bot"
    signal_bot_token: str

    profit_bot_name: str = "TASI_KSA_profit11_bot"
    profit_bot_token: str

    loss_bot_name: str = "TASI_KSA_loss1122_bot"
    loss_bot_token: str

    report_bot_name: str = "TASI_KSA_report112233_bot"
    report_bot_token: str

    # Telegram Group
    telegram_chat_id: int

    # Telegram Channel
    telegram_channel_id: int

    # Private admin account
    telegram_admin_user_id: int

    # auto -> webhook on Render / polling locally
    telegram_mode: str = "auto"

    # =========================================================
    # DATA PROVIDERS
    # =========================================================

    # Start the day with SAHMK.
    # When its safe daily limit is reached,
    # automatically switch to Tasilab.
    data_provider_primary: str = "sahmk"

    # =========================================================
    # SAHMK
    # =========================================================

    sahmk_api_key: str
    sahmk_base_url: str = "https://api.sahmk.sa/api/v1"
    sahmk_plan: str = "free"

    # Protect against minute-rate limits
    sahmk_min_request_interval: float = 6.5

    # Official/free daily allowance used by the project
    sahmk_local_daily_limit: int = 100

    # Switch to Tasilab before reaching the absolute daily limit
    sahmk_daily_switch_limit: int = 95

    # If SAHMK returns HTTP 429,
    # temporarily use Tasilab instead of retrying repeatedly.
    sahmk_cooldown_on_429_seconds: int = 120

    # =========================================================
    # TASILAB
    # =========================================================

    tasilab_api_key: str

    # Keep configurable in Render in case their API URL changes.
    tasilab_base_url: str = "https://api.tasilab.com"

    # Request timeout
    tasilab_timeout_seconds: float = 15.0

    # Local safety interval between Tasilab requests.
    # Tasilab has a much larger allowance, but we still avoid bursts.
    tasilab_min_request_interval: float = 0.6
    # Short cache for the lightweight /v1/market/status endpoint.
    # Keeps breadth/TASI status cheap while delayed data is unchanged.
    tasilab_market_status_cache_seconds: int = 60
    # Smaller bulk chunks reduce upstream/Cloudflare pressure.
    tasilab_bulk_chunk_size: int = 20

    # If bulk quotes are unavailable with 5xx, scan a bounded number of
    # symbols through the documented single-quote endpoint.
    tasilab_single_fallback_scan_limit: int = 60

    # Temporarily stop using the bulk endpoint after consecutive 5xx errors.
    tasilab_bulk_cooldown_seconds: int = 300

    # Open a provider-wide circuit only if single quotes themselves fail
    # repeatedly. A broken bulk endpoint alone must not disable Tasilab.
    tasilab_circuit_failure_threshold: int = 3
    tasilab_circuit_cooldown_seconds: int = 300

    # =========================================================
    # PROVIDER ROUTER
    # =========================================================

    # Full provider order:
    # SAHMK -> Tasilab.
    # When the single SAHMK key reaches the safe daily limit,
    # ProviderRouter activates Tasilab for the rest of the Saudi day.
    provider_switch_on_daily_limit: bool = True

    # Legacy compatibility flag. Temporary SAHMK 429 never switches provider.
    # Tasilab is reserved for the daily quota switch only.
    provider_switch_on_429: bool = False

    # If the active provider fails:
    # allow the other provider to serve the request.
    provider_fallback_enabled: bool = True

    # =========================================================
    # HISTORICAL DATA
    # =========================================================

    # Keep Yahoo for historical analysis for now.
    historical_provider: str = "yahoo"

    historical_max_price_gap_pct: float = 15.0
    # Entry-driving intraday research must track the live/delayed quote much more closely.
    intraday_max_price_gap_pct: float = 2.5
    historical_intraday_max_age_minutes: int = 30

    intraday_min_bars: int = 60
    swing_min_bars: int = 120

    # =========================================================
    # STORAGE / HEALTH
    # =========================================================

    state_dir: str = "data"
    database_url: str = ""
    persistent_storage_required: bool = False
    health_interval: int = 600

    # =========================================================
    # SCHEDULER / TRADE MONITOR
    # =========================================================

    # Internal monitoring every 15 minutes (matches delayed-data economics).
    # Scheduler NEVER creates new signals.
    scan_interval_seconds: int = 900

    # Monitor up to 3 open trades per cycle
    trade_monitor_quotes_per_cycle: int = 5

    # Public price update every 20 minutes
    trade_price_update_minutes: int = 20

    # =========================================================
    # MANUAL SIGNAL SCAN
    # =========================================================

    manual_quotes_per_signal: int = 50
    detail_quotes_per_signal: int = 5

    # Saudi-native Stage-1 candidate discovery. Search25/50/100 keeps the
    # requested row count but allocates it across activity channels so a fast
    # gainer or high-value leader is not missed simply because it is not in the
    # raw-share-volume ranking.
    stage1_use_top_value: bool = True
    stage1_use_gainers: bool = True
    stage1_watchlist_limit: int = 6
    stage1_watch_share: float = 0.10
    stage1_gainers_share: float = 0.25
    stage1_value_share: float = 0.30
    stage1_acceleration_bonus_max: float = 8.0
    stage1_persistence_bonus_max: float = 6.0
    stage1_candidate_cache_seconds: int = 180

    # Intraday dual-logic discovery. CORE preserves the conservative structural
    # ranker. EMERGING hunts transitions into leadership and is still subject to
    # the same final risk/Judge gates before publication.
    intraday_emerging_enabled: bool = True
    emerging_leader_min_score: float = 68.0
    emerging_leader_share: float = 0.50

    # V23 admin-enabled intraday leader monitor. One press enables it for the
    # current Saudi trading day; it never publishes a paper trade automatically.
    leader_monitor_interval_minutes: int = 30
    leader_monitor_screen_limit: int = 50
    leader_monitor_detail_limit: int = 6
    # Legacy V24 Saudi-native scanner retained for comparison; WASEEM 20 is the new primary scanner.
    # actual new-trade scans remain market-aware.
    saudi_scanner_interval_minutes: int = 30
    saudi_scanner_screen_limit: int = 100
    saudi_scanner_detail_limit: int = 10

    # V25 WASEEM 20 unified Saudi engine. Enable once; stays enabled until stopped.
    # It starts in the official opening-auction window and scans every 15 minutes.
    waseem20_interval_minutes: int = 15
    waseem20_screen_limit: int = 300
    waseem20_detail_limit: int = 12
    waseem20_opening_auction_start: str = "09:30"
    waseem20_new_entry_end: str = "14:50"
    waseem20_notify_wait: bool = True
    waseem20_notify_leader: bool = False

    # WASEEM 30 Early Hunter — primary engine. WASEEM20 remains available as legacy.
    waseem30_interval_minutes: int = 15
    waseem30_screen_limit: int = 300
    waseem30_detail_limit: int = 20
    waseem30_opening_auction_start: str = "09:30"
    waseem30_new_entry_end: str = "14:50"
    waseem30_notify_radar: bool = True
    waseem30_notify_building: bool = True
    emerging_mtf_weight: float = 0.12
    emerging_max_hunter_boost: float = 8.0

    # A discovered setup is previewed privately and must be confirmed quickly.
    # Confirmation reuses the same scan result and makes no additional market API call.
    signal_confirmation_expiry_minutes: int = 5
    # After public publication, the setup stays WAITING_ENTRY until price actually
    # trades inside the technical entry zone. It is never assumed filled.
    entry_wait_expiry_minutes: int = 1440

    # =========================================================
    # CACHE
    # =========================================================

    market_cache_seconds: int = 600
    universe_refresh_seconds: int = 21600
    bootstrap_universe_file: str = "app/data/tasi_universe.json"

    # Keep TASI level/change/breadth on the primary provider, but optionally
    # supplement market-wide trading volume/value from Mubasher.
    market_totals_use_mubasher: bool = True
    market_totals_mubasher_url: str = "https://www.mubasher.info/markets/TDWL"
    market_totals_timeout_seconds: float = 15.0
    market_totals_cache_seconds: int = 600

    # Market breadth (advancers/decliners) self-healing. When the compact
    # provider summary and Mubasher do not expose breadth, use a cached
    # full-market scan first, then a bounded Tasilab-wide quote snapshot.
    # This never fabricates breadth and never spends SAHMK quota for recovery.
    market_breadth_tasilab_enabled: bool = True
    market_breadth_cache_seconds: int = 900
    market_breadth_min_coverage: float = 0.65
    market_breadth_min_samples: int = 80
    market_breadth_yahoo_fallback_enabled: bool = True
    market_breadth_yahoo_retry_seconds: int = 900

    # =========================================================
    # SAUDI MARKET HOURS
    # =========================================================

    market_open: str = "10:00"
    market_close: str = "15:00"
    market_monitor_close: str = "15:20"
    # Warm market context before the first permitted new-entry scan.
    market_data_start: str = "10:15"

    # Saudi anti-fake-momentum entry window. The first 30 minutes are
    # observation-only; new entries stop before the closing auction.
    signal_window_start: str = "10:30"
    signal_window_end: str = "14:50"
    manual_search_window_end: str = "14:50"

    timezone: str = "Asia/Riyadh"

    allow_off_hours_scan: bool = False

    # =========================================================
    # TRADE HORIZONS / SAUDI PRICE LIMITS
    # =========================================================

    intraday_enabled: bool = True
    multi_session_enabled: bool = True
    two_day_enabled: bool = True
    multi_session_min_days: int = 2
    multi_session_max_days: int = 5

    # Intraday positions are reconciled against a post-close quote only after
    # the delayed-data window has had time to include the closing auction /
    # trade-at-last session. A stale pre-close quote is never used as a fake close.
    intraday_close_reconcile_after: str = "15:35"
    close_quote_min_market_time: str = "15:10"

    # Main-market equities normally use +/-10% from the fourth trading day.
    # Newly listed Main Market equities can use +/-30% for the first 3 days.
    # Provider-supplied limit metadata overrides this default when available.
    normal_daily_price_limit_pct: float = 10.0
    newly_listed_daily_price_limit_pct: float = 30.0
    near_limit_buffer_pct: float = 0.75

    # =========================================================
    # SIGNAL QUALITY
    # =========================================================

    min_score: float = 82
    min_probability: float = 65

    # Saudi-market liquidity gate. Missing/zero traded value is treated as
    # unverified liquidity and rejected for new signals.
    min_daily_traded_value: float = 2_000_000

    # Cumulative traded-value gate is scaled by elapsed continuous-session time.
    # This avoids judging a 10:30 stock by the same cumulative value expected
    # near 14:30 while retaining an absolute execution-liquidity floor.
    liquidity_progress_floor: float = 0.25

    max_daily_signals: int = 3
    max_open_trades: int = 5

    # =========================================================
    # RISK MANAGEMENT
    # =========================================================

    data_max_delay_minutes: int = 30

    min_rr: float = 1.8

    tp1_percent: float = 30
    tp2_percent: float = 30
    tp3_percent: float = 40

    slippage_bps: float = 5
    # Saudi Exchange total trading commission baseline; keep configurable for broker-specific costs.
    fee_bps: float = 15.5

    allow_long: bool = True

    # Paper Trading only
    paper_mode: bool = True

    # =========================================================
    # TRAILING STOP
    # =========================================================

    trailing_stop_enabled: bool = False

    trailing_after_tp1_to_entry: bool = True
    trailing_after_tp2_atr: float = 1.0

    # =========================================================
    # PROFIT / LOSS ALERTS
    # =========================================================

    # Legacy list kept for backward-compatible ENV parsing only.
    profit_alert_thresholds: str = "2,5,10,15,20"
    # V15 policy: alert once at every new whole positive percentage level.
    profit_alert_step_pct: float = 1.0

    near_sl_warning_pct: float = 0.5


    # =========================================================
    # ADAPTIVE LEARNING (bounded; never overrides hard gates)
    # =========================================================

    learning_enabled: bool = True
    learning_file: str = "data/learning_memory.json"
    learning_min_samples: int = 12
    learning_max_adjustment: float = 2.0

    # =========================================================
    # REPORTS / TELEGRAM MEDIA
    # =========================================================

    # Static approved visual assets bundled with the project.
    trade_card_image: str = "app/assets/telegram/trade_card.png"
    profit_update_image: str = "app/assets/telegram/profit_update.png"
    daily_report_image: str = "app/assets/telegram/daily_report.png"
    weekly_report_image: str = "app/assets/telegram/weekly_report.png"

    # Reports are NEVER auto-published. They are generated/sent only on explicit private-admin request.
    daily_report_enabled: bool = False
    daily_report_hour: int = 15
    daily_report_minute: int = 25

    weekly_report_enabled: bool = False

    # Thursday
    weekly_report_weekday: int = 3

    weekly_report_hour: int = 15
    weekly_report_minute: int = 25

    # Hard safety: even if an old scheduler flag is accidentally enabled,
    # reports cannot be published to group/channel.

    # =========================================================
    # NEWS / CATALYST ENGINE
    # =========================================================

    news_enabled: bool = True
    news_saudi_exchange_url: str = (
        "https://www.saudiexchange.sa/wps/portal/saudiexchange/"
        "newsandreports/issuer-news/issuer-announcements?locale=ar"
    )
    # Official source is always attempted first. Mubasher exposes a public
    # Saudi-market RSS feed and is used only as a resilient fallback when the
    # Saudi Exchange page is blocked/dynamic/unusable from Render.
    news_fallback_enabled: bool = True
    news_mubasher_rss_url: str = "https://feeds.mubasher.info/ar/TDWL/news"
    news_mubasher_announcements_url: str = "https://www.mubasher.info/news/sa/now/announcements"
    news_timeout_seconds: float = 15.0
    # Startup at ~10:15 must still see announcements released after the prior
    # session and before today's startup. Cache survives restarts when storage does.
    news_bootstrap_lookback_hours: int = 96
    news_refresh_minutes: int = 30
    news_max_items: int = 200
    news_cache_file: str = "data/news_cache.json"

    # =========================================================
    # PYDANTIC
    # =========================================================

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
