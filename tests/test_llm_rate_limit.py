import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from tradingagents.llm_clients import openai_client, rate_limit
from tradingagents.llm_clients.openai_client import (
    GroqRateLimitedChatOpenAI,
    NormalizedChatOpenAI,
)
from tradingagents.llm_clients.rate_limit import (
    SlidingWindowRateLimiter,
    estimate_chat_tokens,
    groq_recovery_attempts,
    groq_transient_retry_delay,
)


class _Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def test_limiter_waits_for_token_window_instead_of_failing():
    clock = _Clock()
    limiter = SlidingWindowRateLimiter(
        requests_per_minute=30,
        tokens_per_minute=8000,
        window_seconds=60,
        clock=clock,
        sleeper=clock.sleep,
    )
    limiter.acquire(5000)
    limiter.acquire(4000)
    assert clock.sleeps == [60.0]


def test_limiter_reconciles_estimate_with_actual_usage():
    clock = _Clock()
    limiter = SlidingWindowRateLimiter(
        requests_per_minute=30,
        tokens_per_minute=8000,
        window_seconds=60,
        clock=clock,
        sleeper=clock.sleep,
    )
    reservation = limiter.acquire(3000)
    limiter.reconcile(reservation, 7000)
    limiter.acquire(2000)
    assert clock.sleeps == [60.0]


def test_estimator_counts_prompt_tools_and_output_reserve():
    estimate = estimate_chat_tokens(
        [{"role": "user", "content": "x" * 400}],
        {"tools": [{"name": "calculator", "description": "y" * 200}]},
        output_reserve=512,
    )
    assert estimate > 650


class _ProviderError(Exception):
    def __init__(self, status_code, message):
        super().__init__(message)
        self.status_code = status_code


def test_groq_transient_429_waits_for_full_window(monkeypatch):
    monkeypatch.delenv("TRADINGAGENTS_GROQ_RATE_LIMIT_WAIT_SECONDS", raising=False)
    error = _ProviderError(429, "rate_limit_exceeded: tokens per minute")
    assert groq_transient_retry_delay(error) == 60.0


def test_groq_oversized_413_is_not_retried():
    error = _ProviderError(413, "rate_limit_exceeded: request too large")
    assert groq_transient_retry_delay(error) is None


def test_groq_recovery_policy_is_configurable(monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_GROQ_RATE_LIMIT_RECOVERY_RETRIES", "2")
    monkeypatch.setenv("TRADINGAGENTS_GROQ_RATE_LIMIT_WAIT_SECONDS", "17")
    assert groq_recovery_attempts() == 2
    assert groq_transient_retry_delay(
        _ProviderError(429, "Rate limit reached")
    ) == 17.0


class _Limiter:
    def __init__(self):
        self.acquisitions = []

    def acquire(self, tokens):
        self.acquisitions.append(tokens)
        return object()

    def reconcile(self, reservation, actual_tokens):
        self.actual_tokens = actual_tokens


def test_groq_client_waits_and_retries_one_transient_429(monkeypatch):
    limiter = _Limiter()
    calls = []
    sleeps = []

    def fake_generate(self, messages, stop=None, run_manager=None, **kwargs):
        calls.append(messages)
        if len(calls) == 1:
            raise _ProviderError(429, "rate_limit_exceeded: tokens per minute")
        return ChatResult(
            generations=[
                ChatGeneration(
                    message=AIMessage(
                        content="recovered",
                        usage_metadata={"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
                    )
                )
            ]
        )

    monkeypatch.setattr(rate_limit, "get_groq_limiter", lambda model: limiter)
    monkeypatch.setattr(rate_limit, "groq_recovery_attempts", lambda: 1)
    monkeypatch.setattr(rate_limit, "groq_transient_retry_delay", lambda error: 60.0)
    monkeypatch.setattr(openai_client.time, "sleep", sleeps.append)
    monkeypatch.setattr(NormalizedChatOpenAI, "_generate", fake_generate)

    client = GroqRateLimitedChatOpenAI(model="openai/gpt-oss-20b", api_key="test")
    result = client._generate([HumanMessage(content="test")])

    assert result.generations[0].message.content == "recovered"
    assert len(calls) == 2
    assert sleeps == [60.0]
    assert len(limiter.acquisitions) == 2
    assert limiter.actual_tokens == 12


def test_groq_client_does_not_retry_oversized_413(monkeypatch):
    limiter = _Limiter()
    calls = []

    def fake_generate(self, messages, stop=None, run_manager=None, **kwargs):
        calls.append(messages)
        raise _ProviderError(413, "rate_limit_exceeded: request too large")

    monkeypatch.setattr(rate_limit, "get_groq_limiter", lambda model: limiter)
    monkeypatch.setattr(rate_limit, "groq_recovery_attempts", lambda: 1)
    monkeypatch.setattr(NormalizedChatOpenAI, "_generate", fake_generate)

    client = GroqRateLimitedChatOpenAI(model="openai/gpt-oss-20b", api_key="test")
    with pytest.raises(_ProviderError, match="request too large"):
        client._generate([HumanMessage(content="test")])
    assert len(calls) == 1
