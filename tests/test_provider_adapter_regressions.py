"""Provider construction honors explicit credentials, endpoint envs, and caps."""

import pytest

from tradingagents.llm_clients.factory import create_llm_client


@pytest.mark.parametrize("provider,model,env", [
    ("openai", "gpt-5.6", "OPENAI_API_KEY"),
    ("deepseek", "deepseek-v4-flash", "DEEPSEEK_API_KEY"),
    ("openrouter", "openai/gpt-5.6", "OPENROUTER_API_KEY"),
])
@pytest.mark.parametrize("ambient", [None, "ambient-test-key"])
def test_explicit_key_works_without_env_and_takes_precedence(monkeypatch, provider, model, env, ambient):
    if ambient is None:
        monkeypatch.delenv(env, raising=False)
    else:
        monkeypatch.setenv(env, ambient)
    llm = create_llm_client(provider, model, api_key="explicit-test-key").get_llm()
    assert llm.openai_api_key.get_secret_value() == "explicit-test-key"


@pytest.mark.parametrize("key", [None, ""])
def test_empty_explicit_key_keeps_provider_env_key(monkeypatch, key):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "provider-test-key")
    llm = create_llm_client("deepseek", "deepseek-v4-flash", api_key=key).get_llm()
    assert llm.openai_api_key.get_secret_value() == "provider-test-key"


@pytest.mark.parametrize("variable", ["OPENAI_API_BASE", "OPENAI_BASE_URL"])
def test_standard_endpoint_env_uses_chat_completions(monkeypatch, variable):
    for name in ("OPENAI_API_BASE", "OPENAI_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(variable, "http://localhost:1234/v1")
    llm = create_llm_client("openai", "gpt-5.6", api_key="test-key").get_llm()
    assert str(llm.root_client.base_url) == "http://localhost:1234/v1/"
    assert llm.use_responses_api is False


def test_endpoint_precedence_matches_sdk(monkeypatch):
    monkeypatch.setenv("OPENAI_API_BASE", "http://localhost:1234/v1")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:5678/v1")
    env_llm = create_llm_client("openai", "gpt-5.6", api_key="test-key").get_llm()
    assert str(env_llm.root_client.base_url) == "http://localhost:1234/v1/"
    explicit = create_llm_client(
        "openai", "gpt-5.6", base_url="https://api.openai.com/v1", api_key="test-key"
    ).get_llm()
    assert str(explicit.root_client.base_url) == "https://api.openai.com/v1/"
    assert explicit.use_responses_api is True


def test_azure_keeps_configured_output_cap(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com/")
    monkeypatch.setenv("OPENAI_API_VERSION", "2025-03-01-preview")
    monkeypatch.delenv("OPENAI_API_BASE", raising=False)
    llm = create_llm_client("azure", "test-deployment", api_key="test-key", max_tokens=123).get_llm()
    assert llm.max_tokens == 123
