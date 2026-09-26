"""A truncated completion must never become a report or a final trade call."""

import json
from unittest.mock import MagicMock

import httpx
import pytest
from langchain_core.exceptions import OutputParserException
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda
from openai import APIConnectionError, APIStatusError, AuthenticationError

from tradingagents.agents.analysts.fundamentals_analyst import create_fundamentals_analyst
from tradingagents.agents.analysts.market_analyst import create_market_analyst
from tradingagents.agents.analysts.news_analyst import create_news_analyst
from tradingagents.agents.schemas import PortfolioDecision, render_pm_decision
from tradingagents.agents.utils.structured import bind_structured, invoke_structured_or_freetext
from tradingagents.graph.propagation import Propagator
from tradingagents.llm_clients.base_client import normalize_content
from tradingagents.llm_clients.response_validation import IncompleteResponseError, validate_response


@pytest.mark.parametrize("metadata", [
    {"finish_reason": "length"},
    {"finish_reason": "MAX_TOKENS"},
    {"stop_reason": "max_tokens"},
    {"stopReason": "max_tokens"},
    {"stop_reason": "model_context_window_exceeded"},
    {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}},
])
@pytest.mark.parametrize("content", ["", "The evidence points to"])
def test_provider_truncation_metadata_is_rejected(metadata, content):
    with pytest.raises(IncompleteResponseError, match="incomplete output"):
        validate_response(AIMessage(content, response_metadata=metadata))


def test_empty_completion_is_rejected_but_real_tool_request_is_allowed():
    with pytest.raises(IncompleteResponseError, match="no usable text"):
        normalize_content(AIMessage(""))
    request = AIMessage("", tool_calls=[{"name": "get_stock_data", "args": {}, "id": "1"}])
    assert normalize_content(request) is request


def test_reasoning_only_output_is_not_usable_prose():
    with pytest.raises(IncompleteResponseError, match="no usable text"):
        normalize_content(AIMessage([{"type": "reasoning", "summary": []}]))


@pytest.mark.parametrize("factory", [create_market_analyst, create_news_analyst, create_fundamentals_analyst])
def test_tool_bound_analyst_does_not_accept_truncated_report(factory):
    llm = MagicMock()
    llm.bind_tools.return_value = RunnableLambda(
        lambda _: AIMessage("Partial report", response_metadata={"finish_reason": "length"})
    )
    state = Propagator().create_initial_state("AAPL", "2026-09-24")
    with pytest.raises(IncompleteResponseError):
        factory(llm)(state)
    # The guard must sit after the bound runnable; plain invoke was never used.
    llm.invoke.assert_not_called()


def test_real_openai_bound_runnable_rejects_truncation_without_network():
    from tradingagents.llm_clients.openai_client import NormalizedChatOpenAI

    def serve(request):
        return httpx.Response(200, json={
            "id": "completion-test", "object": "chat.completion", "created": 0,
            "model": "test-model", "choices": [{"index": 0, "finish_reason": "length",
                "message": {"role": "assistant", "content": "Partial report"}}],
        })

    with httpx.Client(transport=httpx.MockTransport(serve)) as http_client:
        llm = NormalizedChatOpenAI(
            model="test-model", api_key="test-key", base_url="http://mock.invalid/v1",
            use_responses_api=False, http_client=http_client, max_retries=0,
        )
        with pytest.raises(IncompleteResponseError):
            create_market_analyst(llm)(Propagator().create_initial_state("AAPL", "2026-09-24"))


def test_real_structured_output_cannot_hide_a_truncated_completion():
    from tradingagents.llm_clients.openai_client import NormalizedChatOpenAI

    requests = []

    def serve(request):
        requests.append(request)
        return httpx.Response(200, json={
            "id": "completion-test", "object": "chat.completion", "created": 0,
            "model": "test-model", "choices": [{"index": 0, "finish_reason": "length",
                "message": {"role": "assistant", "content": None, "tool_calls": [{
                    "id": "call-test", "type": "function", "function": {
                        "name": "PortfolioDecision", "arguments": json.dumps(_decision().model_dump())
                    },
                }]}}],
        })

    with httpx.Client(transport=httpx.MockTransport(serve)) as http_client:
        llm = NormalizedChatOpenAI(
            model="test-model", api_key="test-key", base_url="http://mock.invalid/v1",
            use_responses_api=False, http_client=http_client, max_retries=0,
        )
        structured = bind_structured(llm, PortfolioDecision, "PM")
        with pytest.raises(IncompleteResponseError):
            invoke_structured_or_freetext(structured, llm, "prompt", render_pm_decision, "PM")
    assert len(requests) == 1  # No free-text fallback after truncation.


