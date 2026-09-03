from types import SimpleNamespace
from pathlib import Path

import pytest

from app.news.engine import NewsCatalystEngine


def _settings(tmp_path):
    return SimpleNamespace(
        news_enabled=True,
        news_saudi_exchange_url="https://official.invalid/announcements",
        news_fallback_enabled=True,
        news_mubasher_rss_url="https://feeds.mubasher.info/ar/TDWL/news",
        news_mubasher_announcements_url="https://www.mubasher.info/news/sa/now/announcements",
        news_timeout_seconds=3,
        news_bootstrap_lookback_hours=96,
        news_max_items=200,
        news_cache_file=str(tmp_path / "news.json"),
    )


def test_mubasher_rss_parser_keeps_verified_pubdate_and_symbol(tmp_path):
    engine = NewsCatalystEngine(_settings(tmp_path))
    engine.bind_universe([{"symbol": "4160", "name_ar": "ثمار"}])
    xml = """<?xml version='1.0' encoding='UTF-8'?>
    <rss version='2.0'><channel><item>
      <title>تعلن شركة ثمار عن توقيع عقد جديد</title>
      <link>https://www.mubasher.info/news/123</link>
      <pubDate>Sun, 30 Aug 2026 08:30:00 +0300</pubDate>
      <description>إعلان سوق الأسهم السعودية</description>
    </item></channel></rss>"""
    items = engine._parse_mubasher_rss(xml, __import__('datetime').datetime.now(__import__('datetime').timezone.utc))
    assert len(items) == 1
    assert items[0].source == "MUBASHER_RSS"
    assert items[0].symbol == "4160"
    assert items[0].published_at is not None


@pytest.mark.asyncio
async def test_refresh_falls_back_when_saudi_exchange_403(tmp_path, monkeypatch):
    engine = NewsCatalystEngine(_settings(tmp_path))
    engine.bind_universe([{"symbol": "4160", "name_ar": "ثمار"}])
    xml = """<?xml version='1.0' encoding='UTF-8'?>
    <rss version='2.0'><channel><item>
      <title>تعلن شركة ثمار عن توقيع عقد جديد</title>
      <link>https://www.mubasher.info/news/123</link>
      <pubDate>Sun, 30 Aug 2026 08:30:00 +0300</pubDate>
      <description>إعلان سوق الأسهم السعودية</description>
    </item></channel></rss>"""

    async def fake_get(client, url, user_agent):
        if "official.invalid" in url:
            raise RuntimeError("403 Forbidden")
        return xml

    monkeypatch.setattr(engine, "_get_text", fake_get)
    result = await engine.refresh(reason="test")
    assert result["ok"] is True
    assert result["effective_source"] == "MUBASHER_RSS"
    assert result["providers"]["SAUDI_EXCHANGE"]["state"] == "ERROR"
    assert result["providers"]["MUBASHER_RSS"]["state"] == "OK"
    st = engine.status()
    assert st["verified_time_items"] == 1
    ctx = engine.for_symbol("4160")
    assert ctx["available"] is True


@pytest.mark.asyncio
async def test_page_fallback_is_display_only_when_rss_unavailable(tmp_path, monkeypatch):
    engine = NewsCatalystEngine(_settings(tmp_path))
    engine.bind_universe([{"symbol": "4160", "name_ar": "ثمار"}])
    html = """<html><body>
      <a href='/news/123'>تعلن شركة ثمار عن توقيع عقد جديد</a>
    </body></html>"""

    async def fake_get(client, url, user_agent):
        if "mubasher.info/news/sa/now/announcements" in url:
            return html
        raise RuntimeError("upstream unavailable")

    monkeypatch.setattr(engine, "_get_text", fake_get)
    result = await engine.refresh(reason="test-page")
    assert result["ok"] is True
    assert result["effective_source"] == "MUBASHER_PAGE"
    assert engine.status()["verified_time_items"] == 0
    assert engine.status()["display_only_unknown_time_items"] >= 1
    assert engine.for_symbol("4160")["available"] is False
