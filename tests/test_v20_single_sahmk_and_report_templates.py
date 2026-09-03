from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from app.data.provider_router import ProviderRouter
from app.telegram.report_card import build_report_card


class DummyProvider:
    def stats(self):
        return {"daily_requests": 3, "daily_limit": 100, "remaining": 97, "recent_requests": []}


class DummyTasilab:
    def stats(self):
        return {"daily_requests": 0, "recent_requests": []}


def _settings():
    return SimpleNamespace(
        timezone="Asia/Riyadh", provider_switch_on_daily_limit=True,
        sahmk_daily_switch_limit=95, sahmk_local_daily_limit=100,
        provider_fallback_enabled=True, provider_switch_on_429=False,
    )


def test_secondary_sahmk_key_removed_from_runtime_and_env_contract():
    root = Path(__file__).resolve().parents[1]
    settings = (root / "app/config/settings.py").read_text(encoding="utf-8")
    main = (root / "app/main.py").read_text(encoding="utf-8")
    env = (root / ".env.example").read_text(encoding="utf-8")
    render = (root / "render.yaml").read_text(encoding="utf-8")
    assert "sahmk_api_key_2" not in settings
    assert "SahmkKeyPool" not in main
    assert "SAHMK_API_KEY_2" not in env
    assert "SAHMK_API_KEY_2" not in render
    assert not (root / "app/data/sahmk_pool.py").exists()


def test_provider_order_is_single_sahmk_then_tasilab():
    router = ProviderRouter(settings=_settings(), sahmk_provider=DummyProvider(), tasilab_provider=DummyTasilab())
    assert router.provider_order_text() == "SAHMK → Tasilab"
    assert router.active_provider_detail() == "SAHMK"


def test_daily_and_weekly_templates_are_used_and_dynamic(tmp_path):
    root = Path(__file__).resolve().parents[1]
    assert (root / "app/assets/telegram/daily_report_template.png").exists()
    assert (root / "app/assets/telegram/weekly_report_template.png").exists()
    metrics = {
        "period_label":"31-08-2026", "wins":2, "losses":1, "waiting_entry":1,
        "active_open":1, "settled":3, "total_trades":5, "win_rate":66.7,
        "gross_win":5.5, "gross_loss":1.2, "net":4.3,
        "gross_win_sar":4.2, "gross_loss_sar":1.1, "net_sar":3.1,
        "rows":[{"symbol":"7202","type":"multi_session","entry":215.10,"high":226.30,"best_pct":5.2,"status":"WIN"}],
    }
    for period in ("daily", "weekly"):
        out = tmp_path / f"{period}.png"
        build_report_card({**metrics, "period":period}, str(out))
        assert out.exists() and out.stat().st_size > 100_000
        assert Image.open(out).size == (1280, 960)