def _decision():
    return PortfolioDecision(rating="Buy", executive_summary="Summary", investment_thesis="Thesis")


def test_structured_binding_preserves_raw_response_metadata():
    llm = MagicMock()
    bind_structured(llm, PortfolioDecision, "Portfolio Manager")
    llm.with_structured_output.assert_called_once_with(PortfolioDecision, include_raw=True)


def test_valid_parsed_result_does_not_override_truncated_raw_completion():
    structured, plain = MagicMock(), MagicMock()
    structured.invoke.return_value = {
        "raw": AIMessage("", response_metadata={"finish_reason": "length"}),
        "parsed": _decision(), "parsing_error": None,
    }
    with pytest.raises(IncompleteResponseError):
        invoke_structured_or_freetext(structured, plain, "prompt", render_pm_decision, "PM")
    plain.invoke.assert_not_called()


def test_complete_structured_result_is_rendered():
    structured, plain = MagicMock(), MagicMock()
    structured.invoke.return_value = {
        "raw": AIMessage("", tool_calls=[{"name": "PortfolioDecision", "args": {}, "id": "1"}]),
        "parsed": _decision(), "parsing_error": None,
    }
    assert "**Rating**: Buy" in invoke_structured_or_freetext(
        structured, plain, "prompt", render_pm_decision, "PM"
    )
    plain.invoke.assert_not_called()


def test_malformed_structured_output_can_fall_back_once():
    structured, plain = MagicMock(), MagicMock()
    structured.invoke.return_value = {
        "raw": AIMessage("not JSON", response_metadata={"finish_reason": "stop"}),
        "parsed": None, "parsing_error": OutputParserException("bad JSON"),
    }
    plain.invoke.return_value = AIMessage("**Rating**: Hold")
    assert invoke_structured_or_freetext(structured, plain, "prompt", render_pm_decision, "PM") == "**Rating**: Hold"
    plain.invoke.assert_called_once()


@pytest.mark.parametrize("kind", ["auth", "credits", "network", "other"])
def test_provider_request_failure_does_not_trigger_freetext_request(kind):
    request = httpx.Request("POST", "http://mock.invalid/v1/chat/completions")
    if kind == "network":
        error = APIConnectionError(request=request)
    elif kind == "other":
        error = ValueError("provider configuration is invalid")
    else:
        status = 401 if kind == "auth" else 402
        response = httpx.Response(status, request=request)
        cls = AuthenticationError if kind == "auth" else APIStatusError
        error = cls("provider rejected request", response=response, body={})
    structured, plain = MagicMock(), MagicMock()
    structured.invoke.side_effect = error
    with pytest.raises(type(error)) as raised:
        invoke_structured_or_freetext(structured, plain, "prompt", render_pm_decision, "PM")
    assert raised.value is error
    plain.invoke.assert_not_called()


def test_freetext_fallback_is_also_checked_for_truncation():
    plain = MagicMock()
    plain.invoke.return_value = AIMessage("**Rating**: Buy", response_metadata={"finish_reason": "length"})
    with pytest.raises(IncompleteResponseError):
        invoke_structured_or_freetext(None, plain, "prompt", render_pm_decision, "PM")


@pytest.mark.parametrize("module,factory", [
    ("researchers.bull_researcher", "create_bull_researcher"),
    ("researchers.bear_researcher", "create_bear_researcher"),
    ("risk_mgmt.aggressive_debator", "create_aggressive_debator"),
    ("risk_mgmt.conservative_debator", "create_conservative_debator"),
    ("risk_mgmt.neutral_debator", "create_neutral_debator"),
])
def test_debate_agents_reject_truncated_evidence(module, factory):
    from importlib import import_module

    llm = MagicMock()
    llm.invoke.return_value = AIMessage("Partial argument", response_metadata={"finish_reason": "length"})
    state = Propagator().create_initial_state("AAPL", "2026-09-24")
    state.update(investment_plan="Research plan", trader_investment_plan="Trade plan")
    node = getattr(import_module(f"tradingagents.agents.{module}"), factory)(llm)
    with pytest.raises(IncompleteResponseError):
        node(state)
