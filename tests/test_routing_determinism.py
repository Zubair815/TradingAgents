"""Tests for deterministic debate routing independent of LLM prose."""

from unittest.mock import MagicMock

from tradingagents.agents.researchers.bear_researcher import create_bear_researcher
from tradingagents.agents.researchers.bull_researcher import create_bull_researcher
from tradingagents.graph.conditional_logic import ConditionalLogic


def test_investment_debate_routing_uses_last_speaker():
    cl = ConditionalLogic(max_debate_rounds=2)

    # Bull spoke last, but response is translated/non-English
    state_after_bull = {
        "investment_debate_state": {
            "count": 1,
            "current_response": "多头分析师: 强烈看涨",  # Chinese prose without 'Bull'
            "last_speaker": "bull",
        }
    }
    assert cl.should_continue_debate(state_after_bull) == "Bear Researcher"

    # Bear spoke last, response starts with whatever
    state_after_bear = {
        "investment_debate_state": {
            "count": 2,
            "current_response": "Arbitrary opening phrase from bear",
            "last_speaker": "bear",
        }
    }
    assert cl.should_continue_debate(state_after_bear) == "Bull Researcher"

    # Reaches max rounds -> Research Manager
    state_done = {
        "investment_debate_state": {
            "count": 4,
            "current_response": "Any text",
            "last_speaker": "bear",
        }
    }
    assert cl.should_continue_debate(state_done) == "Research Manager"


def test_investment_debate_backward_compat_fallback():
    cl = ConditionalLogic(max_debate_rounds=2)

    # Legacy state without last_speaker
    legacy_bull = {
        "investment_debate_state": {
            "count": 1,
            "current_response": "Bull Analyst: strong buy",
        }
    }
    assert cl.should_continue_debate(legacy_bull) == "Bear Researcher"

    legacy_bear = {
        "investment_debate_state": {
            "count": 2,
            "current_response": "Bear Analyst: strong sell",
        }
    }
    assert cl.should_continue_debate(legacy_bear) == "Bull Researcher"


def test_risk_analysis_routing_determinism():
    cl = ConditionalLogic(max_risk_discuss_rounds=1)

    state_agg = {
        "risk_debate_state": {
            "count": 1,
            "latest_speaker": "Aggressive",
        }
    }
    assert cl.should_continue_risk_analysis(state_agg) == "Conservative Analyst"

    state_cons = {
        "risk_debate_state": {
            "count": 2,
            "latest_speaker": "Conservative",
        }
    }
    assert cl.should_continue_risk_analysis(state_cons) == "Neutral Analyst"

    state_done = {
        "risk_debate_state": {
            "count": 3,
            "latest_speaker": "Neutral",
        }
    }
    assert cl.should_continue_risk_analysis(state_done) == "Portfolio Manager"


def test_bull_and_bear_researchers_set_last_speaker():
    mock_llm = MagicMock()
    mock_llm.invoke.return_value = MagicMock(content="Argument text")

    bull_fn = create_bull_researcher(mock_llm)
    bear_fn = create_bear_researcher(mock_llm)

    state = {
        "company_of_interest": "AAPL",
        "asset_type": "stock",
        "trade_date": "2026-01-01",
        "market_report": "market",
        "sentiment_report": "sentiment",
        "news_report": "news",
        "fundamentals_report": "fundamentals",
        "investment_debate_state": {
            "history": "",
            "bull_history": "",
            "bear_history": "",
            "current_response": "",
            "count": 0,
        },
    }

    res_bull = bull_fn(state)
    assert res_bull["investment_debate_state"]["last_speaker"] == "bull"
    assert res_bull["investment_debate_state"]["count"] == 1

    # Now pass to bear
    state["investment_debate_state"] = res_bull["investment_debate_state"]
    res_bear = bear_fn(state)
    assert res_bear["investment_debate_state"]["last_speaker"] == "bear"
    assert res_bear["investment_debate_state"]["count"] == 2
