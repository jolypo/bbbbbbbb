from __future__ import annotations

import hashlib
import html as html_lib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin

import httpx


@dataclass
class CatalystSnapshot:
    symbol: str
    headline: str
    source: str
    url: str
    published_at: str | None
    category: str
    impact: str
    score: float
    direction: str
    corporate_action: bool
    fetched_at: str
    announcement_id: str

    def to_dict(self):
        return asdict(self)


class _AnchorParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.current_href = None
        self.current_text: list[str] = []
        self.links: list[tuple[str, str]] = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "a":
            self.current_href = dict(attrs).get("href")
            self.current_text = []

    def handle_data(self, data):
        if self.current_href is not None:
            self.current_text.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "a" and self.current_href is not None:
            text = " ".join("".join(self.current_text).split())
            if text:
                self.links.append((self.current_href, text))
            self.current_href = None
            self.current_text = []


class NewsCatalystEngine:
    """Best-effort official Saudi Exchange announcement bootstrap.

    News is context, never a standalone BUY/SELL gate. A provider outage or
    page-layout change degrades to an empty catalyst set and must not stop the
    trading service.
    """

    HIGH_POSITIVE = (
        "awarded", "contract", "توقيع عقد", "ترسية", "عقد", "استحواذ", "acquisition",
        "ارتفاع صافي الربح", "increase in net profit",
        "strategic", "مشروع", "agreement", "اتفاقية",
    )
    HIGH_RISK = (
        "خفض رأس المال", "capital reduction", "حقوق أولوية", "rights issue",
        "زيادة رأس المال", "capital increase", "تعليق التداول", "suspension",
        "خسائر متراكمة", "accumulated losses", "إلغاء عقد", "termination",
    )
    NEGATIVE = (
        "انخفاض صافي الربح", "تراجع صافي الربح", "net profit decreased", "net loss",
        "صافي خسارة", "غرامة", "penalty", "رفض", "دعوى", "lawsuit",
    )
    CORPORATE_ACTION = (
        "توزيعات", "dividend", "أحقية", "eligibility", "حقوق أولوية", "rights",
        "زيادة رأس المال", "capital increase", "خفض رأس المال", "capital reduction",
        "تجزئة", "split", "انتقال", "transfer", "تعليق التداول", "suspension",
    )

    def __init__(self, settings):
        self.s = settings
        self.url = str(getattr(settings, "news_saudi_exchange_url", "") or "").strip()
        self.fallback_enabled = bool(getattr(settings, "news_fallback_enabled", True))
        self.mubasher_rss_url = str(getattr(settings, "news_mubasher_rss_url", "") or "").strip()
        self.mubasher_announcements_url = str(getattr(settings, "news_mubasher_announcements_url", "") or "").strip()
        self.timeout = float(getattr(settings, "news_timeout_seconds", 15.0) or 15.0)
        self.lookback_hours = int(getattr(settings, "news_bootstrap_lookback_hours", 72) or 72)
        self.max_items = int(getattr(settings, "news_max_items", 200) or 200)
        self.cache_path = Path(str(getattr(settings, "news_cache_file", "data/news_cache.json")))
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._last_refresh: datetime | None = None
        self._last_source_reason: str | None = None
        self._items: list[CatalystSnapshot] = self._load_cache()
        self._last_source_state: str = "CACHE_ONLY" if self._items else "UNKNOWN"
        self._effective_source: str = "CACHE" if self._items else "NONE"
        self._provider_states: dict[str, dict] = {
            "SAUDI_EXCHANGE": {"state": "UNKNOWN", "reason": None, "items": 0},
            "MUBASHER_RSS": {"state": "UNKNOWN", "reason": None, "items": 0},
            "MUBASHER_PAGE": {"state": "UNKNOWN", "reason": None, "items": 0},
        }
        self._name_to_symbol: dict[str, str] = {}


    @staticmethod
    def _normalize_name(value: str) -> str:
        text = html_lib.unescape(str(value or "")).strip().lower()
        text = re.sub(r"[\u064b-\u065f\u0670]", "", text)
        text = re.sub(r"[^0-9a-z\u0600-\u06ff]+", " ", text)
        stop = {"شركة", "الشركة", "company", "co", "saudi", "السعودية", "للتجارة", "القابضة"}
        parts = [x for x in text.split() if x not in stop]
        return " ".join(parts).strip()

    def bind_universe(self, companies: Iterable[dict] | None):
        """Bind live company names so official headlines can resolve to TASI symbols.

        Saudi Exchange headlines do not always expose the numeric ticker in the
        visible anchor text, so relying on a four-digit regex alone would miss
        catalysts. Runtime provider metadata is used when available.
        """
        mapping: dict[str, str] = {}
        for item in companies or []:
            symbol = str((item or {}).get("symbol", "") or "").strip()
            if not symbol:
                continue
            for key in ("name", "name_ar", "name_en", "company_name"):
                name = self._normalize_name((item or {}).get(key, ""))
                if len(name) >= 3:
                    mapping[name] = symbol
        self._name_to_symbol = mapping

    def _resolve_symbol(self, text: str) -> str:
        direct = self._symbol_from_text(text)
        if direct:
            return direct
        normalized = self._normalize_name(text)
        # Prefer longer names to avoid a short company token stealing another name.
        for name, symbol in sorted(self._name_to_symbol.items(), key=lambda kv: len(kv[0]), reverse=True):
            if name and name in normalized:
                return symbol
        return ""

    def _load_cache(self) -> list[CatalystSnapshot]:
        try:
            raw = json.loads(self.cache_path.read_text(encoding="utf-8"))
            out = []
            for item in raw if isinstance(raw, list) else []:
                try:
                    out.append(CatalystSnapshot(**item))
                except TypeError:
                    continue
            return out[-self.max_items:]
        except (OSError, ValueError, TypeError):
            return []

    def _save_cache(self):
        try:
            self.cache_path.write_text(
                json.dumps([x.to_dict() for x in self._items[-self.max_items:]], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            print(f"[news] cache write failed: {exc}")

    @staticmethod
    def _symbol_from_text(text: str) -> str:
        # Saudi cash-equity symbols are commonly four digits. Avoid years.
        for match in re.findall(r"(?<!\d)(\d{4})(?!\d)", text or ""):
            year = int(match)
            if 1990 <= year <= 2100:
                continue
            return match
        return ""

    @classmethod
    def classify(cls, headline: str) -> tuple[str, str, float, str, bool]:
        text = (headline or "").strip().lower()
        corporate = any(k.lower() in text for k in cls.CORPORATE_ACTION)
        if any(k.lower() in text for k in cls.HIGH_RISK):
            return "CORPORATE_ACTION" if corporate else "MATERIAL_EVENT", "HIGH", 0.0, "CONTEXT", corporate
        if any(k.lower() in text for k in cls.NEGATIVE):
            return "MATERIAL_EVENT", "HIGH", -4.0, "NEGATIVE", corporate
        if any(k.lower() in text for k in cls.HIGH_POSITIVE):
            # Direction is supportive, but the engine does not know contract size
            # versus annual revenue from a headline alone, so keep the bonus bounded.
            return "MATERIAL_EVENT", "HIGH", 2.5, "POSITIVE", corporate
        if "توزيعات نقدية" in text or "cash dividend" in text or "توزيعات" in text:
            return "DIVIDEND", "MEDIUM", 1.0, "CONTEXT", True
        if "نتائج" in text or "financial results" in text:
            # A results headline without the actual surprise versus expectations
            # is context only; price reaction must decide direction.
            return "FINANCIAL_RESULTS", "HIGH", 0.0, "CONTEXT", corporate
        return "ANNOUNCEMENT", "LOW", 0.5, "CONTEXT", corporate

    @staticmethod
    def _make_id(url: str, headline: str) -> str:
        return hashlib.sha256(f"{url}|{headline}".encode("utf-8")).hexdigest()[:24]

    def _parse_html(self, html: str, fetched_at: datetime) -> list[CatalystSnapshot]:
        parser = _AnchorParser()
        parser.feed(html or "")
        items: list[CatalystSnapshot] = []
        seen: set[str] = set()
        for href, raw_text in parser.links:
            text = html_lib.unescape(raw_text).strip()
            href_l = (href or "").lower()
            text_l = text.lower()
            looks_like_announcement = (
                "announcement" in href_l
                or "إعلان" in text
                or "announces" in text_l
                or "تعلن" in text
            )
            if not looks_like_announcement or len(text) < 12:
                continue
            full_url = urljoin(self.url, href)
            ident = self._make_id(full_url, text)
            if ident in seen:
                continue
            seen.add(ident)
            category, impact, score, direction, corporate = self.classify(text)
            items.append(CatalystSnapshot(
                symbol=self._resolve_symbol(text),
                headline=text[:500],
                source="SAUDI_EXCHANGE",
                url=full_url,
                published_at=None,
                category=category,
                impact=impact,
                score=score,
                direction=direction,
                corporate_action=corporate,
                fetched_at=fetched_at.isoformat(),
                announcement_id=ident,
            ))
            if len(items) >= self.max_items:
                break
        return items


    def _parse_mubasher_rss(self, xml_text: str, fetched_at: datetime) -> list[CatalystSnapshot]:
        """Parse Mubasher's public Saudi-market RSS feed.

        The feed is a discovery/fallback source. Publication timestamps from RSS
        are trusted as source timestamps; classification remains context-only and
        never creates a standalone BUY/SELL.
        """
        try:
            root = ET.fromstring(xml_text or "")
        except ET.ParseError:
            return []
        items: list[CatalystSnapshot] = []
        seen: set[str] = set()
        for node in root.findall(".//item"):
            title = html_lib.unescape((node.findtext("title") or "").strip())
            link = (node.findtext("link") or "").strip()
            desc = html_lib.unescape((node.findtext("description") or "").strip())
            raw_date = (node.findtext("pubDate") or "").strip()
            if not title or len(title) < 8:
                continue
            # Keep the feed broad enough to capture issuer announcements, while
            # symbol resolution determines whether the item can affect a stock.
            published_at = None
            if raw_date:
                try:
                    dt = parsedate_to_datetime(raw_date)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    published_at = dt.astimezone(timezone.utc).isoformat()
                except (TypeError, ValueError, OverflowError):
                    published_at = None
            text = f"{title} {re.sub(r'<[^>]+>', ' ', desc)}".strip()
            ident = self._make_id(link or self.mubasher_rss_url, title)
            if ident in seen:
                continue
            seen.add(ident)
            category, impact, score, direction, corporate = self.classify(title)
            items.append(CatalystSnapshot(
                symbol=self._resolve_symbol(text),
                headline=title[:500],
                source="MUBASHER_RSS",
                url=link or self.mubasher_rss_url,
                published_at=published_at,
                category=category,
                impact=impact,
                score=score,
                direction=direction,
                corporate_action=corporate,
                fetched_at=fetched_at.isoformat(),
                announcement_id=ident,
            ))
            if len(items) >= self.max_items:
                break
        return items

    def _parse_mubasher_page(self, html: str, fetched_at: datetime) -> list[CatalystSnapshot]:
        """Last-resort Mubasher market-announcements page parser.

        The page gives useful current headlines but the listing timestamp can be
        relative/dynamic. Items are therefore stored for visibility and discovery
        only; without a verified publication timestamp they never alter Catalyst
        score.
        """
        parser = _AnchorParser()
        parser.feed(html or "")
        items: list[CatalystSnapshot] = []
        seen: set[str] = set()
        for href, raw_text in parser.links:
            text = html_lib.unescape(raw_text).strip()
            if len(text) < 12:
                continue
            text_l = text.lower()
            if not ("إعلان" in text or "اعلان" in text or "تعلن" in text or "announc" in text_l):
                continue
            full_url = urljoin(self.mubasher_announcements_url, href)
            ident = self._make_id(full_url, text)
            if ident in seen:
                continue
            seen.add(ident)
            category, impact, score, direction, corporate = self.classify(text)
            items.append(CatalystSnapshot(
                symbol=self._resolve_symbol(text),
                headline=text[:500],
                source="MUBASHER_PAGE",
                url=full_url,
                published_at=None,
                category=category,
                impact=impact,
                score=score,
                direction=direction,
                corporate_action=corporate,
                fetched_at=fetched_at.isoformat(),
                announcement_id=ident,
            ))
            if len(items) >= self.max_items:
                break
        return items

    @staticmethod
    async def _get_text(client: httpx.AsyncClient, url: str, user_agent: str) -> str:
        resp = await client.get(url, headers={
            "User-Agent": user_agent,
            "Accept": "application/rss+xml, application/xml, text/xml, text/html;q=0.9, */*;q=0.8",
        })
        resp.raise_for_status()
        return resp.text

    async def refresh(self, *, reason: str = "scheduled") -> dict:
        if not bool(getattr(self.s, "news_enabled", True)):
            return {"ok": False, "reason": "disabled", "items": 0}
        now = datetime.now(timezone.utc)
        parsed_all: list[CatalystSnapshot] = []
        primary_error = None
        fallback_error = None

        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            if self.url:
                try:
                    html_text = await self._get_text(client, self.url, "ALLUQMANU_TASI/11.0")
                    official = self._parse_html(html_text, now)
                    if official:
                        parsed_all.extend(official)
                        self._provider_states["SAUDI_EXCHANGE"] = {"state": "OK", "reason": None, "items": len(official)}
                    else:
                        primary_error = "official_page_returned_no_parseable_announcement_items"
                        self._provider_states["SAUDI_EXCHANGE"] = {"state": "EMPTY_DYNAMIC_SOURCE", "reason": primary_error, "items": 0}
                except Exception as exc:
                    primary_error = str(exc)
                    self._provider_states["SAUDI_EXCHANGE"] = {"state": "ERROR", "reason": primary_error, "items": 0}
            else:
                primary_error = "official_source_not_configured"
                self._provider_states["SAUDI_EXCHANGE"] = {"state": "DISABLED", "reason": primary_error, "items": 0}

            # Use the RSS fallback whenever the official source is unusable OR it
            # produced no verified publication timestamps. This makes Catalyst
            # scoring useful while retaining Saudi Exchange as the primary source.
            official_has_verified_time = any(x.published_at for x in parsed_all if x.source == "SAUDI_EXCHANGE")
            should_fallback = self.fallback_enabled and self.mubasher_rss_url and (primary_error is not None or not official_has_verified_time)
            if should_fallback:
                try:
                    rss_text = await self._get_text(client, self.mubasher_rss_url, "ALLUQMANU_TASI/11.0 (+news-fallback)")
                    fallback = self._parse_mubasher_rss(rss_text, now)
                    if fallback:
                        parsed_all.extend(fallback)
                        self._provider_states["MUBASHER_RSS"] = {"state": "OK", "reason": None, "items": len(fallback)}
                    else:
                        fallback_error = "rss_returned_no_parseable_items"
                        self._provider_states["MUBASHER_RSS"] = {"state": "EMPTY", "reason": fallback_error, "items": 0}
                except Exception as exc:
                    fallback_error = str(exc)
                    self._provider_states["MUBASHER_RSS"] = {"state": "ERROR", "reason": fallback_error, "items": 0}
            elif not self.fallback_enabled:
                self._provider_states["MUBASHER_RSS"] = {"state": "DISABLED", "reason": "fallback_disabled", "items": 0}

            rss_ok = self._provider_states["MUBASHER_RSS"].get("state") == "OK"
            if self.fallback_enabled and self.mubasher_announcements_url and not rss_ok:
                try:
                    page_text = await self._get_text(client, self.mubasher_announcements_url, "ALLUQMANU_TASI/11.0 (+news-page-fallback)")
                    page_items = self._parse_mubasher_page(page_text, now)
                    if page_items:
                        parsed_all.extend(page_items)
                        self._provider_states["MUBASHER_PAGE"] = {"state": "DISPLAY_ONLY_OK", "reason": "publication_time_not_verified", "items": len(page_items)}
                    else:
                        self._provider_states["MUBASHER_PAGE"] = {"state": "EMPTY", "reason": "page_returned_no_parseable_announcements", "items": 0}
                except Exception as exc:
                    self._provider_states["MUBASHER_PAGE"] = {"state": "ERROR", "reason": str(exc), "items": 0}
            elif not self.fallback_enabled:
                self._provider_states["MUBASHER_PAGE"] = {"state": "DISABLED", "reason": "fallback_disabled", "items": 0}

        if not parsed_all:
            self._last_refresh = now
            self._last_source_state = "ERROR" if (primary_error or fallback_error) else "EMPTY"
            self._last_source_reason = fallback_error or primary_error or "no_news_items"
            self._effective_source = "CACHE" if self._items else "NONE"
            print(f"[news] refresh failed reason={reason}: {self._last_source_reason}; cached={len(self._items)}")
            return {"ok": False, "reason": self._last_source_reason, "items": 0, "cached": len(self._items), "source_state": self._last_source_state}

        merged = {x.announcement_id: x for x in self._items}
        for item in parsed_all:
            old = merged.get(item.announcement_id)
            if old is not None:
                item.fetched_at = old.fetched_at
                item.published_at = old.published_at or item.published_at
                if not item.symbol and old.symbol:
                    item.symbol = old.symbol
            merged[item.announcement_id] = item
        # Order by verified publication time when available, otherwise fetched time.
        self._items = sorted(merged.values(), key=lambda x: (x.published_at or x.fetched_at, x.announcement_id))[-self.max_items:]
        self._last_refresh = now
        fallback_ok = self._provider_states["MUBASHER_RSS"].get("state") == "OK"
        page_ok = self._provider_states["MUBASHER_PAGE"].get("state") == "DISPLAY_ONLY_OK"
        primary_ok = self._provider_states["SAUDI_EXCHANGE"].get("state") == "OK"
        if primary_ok and fallback_ok:
            self._last_source_state = "OK_WITH_FALLBACK"
            self._effective_source = "SAUDI_EXCHANGE + MUBASHER_RSS"
        elif fallback_ok:
            self._last_source_state = "FALLBACK_OK"
            self._effective_source = "MUBASHER_RSS"
        elif page_ok:
            self._last_source_state = "DISPLAY_ONLY_FALLBACK"
            self._effective_source = "MUBASHER_PAGE"
        else:
            self._last_source_state = "OK"
            self._effective_source = "SAUDI_EXCHANGE"
        self._last_source_reason = primary_error if fallback_ok and primary_error else None
        self._save_cache()
        print(f"[news] refresh reason={reason} parsed={len(parsed_all)} cached={len(self._items)} effective={self._effective_source}")
        return {
            "ok": True, "reason": reason, "items": len(parsed_all), "cached": len(self._items),
            "source_state": self._last_source_state, "effective_source": self._effective_source,
            "providers": self._provider_states.copy(),
        }

    async def bootstrap(self) -> dict:
        return await self.refresh(reason="startup_bootstrap")

    def for_symbol(self, symbol: str, *, now: datetime | None = None) -> dict:
        now = now or datetime.now(timezone.utc)
        symbol = str(symbol or "").strip()
        cutoff = now - timedelta(hours=max(24, self.lookback_hours))
        candidates = []
        for item in self._items:
            resolved = item.symbol or self._resolve_symbol(item.headline)
            if resolved != symbol:
                continue
            # Trading score requires a verified publication timestamp. The time
            # we fetched a dynamic Saudi Exchange page is NOT the publication
            # time and must never make an old announcement look fresh.
            stamp = item.published_at
            if not stamp:
                continue
            try:
                when = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
                if when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                continue
            if when < cutoff:
                continue
            candidates.append(item)
        if not candidates:
            return {"available": False, "score": 0.0, "impact": "NONE", "items": []}
        score = sum(float(x.score) for x in candidates[-5:])
        score = max(-5.0, min(5.0, score))
        impact = "HIGH" if any(x.impact == "HIGH" for x in candidates[-5:]) else "MEDIUM"
        return {
            "available": True,
            "score": score,
            "impact": impact,
            "corporate_action": any(x.corporate_action for x in candidates[-5:]),
            "items": [x.to_dict() for x in candidates[-5:]],
        }

    def recent(self, limit: int = 8) -> list[dict]:
        limit = max(1, min(20, int(limit or 8)))
        return [x.to_dict() for x in self._items[-limit:]][::-1]

    def watch_symbols(self, limit: int = 8) -> list[str]:
        """Return recent symbols with material/context catalysts for Stage-1.

        This is discovery context only. A symbol entering this watch list still
        has to pass fresh quote, liquidity, structure, anti-chase and Judge.
        """
        limit = max(1, min(25, int(limit or 8)))
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=max(24, self.lookback_hours))
        ranked: dict[str, tuple[int, float, str]] = {}
        impact_rank = {"HIGH": 3, "MEDIUM": 2, "LOW": 1, "NONE": 0}
        for item in self._items:
            symbol = item.symbol or self._resolve_symbol(item.headline)
            if not symbol or not item.published_at:
                continue
            stamp = item.published_at
            try:
                when = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
                if when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                continue
            if when < cutoff:
                continue
            key = (impact_rank.get(str(item.impact).upper(), 0), abs(float(item.score or 0.0)), str(stamp or ""))
            if symbol not in ranked or key > ranked[symbol]:
                ranked[symbol] = key
        ordered = sorted(ranked.items(), key=lambda kv: kv[1], reverse=True)
        return [symbol for symbol, _ in ordered[:limit]]

    def status(self) -> dict:
        return {
            "enabled": bool(getattr(self.s, "news_enabled", True)),
            "source": "SAUDI_EXCHANGE",
            "effective_source": self._effective_source,
            "fallback_enabled": self.fallback_enabled,
            "providers": {k: dict(v) for k, v in self._provider_states.items()},
            "source_state": self._last_source_state,
            "source_reason": self._last_source_reason,
            "cached_items": len(self._items),
            "verified_time_items": sum(1 for x in self._items if x.published_at),
            "display_only_unknown_time_items": sum(1 for x in self._items if not x.published_at),
            "last_refresh": self._last_refresh.isoformat() if self._last_refresh else None,
        }
