from __future__ import annotations

import json
import time
import httpx
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.data.providers.sahmk import SahmkRateLimitError


class ProviderRouter:
    """Route market data between SAHMK and Tasilab safely.

    Policy:
    - SAHMK is primary while its daily budget is available.
    - A temporary SAHMK 429 never switches provider. The request fails fast
      and the caller can try again after Retry-After.
    - A daily/account/IP daily 429 switches to Tasilab for the rest of the
      Saudi calendar day.
    - Reaching SAHMK_DAILY_SWITCH_LIMIT also switches to Tasilab.
    - A bundled TASI equity universe is always available, so a fresh Render
      deploy can still use Tasilab even when SAHMK is already daily-limited.
    - Live SAHMK company metadata replaces the bundled bootstrap universe
      whenever it is available.
    """

    def __init__(self, settings, sahmk_provider, tasilab_provider):
        self.s = settings
        self.sahmk = sahmk_provider
        self.tasilab = tasilab_provider
        self.tz = ZoneInfo(getattr(settings, "timezone", "Asia/Riyadh"))
        self._last_day = self._today_key()
        self._forced_daily_switch = False
        self._last_switch_reason = None
        self._last_switch_at = None
        self._last_call_provider_detail = "SAHMK"

        # Short Stage-1 cache: running Intraday then Multi-Session back-to-back
        # should reuse the same delayed market discovery snapshot instead of
        # spending three more SAHMK Free requests immediately.
        self._candidate_pool_cache = {}
        self._last_candidate_pool_diag = {
            "requested": 0,
            "selected": 0,
            "coverage_ratio": 0.0,
            "quality": "UNKNOWN",
            "cached": False,
            "mode": "uninitialized",
            "sources": {},
        }

        self._runtime_cache_path = (
            Path(getattr(settings, "state_dir", "data")) / "universe_cache.json"
        )
        self._bootstrap_path = self._resolve_bootstrap_path()
        self._universe_symbols, self._universe_source = self._load_best_universe()
        self._sync_tasilab_universe()

        print(
            f"[router] universe ready: {len(self._universe_symbols)} symbols "
            f"source={self._universe_source}"
        )

    # =========================================================
    # TIME
    # =========================================================

    def _today_key(self):
        return datetime.now(self.tz).date().isoformat()

    def _reset_day_if_needed(self):
        current = self._today_key()
        if current != self._last_day:
            self._last_day = current
            self._forced_daily_switch = False
            self._last_switch_reason = None
            self._last_switch_at = None
            print("[router] new Saudi day; SAHMK restored as primary provider")

    # =========================================================
    # UNIVERSE
    # =========================================================

    def _resolve_bootstrap_path(self):
        configured = getattr(self.s, "bootstrap_universe_file", "app/data/tasi_universe.json")
        path = Path(str(configured))
        if path.is_absolute():
            return path

        # Render/Docker starts with the repository at /app. Local tests may
        # start from another cwd, so also resolve relative to this module.
        cwd_path = Path.cwd() / path
        if cwd_path.exists():
            return cwd_path

        app_dir = Path(__file__).resolve().parents[2]
        if str(path).startswith("app/"):
            return app_dir.parent / path
        return app_dir / "data" / path.name

    @staticmethod
    def _extract_symbols(payload):
        if not isinstance(payload, dict):
            return []
        symbols = payload.get("symbols")
        if not isinstance(symbols, list):
            return []
        return list(
            dict.fromkeys(
                str(symbol).strip()
                for symbol in symbols
                if str(symbol).strip()
            )
        )

    def _load_json_symbols(self, path):
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
            return self._extract_symbols(payload)
        except Exception:
            return []

    def _load_best_universe(self):
        runtime = self._load_json_symbols(self._runtime_cache_path)
        if runtime:
            return runtime, "runtime_cache"

        bundled = self._load_json_symbols(self._bootstrap_path)
        if bundled:
            return bundled, "bundled_bootstrap"

        return [], "none"

    def _save_runtime_universe(self):
        if not self._universe_symbols:
            return
        try:
            self._runtime_cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._runtime_cache_path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(
                    {
                        "market": "TASI",
                        "source": "sahmk_runtime",
                        "updated_at": datetime.now(self.tz).isoformat(),
                        "symbols": self._universe_symbols,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            tmp.replace(self._runtime_cache_path)
        except Exception as exc:
            print(f"[router] universe cache save failed: {exc}")

    def _sync_tasilab_universe(self):
        if hasattr(self.tasilab, "set_universe"):
            self.tasilab.set_universe(self._universe_symbols)

    def cached_companies(self):
        """Return the local runtime/bundled universe without any API request."""
        return self._bootstrap_companies()

    def _bootstrap_companies(self):
        return [
            {
                "symbol": symbol,
                "name": "",
                "name_en": "",
                "sector": "",
                "security_type": "equity",
                "metadata_source": self._universe_source,
            }
            for symbol in self._universe_symbols
        ]

    # =========================================================
    # SAHMK DAILY STATE
    # =========================================================

    def _sahmk_stats(self):
        try:
            stats = self.sahmk.stats() if hasattr(self.sahmk, "stats") else {}
            return stats if isinstance(stats, dict) else {}
        except Exception as exc:
            print(f"[router] unable to read SAHMK stats: {exc}")
            return {}

    def _sahmk_switch_limit(self):
        return max(1, int(getattr(self.s, "sahmk_daily_switch_limit", 90)))

    def _sahmk_daily_limit_reached(self):
        self._reset_day_if_needed()

        if self._forced_daily_switch:
            return True

        if not getattr(self.s, "provider_switch_on_daily_limit", True):
            return False

        stats = self._sahmk_stats()
        if bool(stats.get("daily_exhausted", False)):
            return True

        try:
            used = int(stats.get("daily_requests", 0) or 0)
        except (TypeError, ValueError):
            used = 0

        return used >= self._sahmk_switch_limit()

    def _activate_daily_switch(self, reason):
        if not self._forced_daily_switch:
            print(f"[router] switching to Tasilab for rest of Saudi day: {reason}")
        self._forced_daily_switch = True
        self._last_switch_reason = str(reason)
        self._last_switch_at = datetime.now(self.tz).strftime("%H:%M:%S")
        self._sync_tasilab_universe()

    def active_provider(self):
        return "tasilab" if self._sahmk_daily_limit_reached() else "sahmk"

    def active_provider_detail(self):
        """Human-readable active source without exposing the API key."""
        return "TASILAB" if self._sahmk_daily_limit_reached() else "SAHMK"

    def provider_order_text(self):
        return "SAHMK → Tasilab"

    def last_call_provider_detail(self):
        """Actual provider that served the most recent routed data call."""
        return str(self._last_call_provider_detail or self.active_provider_detail())

    def _http_fallback_allowed(self, exc):
        if not getattr(self.s, "provider_fallback_enabled", True):
            return False
        if not isinstance(exc, httpx.HTTPStatusError):
            return False
        status = getattr(getattr(exc, "response", None), "status_code", 0) or 0
        # 403 can mean an invalid/unauthorized SAHMK key or endpoint permission;
        # 5xx is a provider outage. Both are safe reasons for a one-call fallback.
        return status == 403 or status in (500, 502, 503, 504)

    # =========================================================
    # GENERIC CALL
    # =========================================================

    async def _call(self, method_name, *args, **kwargs):
        if self._sahmk_daily_limit_reached():
            method = getattr(self.tasilab, method_name)
            result = await method(*args, **kwargs)
            self._last_call_provider_detail = "TASILAB"
            return result

        try:
            method = getattr(self.sahmk, method_name)
            result = await method(*args, **kwargs)
            self._last_call_provider_detail = self.active_provider_detail()
        except SahmkRateLimitError as exc:
            if exc.daily_exhausted:
                self._activate_daily_switch(str(exc))
                method = getattr(self.tasilab, method_name)
                result = await method(*args, **kwargs)
                self._last_call_provider_detail = "TASILAB"
                return result

            print(
                "[router] temporary SAHMK throttle; provider unchanged; "
                f"retry_after={exc.retry_after:.0f}s"
            )
            raise
        except httpx.HTTPStatusError as exc:
            if self._http_fallback_allowed(exc):
                status = exc.response.status_code
                print(f"[router] SAHMK HTTP {status}; one-call fallback to Tasilab for {method_name}")
                method = getattr(self.tasilab, method_name)
                result = await method(*args, **kwargs)
                self._last_call_provider_detail = "TASILAB (ONE-CALL FALLBACK)"
                return result
            raise

        return result

    # =========================================================
    # COMPANIES / UNIVERSE
    # =========================================================

    async def companies(self, market="TASI"):
        # Once daily-limited, serve bundled/runtime symbols instead of making
        # another SAHMK request. This is what makes fresh Render deploys robust.
        if self._sahmk_daily_limit_reached():
            if self._universe_symbols:
                return self._bootstrap_companies()
            raise RuntimeError(
                "SAHMK daily limit reached and no TASI fallback universe is available"
            )

        try:
            companies = await self.sahmk.companies(market)
        except SahmkRateLimitError as exc:
            if exc.daily_exhausted:
                self._activate_daily_switch(str(exc))
                if self._universe_symbols:
                    return self._bootstrap_companies()
            else:
                print(
                    "[router] temporary SAHMK throttle while refreshing universe; "
                    f"using {self._universe_source} symbols; "
                    f"retry_after={exc.retry_after:.0f}s"
                )
                if self._universe_symbols:
                    return self._bootstrap_companies()
            raise
        except httpx.HTTPStatusError as exc:
            if self._http_fallback_allowed(exc) and self._universe_symbols:
                print(f"[router] SAHMK HTTP {exc.response.status_code}; using bundled/runtime universe")
                return self._bootstrap_companies()
            raise

        symbols = []
        filtered_companies = []
        for item in companies:
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("symbol", "")).strip()
            security_type = str(item.get("security_type", "")).lower()
            if not symbol:
                continue
            # Full-market mode is for Main Market equities only. Do not let
            # REITs, ETFs, rights or other instruments silently enter the stock
            # universe when the provider returns mixed security types.
            if security_type and not any(
                token in security_type for token in ("equity", "stock", "share")
            ):
                continue
            symbols.append(symbol)
            filtered_companies.append(item)

        if symbols:
            self._universe_symbols = list(dict.fromkeys(symbols))
            self._universe_source = "sahmk_runtime"
            self._save_runtime_universe()
            self._sync_tasilab_universe()

        return filtered_companies

    # =========================================================
    # MARKET DATA METHODS
    # =========================================================

    async def market_summary(self):
        return await self._call("market_summary")

    async def quote(self, symbol):
        return await self._call("quote", symbol)

    async def quotes(self, symbols):
        if self._sahmk_daily_limit_reached():
            return await self.tasilab.quotes(symbols)

        try:
            return await self.sahmk.quotes(symbols)
        except SahmkRateLimitError as exc:
            if exc.daily_exhausted:
                self._activate_daily_switch(str(exc))
                return await self.tasilab.quotes(symbols)

            print(
                "[router] temporary SAHMK throttle in quotes; "
                f"Tasilab NOT activated; retry_after={exc.retry_after:.0f}s"
            )
            return {}
        except httpx.HTTPStatusError as exc:
            if self._http_fallback_allowed(exc):
                print(f"[router] SAHMK HTTP {exc.response.status_code}; one-call fallback to Tasilab quotes")
                return await self.tasilab.quotes(symbols)
            raise

    async def monitor_quotes(self, symbols):
        """Monitor all open trades with Tasilab first to protect SAHMK quota.

        Tasilab supports bulk/single degraded paths and is therefore better suited
        to recurring monitoring. If it is unavailable, fall back to SAHMK while
        its daily budget remains available. Freshness is still enforced by the
        TradingService before any TP/SL decision.
        """
        normalized = list(dict.fromkeys(str(s).strip() for s in symbols if str(s).strip()))
        if not normalized:
            return {}
        try:
            result = await self.tasilab.quotes(normalized)
            if result:
                return result
        except Exception as exc:
            print(f"[router] Tasilab monitor quotes failed: {exc}")

        if self._sahmk_daily_limit_reached():
            return {}
        try:
            return await self.sahmk.quotes(normalized)
        except Exception as exc:
            print(f"[router] SAHMK monitor fallback failed: {exc}")
            return {}

    async def market_is_open(self):
        """Use provider market status as a holiday/session gate when available."""
        try:
            if hasattr(self.tasilab, "market_is_open"):
                return await self.tasilab.market_is_open()
        except Exception as exc:
            print(f"[router] market-status check unavailable: {exc}")
        return None

    async def top_volume_quotes(self, limit=50, market="TASI"):
        """Return up to *limit* active TASI names without silently truncating 100 to 50.

        SAHMK's market-volume response may contain fewer rows than requested in
        practice. For wide scans we merge its Free top-volume and top-value
        endpoints, deduplicate by symbol, then use Tasilab only to fill any
        remaining gap. This keeps Search 25/50 economical while making Search
        100 truthful about how many live candidates were actually screened.
        """
        requested = max(1, min(int(limit), 100))

        def merge_unique(*groups):
            merged = {}
            for group in groups:
                for q in (group or []):
                    symbol = str(getattr(q, "symbol", "") or "").strip()
                    if not symbol:
                        continue
                    # Preserve the first source but enrich obvious missing fields.
                    if symbol not in merged:
                        merged[symbol] = q
                    else:
                        existing = merged[symbol]
                        for attr in ("value", "volume", "price", "change_percent", "updated_at", "bid", "ask"):
                            try:
                                if getattr(existing, attr, None) in (None, 0, 0.0, "") and getattr(q, attr, None) not in (None, ""):
                                    setattr(existing, attr, getattr(q, attr))
                            except Exception:
                                pass
            rows = list(merged.values())
            rows.sort(key=lambda q: (float(getattr(q, "value", 0) or 0), float(getattr(q, "volume", 0) or 0)), reverse=True)
            return rows

        if self._sahmk_daily_limit_reached():
            self._sync_tasilab_universe()
            return (await self.tasilab.top_volume_quotes(requested, market))[:requested]

        try:
            volume_rows = await self.sahmk.top_volume_quotes(requested, market)
            combined = merge_unique(volume_rows)

            # For wide scans, SAHMK can return fewer rows than the requested
            # limit. Use its separate Free top-value ranking as a second view.
            if len(combined) < requested and requested > 50 and hasattr(self.sahmk, "top_value_quotes"):
                try:
                    value_rows = await self.sahmk.top_value_quotes(requested, market)
                    combined = merge_unique(combined, value_rows)
                    print(f"[router] wide-scan SAHMK merge volume+value: {len(combined)}/{requested}")
                except SahmkRateLimitError:
                    raise
                except Exception as exc:
                    print(f"[router] SAHMK top-value fill unavailable: {exc}")

            # If the two SAHMK rankings overlap heavily, use the configured
            # secondary provider to fill only the missing breadth.
            if len(combined) < requested and requested > 50:
                try:
                    self._sync_tasilab_universe()
                    secondary = await self.tasilab.top_volume_quotes(requested, market)
                    combined = merge_unique(combined, secondary)
                    print(f"[router] wide-scan after Tasilab fill: {len(combined)}/{requested}")
                except Exception as exc:
                    print(f"[router] Tasilab wide-scan fill unavailable: {exc}")

            if len(combined) < requested:
                print(f"[router] requested {requested} active symbols but providers supplied {len(combined)} unique fresh rows")
            return combined[:requested]

        except SahmkRateLimitError as exc:
            if exc.daily_exhausted:
                self._activate_daily_switch(str(exc))
                self._sync_tasilab_universe()
                return (await self.tasilab.top_volume_quotes(requested, market))[:requested]

            print(
                "[router] temporary SAHMK throttle in top-volume; "
                f"Tasilab NOT activated; retry_after={exc.retry_after:.0f}s"
            )
            return []
        except httpx.HTTPStatusError as exc:
            if self._http_fallback_allowed(exc):
                self._sync_tasilab_universe()
                print(f"[router] SAHMK HTTP {exc.response.status_code}; one-call fallback to Tasilab top-volume")
                return (await self.tasilab.top_volume_quotes(requested, market))[:requested]
            raise


    def candidate_pool_diagnostics(self):
        """Return the latest Stage-1 discovery diagnostics without API calls."""
        diag = dict(self._last_candidate_pool_diag or {})
        diag["sources"] = {
            key: dict(value or {})
            for key, value in dict(diag.get("sources", {}) or {}).items()
        }
        return diag

    async def active_candidate_quotes(self, limit=50, market="TASI", watch_symbols=None):
        """Build a Saudi-native Stage-1 discovery pool with transparent diagnostics.

        The pool combines SAHMK market activity views (volume, traded value,
        gainers) and bounded catalyst/persistent-leader watch symbols.  The
        returned list is only a ranker pool; Hunter/Judge still own the trade
        decision.  Diagnostics are cached alongside the rows so Telegram can
        truthfully show which discovery channels were available for each scan.
        """
        requested = max(1, min(int(limit), 100))
        watch_limit = max(0, min(12, int(getattr(self.s, "stage1_watchlist_limit", 6) or 6)))
        watch = list(dict.fromkeys(str(s).strip() for s in (watch_symbols or []) if str(s).strip()))[:watch_limit]
        use_value = bool(getattr(self.s, "stage1_use_top_value", True)) and hasattr(self.sahmk, "top_value_quotes")
        use_gainers = bool(getattr(self.s, "stage1_use_gainers", True)) and hasattr(self.sahmk, "top_gainers_quotes")
        cache_seconds = max(0, min(600, int(getattr(self.s, "stage1_candidate_cache_seconds", 180) or 0)))
        cache_key = (str(market).upper(), requested, tuple(watch))

        diag = {
            "requested": requested,
            "selected": 0,
            "coverage_ratio": 0.0,
            "quality": "UNKNOWN",
            "cached": False,
            "mode": "sahmk_multi_source",
            "sources": {
                "volume": {"enabled": True, "status": "pending", "count": 0, "provider": "SAHMK", "error": ""},
                "value": {"enabled": use_value, "status": "pending" if use_value else "disabled", "count": 0, "provider": "SAHMK", "error": ""},
                "gainers": {"enabled": use_gainers, "status": "pending" if use_gainers else "disabled", "count": 0, "provider": "SAHMK", "error": ""},
                "watch": {"enabled": bool(watch), "status": "pending" if watch else "idle", "count": 0, "provider": "router", "error": ""},
            },
        }

        def clone_diag(value):
            out = dict(value or {})
            out["sources"] = {
                key: dict(item or {})
                for key, item in dict(out.get("sources", {}) or {}).items()
            }
            return out

        def set_source(name, status, count=0, provider=None, error=""):
            item = diag["sources"].setdefault(name, {})
            item["status"] = str(status)
            item["count"] = int(count or 0)
            if provider is not None:
                item["provider"] = str(provider)
            item["error"] = str(error or "")[:180]

        def finalize(rows, mode=None, cached=False):
            rows = list(rows or [])[:requested]
            if mode:
                diag["mode"] = str(mode)
            diag["selected"] = len(rows)
            diag["coverage_ratio"] = len(rows) / max(1, requested)
            diag["cached"] = bool(cached)
            enabled_core = [
                key for key in ("volume", "value", "gainers")
                if bool(diag["sources"].get(key, {}).get("enabled"))
            ]
            core_ok = all(diag["sources"][key].get("status") == "ok" for key in enabled_core)
            diag["quality"] = "FULL" if core_ok and len(rows) >= requested else "DEGRADED"
            self._last_candidate_pool_diag = clone_diag(diag)
            return rows

        cached = self._candidate_pool_cache.get(cache_key)
        if cache_seconds and cached and (time.monotonic() - cached[0]) < cache_seconds:
            print(f"[router] candidate-pool cache hit requested={requested} watch={len(watch)}")
            cached_rows = list(cached[1])
            if len(cached) >= 3:
                self._last_candidate_pool_diag = clone_diag(cached[2])
                self._last_candidate_pool_diag["cached"] = True
            else:
                finalize(cached_rows, mode="cache_legacy", cached=True)
            return cached_rows

        def mark(q, source):
            try:
                raw = dict(getattr(q, "raw", None) or {})
                sources = list(raw.get("stage1_sources", []) or [])
                if source not in sources:
                    sources.append(source)
                raw["stage1_sources"] = sources
                q.raw = raw
            except Exception:
                pass
            return q

        async def tasilab_degraded():
            diag["mode"] = "tasilab_degraded"
            self._sync_tasilab_universe()
            try:
                rows = [mark(q, "top_volume_secondary") for q in await self.tasilab.top_volume_quotes(requested, market)]
                set_source("volume", "ok", len(rows), provider="Tasilab")
            except Exception as exc:
                print(f"[router] candidate-pool Tasilab active list unavailable: {exc}")
                rows = []
                set_source("volume", "unavailable", 0, provider="Tasilab", error=exc)
            if use_value:
                set_source("value", "fallback_unavailable", 0, provider="SAHMK", error="SAHMK daily switch")
            if use_gainers:
                set_source("gainers", "fallback_unavailable", 0, provider="SAHMK", error="SAHMK daily switch")
            if watch:
                try:
                    watched = await self.monitor_quotes(watch)
                    watch_rows = [mark(q, "watchlist") for q in watched.values()]
                    rows.extend(watch_rows)
                    set_source("watch", "ok", len(watch_rows), provider="router")
                except Exception as exc:
                    print(f"[router] candidate-pool watchlist unavailable: {exc}")
                    set_source("watch", "unavailable", 0, provider="router", error=exc)
            # Preserve order and deduplicate.
            out, seen = [], set()
            for q in rows:
                sym = str(getattr(q, "symbol", "") or "").strip()
                if sym and sym not in seen:
                    out.append(q)
                    seen.add(sym)
            final_rows = finalize(out, mode="tasilab_degraded")
            if cache_seconds:
                self._candidate_pool_cache[cache_key] = (
                    time.monotonic(), list(final_rows), clone_diag(self._last_candidate_pool_diag)
                )
            return final_rows

        if self._sahmk_daily_limit_reached():
            return await tasilab_degraded()

        groups = {"volume": [], "value": [], "gainers": [], "watch": []}
        try:
            groups["volume"] = [mark(q, "top_volume") for q in await self.sahmk.top_volume_quotes(requested, market)]
            set_source("volume", "ok", len(groups["volume"]))
        except SahmkRateLimitError as exc:
            if exc.daily_exhausted:
                self._activate_daily_switch(str(exc))
                return await tasilab_degraded()
            set_source("volume", "throttled", 0, error=f"retry_after={exc.retry_after:.0f}s")
            print(f"[router] candidate-pool top-volume throttled: retry_after={exc.retry_after:.0f}s")
        except Exception as exc:
            set_source("volume", "unavailable", 0, error=exc)
            print(f"[router] candidate-pool top-volume unavailable: {exc}")

        if use_value:
            try:
                groups["value"] = [mark(q, "top_value") for q in await self.sahmk.top_value_quotes(requested, market)]
                set_source("value", "ok", len(groups["value"]))
            except SahmkRateLimitError as exc:
                if exc.daily_exhausted:
                    self._activate_daily_switch(str(exc))
                    return await tasilab_degraded()
                set_source("value", "throttled", 0, error=f"retry_after={exc.retry_after:.0f}s")
                print(f"[router] candidate-pool top-value throttled: retry_after={exc.retry_after:.0f}s")
            except Exception as exc:
                set_source("value", "unavailable", 0, error=exc)
                print(f"[router] candidate-pool top-value unavailable: {exc}")

        if use_gainers:
            try:
                groups["gainers"] = [mark(q, "top_gainers") for q in await self.sahmk.top_gainers_quotes(requested, market)]
                set_source("gainers", "ok", len(groups["gainers"]))
            except SahmkRateLimitError as exc:
                if exc.daily_exhausted:
                    self._activate_daily_switch(str(exc))
                    return await tasilab_degraded()
                set_source("gainers", "throttled", 0, error=f"retry_after={exc.retry_after:.0f}s")
                print(f"[router] candidate-pool top-gainers throttled: retry_after={exc.retry_after:.0f}s")
            except Exception as exc:
                set_source("gainers", "unavailable", 0, error=exc)
                print(f"[router] candidate-pool top-gainers unavailable: {exc}")

        if watch:
            try:
                watched = await self.monitor_quotes(watch)
                groups["watch"] = [mark(q, "watchlist") for q in watched.values()]
                set_source("watch", "ok", len(groups["watch"]), provider="router")
            except Exception as exc:
                set_source("watch", "unavailable", 0, provider="router", error=exc)
                print(f"[router] candidate-pool watchlist unavailable: {exc}")

        # Reserve bounded slots per discovery channel, then fill any holes from
        # a weighted rank. This keeps Search25 == 25 while guaranteeing that
        # fast gainers/catalysts are not buried by raw share-volume ranking.
        shares = {
            "watch": max(0.0, min(0.20, float(getattr(self.s, "stage1_watch_share", 0.10) or 0.10))),
            "gainers": max(0.10, min(0.40, float(getattr(self.s, "stage1_gainers_share", 0.25) or 0.25))),
            "value": max(0.15, min(0.45, float(getattr(self.s, "stage1_value_share", 0.30) or 0.30))),
        }
        non_volume_total = sum(shares.values())
        if non_volume_total > 0.90:
            scale = 0.90 / non_volume_total
            for key in list(shares):
                shares[key] *= scale
        shares["volume"] = max(0.10, 1.0 - sum(shares.values()))
        slots = {k: int(requested * v) for k, v in shares.items()}
        slots["volume"] += requested - sum(slots.values())

        selected, selected_map = [], {}

        def add(q):
            sym = str(getattr(q, "symbol", "") or "").strip()
            if not sym:
                return
            if sym in selected_map:
                existing = selected_map[sym]
                eraw = dict(getattr(existing, "raw", None) or {})
                qraw = dict(getattr(q, "raw", None) or {})
                eraw["stage1_sources"] = list(dict.fromkeys(list(eraw.get("stage1_sources", []) or []) + list(qraw.get("stage1_sources", []) or [])))
                existing.raw = eraw
                for attr in ("value", "volume", "price", "change_percent", "updated_at", "bid", "ask"):
                    if getattr(existing, attr, None) in (None, 0, 0.0, "") and getattr(q, attr, None) not in (None, ""):
                        setattr(existing, attr, getattr(q, attr))
                return
            selected.append(q)
            selected_map[sym] = q

        for source in ("watch", "gainers", "value", "volume"):
            for q in groups[source][:max(0, slots[source])]:
                add(q)

        scored = {}
        source_weight = {"gainers": 1.25, "value": 1.15, "volume": 1.0, "watch": 1.35}
        for source, rows in groups.items():
            n = max(1, len(rows))
            for rank, q in enumerate(rows):
                sym = str(getattr(q, "symbol", "") or "").strip()
                if not sym:
                    continue
                score = source_weight[source] * (1.0 - rank / n)
                old = scored.get(sym, (0.0, q))
                scored[sym] = (old[0] + score, old[1])
        for _, q in sorted(scored.values(), key=lambda x: x[0], reverse=True):
            if len(selected) >= requested:
                break
            add(q)

        if len(selected) < requested:
            for source in ("gainers", "value", "volume", "watch"):
                for q in groups[source]:
                    if len(selected) >= requested:
                        break
                    add(q)

        final_rows = finalize(selected, mode="sahmk_multi_source")
        if cache_seconds:
            self._candidate_pool_cache[cache_key] = (
                time.monotonic(), list(final_rows), clone_diag(self._last_candidate_pool_diag)
            )
            if len(self._candidate_pool_cache) > 16:
                oldest = min(self._candidate_pool_cache, key=lambda k: self._candidate_pool_cache[k][0])
                self._candidate_pool_cache.pop(oldest, None)
        print(
            f"[router] candidate-pool requested={requested} selected={len(final_rows)} "
            f"quality={self._last_candidate_pool_diag.get('quality')} "
            f"volume={len(groups['volume'])} value={len(groups['value'])} "
            f"gainers={len(groups['gainers'])} watch={len(groups['watch'])}"
        )
        return final_rows

    # =========================================================
    # STATS
    # =========================================================

    def stats(self):
        self._reset_day_if_needed()
        sahmk_stats = self._sahmk_stats()

        try:
            tasilab_stats = self.tasilab.stats() if hasattr(self.tasilab, "stats") else {}
        except Exception:
            tasilab_stats = {}

        active = self.active_provider()
        switch_reason = str(self._last_switch_reason or "")
        switch_lower = switch_reason.lower()
        sahmk_blocked_for_day = bool(self._forced_daily_switch or sahmk_stats.get("daily_exhausted", False))
        if sahmk_blocked_for_day and "ip daily" in switch_lower:
            sahmk_block_type = "IP_DAILY"
        elif sahmk_blocked_for_day:
            sahmk_block_type = "DAILY"
        else:
            sahmk_block_type = "NONE"
        sahmk_runtime_state = (
            "BLOCKED_IP_DAILY" if sahmk_block_type == "IP_DAILY"
            else "BLOCKED_DAILY" if sahmk_blocked_for_day
            else "COOLDOWN" if int(sahmk_stats.get("cooldown_remaining", 0) or 0) > 0
            else "AVAILABLE"
        )

        return {
            # Compatibility with existing health endpoints/messages.
            "daily_requests": sahmk_stats.get("daily_requests", 0),
            "daily_limit": sahmk_stats.get(
                "daily_limit", getattr(self.s, "sahmk_local_daily_limit", 100)
            ),
            "remaining": sahmk_stats.get("remaining", "—"),
            "rate_limits": sahmk_stats.get("rate_limits", 0),
            "errors": sahmk_stats.get("errors", 0),

            # Router details.
            "active_provider": active,
            "active_provider_detail": self.active_provider_detail(),
            "provider_order": self.provider_order_text(),
            "sahmk_available": active == "sahmk",
            "sahmk_daily_requests": sahmk_stats.get("daily_requests", 0),
            "sahmk_switch_limit": self._sahmk_switch_limit(),
            "sahmk_daily_switched": active == "tasilab",
            "last_switch_reason": self._last_switch_reason,
            "last_switch_at": self._last_switch_at,
            "sahmk_daily_exhausted": bool(sahmk_stats.get("daily_exhausted", False)),
            "sahmk_blocked_for_day": sahmk_blocked_for_day,
            "sahmk_block_type": sahmk_block_type,
            "sahmk_block_reason": switch_reason if sahmk_blocked_for_day else "",
            "sahmk_runtime_state": sahmk_runtime_state,
            "sahmk_cooldown": int(sahmk_stats.get("cooldown_remaining", 0) or 0) > 0,
            "sahmk_cooldown_remaining": sahmk_stats.get("cooldown_remaining", 0),
            "sahmk_recent_requests": sahmk_stats.get("recent_requests", []),

            # Tasilab details.
            "tasilab_requests": tasilab_stats.get("daily_requests", 0),
            "tasilab_successful_requests": tasilab_stats.get("daily_successful_requests", 0),
            "tasilab_requests_last_minute": tasilab_stats.get("requests_last_minute", 0),
            "tasilab_market_status_last_success": tasilab_stats.get("market_status_last_success"),
            "tasilab_market_status_last_error": tasilab_stats.get("market_status_last_error"),
            "tasilab_rate_limits": tasilab_stats.get("rate_limits", 0),
            "tasilab_errors": tasilab_stats.get("errors", 0),
            "tasilab_bulk_cooldown_remaining": tasilab_stats.get(
                "bulk_cooldown_remaining", 0
            ),
            "tasilab_circuit_open": tasilab_stats.get("circuit_open", False),
            "tasilab_circuit_remaining": tasilab_stats.get("circuit_remaining", 0),
            "tasilab_recent_requests": tasilab_stats.get("recent_requests", []),

            # Universe diagnostics.
            "universe_cache_size": len(self._universe_symbols),
            "universe_source": self._universe_source,
        }
