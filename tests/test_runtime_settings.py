import json

import pytest

from tradingagents.dataflows import config as config_module


@pytest.fixture
def temp_runtime_settings(monkeypatch, tmp_path):
    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(config_module, "_RUNTIME_CONFIG_PATH", settings_path)
    yield settings_path


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
