import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tradingagents.dataflows import config as config_module


@pytest.fixture
def temp_runtime_settings(monkeypatch, tmp_path):
    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(config_module, "_RUNTIME_CONFIG_PATH", settings_path)
    config_module.reset_runtime_settings()
    yield settings_path
    config_module.reset_runtime_settings()


def test_runtime_settings_persist_and_override_default(temp_runtime_settings):
    config_module.set_config({"llm_provider": "openai"})
    saved = config_module.save_runtime_settings({"llm_provider": "google", "temperature": 0.7})

    assert saved["llm_provider"] == "google"
    assert saved["temperature"] == 0.7
    assert json.loads(temp_runtime_settings.read_text())["llm_provider"] == "google"

    cfg = config_module.get_config()
    assert cfg["llm_provider"] == "google"
    assert cfg["temperature"] == 0.7


def test_runtime_settings_reject_secret_values(temp_runtime_settings):
    with pytest.raises(ValueError, match="secret|api key|password"):
        config_module.save_runtime_settings({"api_key": "shh"})

    with pytest.raises(ValueError, match="secret|api key|password"):
        config_module.save_runtime_settings({"mt5_password": "p@ss"})


def test_runtime_settings_validate_types_and_ranges(temp_runtime_settings):
    with pytest.raises(ValueError, match="max_debate_rounds"):
        config_module.save_runtime_settings({"max_debate_rounds": 0})

    with pytest.raises(ValueError, match="temperature"):
        config_module.save_runtime_settings({"temperature": 2.5})

    with pytest.raises(ValueError, match="backend_url"):
        config_module.save_runtime_settings({"backend_url": "not a url"})


def test_canonical_forex_settings_normalize_and_survive_reload(temp_runtime_settings):
    saved = config_module.save_runtime_settings({
        "forex_default_pair": "eur/usd",
        "forex_default_execution_timeframe": "h1",
        "forex_default_context_timeframes": ["h4", "D1"],
        "forex_default_risk_percent": 1.25,
        "forex_min_rr": 2,
        "forex_max_spread_pips": 2.5,
        "forex_news_blackout_minutes": 45,
        "mt5_poll_interval_seconds": 2,
    })

    assert saved["forex_default_pair"] == "EURUSD"
    assert saved["forex_default_execution_timeframe"] == "H1"
    assert saved["forex_default_context_timeframes"] == ["H4", "D1"]
    config_module._config = None
    reloaded = config_module.get_config()
    assert reloaded["forex_default_risk_percent"] == 1.25
    assert reloaded["forex_min_rr"] == 2.0
    assert reloaded["mt5_poll_interval_seconds"] == 2.0


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("forex_default_pair", "NOT_A_PAIR"),
        ("forex_default_execution_timeframe", "M7"),
        ("forex_default_context_timeframes", []),
        ("forex_default_context_timeframes", ["H4", "h4"]),
        ("forex_default_risk_percent", 0),
        ("forex_default_risk_percent", 5.1),
        ("forex_min_rr", 0),
        ("forex_news_blackout_minutes", 1.5),
        ("forex_news_blackout_minutes", -1),
        ("mt5_poll_interval_seconds", 0.1),
        ("mt5_poll_interval_seconds", True),
        ("forex_market_source", "alpha_vantage"),
    ],
)
def test_canonical_forex_settings_reject_invalid_values(temp_runtime_settings, key, value):
    with pytest.raises((TypeError, ValueError)):
        config_module.save_runtime_settings({key: value})


def test_invalid_patch_is_atomic(temp_runtime_settings):
    config_module.save_runtime_settings({"forex_default_risk_percent": 1.2})
    before = temp_runtime_settings.read_text(encoding="utf-8")

    with pytest.raises(ValueError):
        config_module.save_runtime_settings({
            "forex_default_risk_percent": 2.0,
            "mt5_poll_interval_seconds": 0.01,
        })

    assert temp_runtime_settings.read_text(encoding="utf-8") == before
    assert config_module.get_config()["forex_default_risk_percent"] == 1.2


def test_malformed_settings_file_fails_safely(temp_runtime_settings):
    temp_runtime_settings.write_text("{malformed", encoding="utf-8")
    config_module._config = None
    assert config_module.get_config()["forex_default_pair"] == "EURUSD"


def test_frontend_settings_use_canonical_keys_and_manual_execution():
    root = Path(__file__).resolve().parents[1]
    source = (root / "web/static/app.js").read_text(encoding="utf-8")
    html = (root / "web/static/index.html").read_text(encoding="utf-8")

    assert "payload.forex_default_risk_percent = Number(DOM.settingRiskPercent.value)" in source
    assert "payload.forex_min_rr = Number(DOM.settingMinRR.value)" in source
    assert "payload.forex_max_spread_pips = Number(DOM.settingRiskPercent.value)" not in source
    assert "payload.forex_quote_max_age_seconds = Number(DOM.settingMinRR.value)" not in source
    assert "payload.forex_default_pair" in source
    assert "payload.forex_default_execution_timeframe" in source
    assert "payload.forex_default_context_timeframes" in source
    assert "payload.forex_news_blackout_minutes" in source
    assert "payload.mt5_poll_interval_seconds" in source
    assert 'value="true">ENABLED' not in html
    assert "DISABLED — IMMUTABLE" in html


def test_mt5_runtime_uses_configured_poll_interval(monkeypatch):
    from web.forex_runtime import ForexRuntime

    monkeypatch.setattr(
        "web.forex_runtime.get_config",
        lambda: {"mt5_poll_interval_seconds": 2.5},
    )
    observer = MagicMock()
    journal_manager = MagicMock()
    learning_manager = MagicMock()

    runtime = ForexRuntime(observer, journal_manager, learning_manager)

    assert runtime.service.poll_interval == 2.5
