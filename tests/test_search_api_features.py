from pathlib import Path
from app.market.regime import tasi_context


def test_market_regime_accepts_sahmk_index_change_alias():
    ctx = tasi_context({"index_change_percent": 0.8, "advancing": 140, "declining": 80})
    assert ctx["change_percent"] == 0.8
    assert ctx["regime"] == "BULLISH"


def test_safe_sahmk_default_is_95():
    text = Path("app/config/settings.py").read_text(encoding="utf-8")
    assert "sahmk_daily_switch_limit: int = 95" in text


def test_private_menu_contains_search_and_api_usage():
    text = Path("app/telegram/bots.py").read_text(encoding="utf-8")
    for label in ("🔎 البحث", "⚡ بحث 25", "🎯 بحث 50", "🔭 بحث 100", "📡 استهلاك مزودي البيانات", "🧾 سجل الطلبات", "📡 استهلاك SAHMK", "📡 استهلاك Tasilab"):
        assert label in text


def test_market_status_supports_total_volume():
    text = Path("app/data/providers/sahmk.py").read_text(encoding="utf-8")
    assert '"total_volume"' in text
    assert '"index_value"' in text
    assert '"change_percent"' in text
