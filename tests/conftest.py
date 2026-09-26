"""Isolated defaults; live provider tests require --run-integration."""

import os
from unittest.mock import MagicMock, patch

import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--run-integration", action="store_true", default=False,
        help="Run external-service tests using configured credentials (may incur charges).",
    )


def pytest_configure(config):
    for marker in ("unit", "integration", "smoke"):
        config.addinivalue_line("markers", f"{marker}: {marker}-level tests")
    live = config.getoption("--run-integration")
    if live:
        from dotenv import find_dotenv, load_dotenv

        load_dotenv(find_dotenv(usecwd=True))
        load_dotenv(find_dotenv(".env.enterprise", usecwd=True))
    # Before test collection imports DEFAULT_CONFIG, remove local run settings
    # and prevent package imports/subprocesses from reloading the user's .env.
    env = pytest.MonkeyPatch()
    config.add_cleanup(env.undo)
    env.setenv("PYTHON_DOTENV_DISABLED", "1")
    for name in list(os.environ):
        if name.startswith("TRADINGAGENTS_"):
            env.delenv(name)
    if not live:
        for name in _API_KEY_ENV_VARS:
            env.setenv(name, "placeholder")
        for name in ("OPENAI_BASE_URL", "OPENAI_API_BASE", "OLLAMA_BASE_URL"):
            env.delenv(name, raising=False)


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--run-integration"):
        skip = pytest.mark.skip(reason="live services require --run-integration")
        for item in items:
            if item.get_closest_marker("integration"):
                item.add_marker(skip)


_API_KEY_ENV_VARS = (
    "OPENAI_API_KEY",
    "GOOGLE_API_KEY",
    "ANTHROPIC_API_KEY",
    "XAI_API_KEY",
    "DEEPSEEK_API_KEY",
    "DASHSCOPE_API_KEY",
    "DASHSCOPE_CN_API_KEY",
    "ZHIPU_API_KEY",
    "ZHIPU_CN_API_KEY",
    "MINIMAX_API_KEY",
    "MINIMAX_CN_API_KEY",
    "OPENROUTER_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "ALPHA_VANTAGE_API_KEY",
    "GEMINI_API_KEY",
    "MISTRAL_API_KEY",
    "MOONSHOT_API_KEY",
    "GROQ_API_KEY",
    "NVIDIA_API_KEY",
    "OPENAI_COMPATIBLE_API_KEY",
    "FRED_API_KEY",
)


@pytest.fixture(autouse=True)
def _dummy_api_keys(monkeypatch, request):
    if request.node.get_closest_marker("integration"):
        return
    for env_var in _API_KEY_ENV_VARS:
        monkeypatch.setenv(env_var, "placeholder")


@pytest.fixture(autouse=True)
def _isolate_config():
    """Reset the global dataflows config before and after each test.

    ``set_config`` merges (it never clears keys absent from the override), so a
    test that sets e.g. ``tool_vendors`` would otherwise leak into later tests
    and make routing behavior order-dependent. Replace the global outright so
    every test starts from a clean DEFAULT_CONFIG.
    """
    import copy

    import tradingagents.dataflows.config as config_module
    import tradingagents.default_config as default_config

    config_module._config = copy.deepcopy(default_config.DEFAULT_CONFIG)
    yield
    config_module._config = copy.deepcopy(default_config.DEFAULT_CONFIG)


@pytest.fixture()
def mock_llm_client():
    client = MagicMock()
    client.get_llm.return_value = MagicMock()
    with patch(
        "tradingagents.llm_clients.factory.create_llm_client",
        return_value=client,
    ):
        yield client
