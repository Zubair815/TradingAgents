"""Tests for Forex Event & News Analyst Agent (Phase 8).

Validates:
- Economic calendar domain models (EconomicEvent, EventImpact, EventSurpriseDirection).
- Point-in-time (PIT) safety — masking future actuals to prevent lookahead leakage.
- Event risk regime evaluation, blackout window calculation, and spread warnings.
- Economic surprise momentum and pair directional bias.
- Agent tools: get_forex_economic_calendar, get_economic_event_risk, get_forex_currency_news, get_event_surprise_history.
- Deterministic report generator (generate_deterministic_forex_news_report).
- Structured Pydantic assessment schema (ForexNewsAssessment).
- LangGraph node factory (create_forex_news_analyst) execution with mock LLM.
- Dual report assignment (news_report and forex_news_report).
- Graph compilation and execution plan integration.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableLambda

from tests.forex_calendar_fixture import generate_institutional_economic_calendar
from tradingagents.agents import create_forex_news_analyst
from tradingagents.agents.analysts.forex_news import (
    ForexNewsAssessment,
    generate_deterministic_forex_news_report,
)
from tradingagents.agents.utils.agent_states import AgentState
from tradingagents.agents.utils.forex_news_tools import (
    FOREX_NEWS_TOOLS,
    get_economic_event_risk,
    get_event_surprise_history,
    get_forex_currency_news,
    get_forex_economic_calendar,
)
from tradingagents.forex.calendar import (
    EconomicEvent,
    EventImpact,
    EventRiskRegime,
    EventSurpriseDirection,
    TradingActionRecommendation,
    calculate_pair_event_bias,
    evaluate_event_risk_regime,
    format_economic_calendar_table,
    get_calendar_events_for_pair,
)
from tradingagents.graph.analyst_execution import (
    ANALYST_NODE_SPECS,
    build_analyst_execution_plan,
)
from tradingagents.graph.conditional_logic import ConditionalLogic

# ---------------------------------------------------------------------------
# 1. Economic Event Model & PIT Clamping Tests
# ---------------------------------------------------------------------------


class TestEconomicEventModel:
    """Test EconomicEvent properties and point-in-time behavior."""

    def test_surprise_calculation(self):
        ev = EconomicEvent(
            event_id="test_1",
            currency="USD",
            title="Non-Farm Payrolls",
            impact=EventImpact.HIGH,
            date="2026-03-06",
            time_utc="12:30",
            actual=215.0,
            forecast=190.0,
            previous=180.0,
            unit="K",
            higher_is_bullish=True,
        )
        assert ev.surprise == 25.0
        assert ev.surprise_direction == EventSurpriseDirection.HAWKISH

    def test_surprise_direction_unemployment_lower_is_hawkish(self):
        # For unemployment rate, lower actual is bullish/hawkish for the currency
        ev = EconomicEvent(
            event_id="test_2",
            currency="USD",
            title="Unemployment Rate",
            impact=EventImpact.HIGH,
            date="2026-03-06",
            time_utc="12:30",
            actual=3.7,
            forecast=3.9,
            previous=3.9,
            unit="%",
            higher_is_bullish=False,
        )
        assert ev.surprise == -0.2
        assert ev.surprise_direction == EventSurpriseDirection.HAWKISH

        # Higher actual unemployment is dovish
        ev_dovish = EconomicEvent(
            event_id="test_2b",
            currency="USD",
            title="Unemployment Rate",
            impact=EventImpact.HIGH,
            date="2026-03-06",
            time_utc="12:30",
            actual=4.1,
            forecast=3.9,
            previous=3.9,
            unit="%",
            higher_is_bullish=False,
        )
        assert ev_dovish.surprise_direction == EventSurpriseDirection.DOVISH

    def test_surprise_direction_inline_and_neutral(self):
        ev_inline = EconomicEvent(
            event_id="test_3",
            currency="EUR",
            title="CPI",
            impact=EventImpact.HIGH,
            date="2026-03-17",
            actual=2.4,
            forecast=2.4,
            unit="%",
        )
        assert ev_inline.surprise == 0.0
        assert ev_inline.surprise_direction == EventSurpriseDirection.INLINE

        ev_neutral = EconomicEvent(
            event_id="test_4",
            currency="EUR",
            title="ECB Rate",
            impact=EventImpact.HIGH,
            date="2026-04-15",
            actual=None,
            forecast=3.25,
            unit="%",
        )
        assert ev_neutral.surprise is None
        assert ev_neutral.surprise_direction == EventSurpriseDirection.NEUTRAL

    def test_clamp_to_as_of_masks_future_events(self):
        ev = EconomicEvent(
            event_id="test_pit",
            published_at_utc=datetime(2026, 3, 15, 12, 30, tzinfo=timezone.utc),
            currency="USD",
            title="CPI",
            impact=EventImpact.HIGH,
            date="2026-03-15",
            time_utc="12:30",
            actual=3.2,
            forecast=3.0,
            previous=2.9,
            unit="%",
        )
        # As of 2026-03-10, this event is in the future
        clamped = ev.clamp_to_as_of("2026-03-10")
        assert clamped.actual is None
        assert clamped.forecast == 3.0
        assert clamped.previous == 2.9
        assert clamped.surprise is None

        # As of 2026-03-20, this event is in the past
        past = ev.clamp_to_as_of("2026-03-20")
        assert past.actual == 3.2
        assert past.surprise == 0.2


# ---------------------------------------------------------------------------
# 2. Institutional Calendar Generation & Query Tests
# ---------------------------------------------------------------------------


class TestCalendarGenerationAndFiltering:
    """Test calendar dataset generation, pair filtering, and formatting."""

    def test_institutional_calendar_coverage(self):
        events = generate_institutional_economic_calendar(2025, 2026)
        assert len(events) > 100

        # Check all G8 currencies are present
        currencies = {ev.currency for ev in events}
        expected = {"USD", "EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "NZD"}
        assert expected.issubset(currencies)

        # Check high impact events are present
        titles = {ev.title for ev in events}
        assert "Non-Farm Payrolls" in titles
        assert "Fed Interest Rate Decision" in titles
        assert "ECB Interest Rate Decision" in titles
        assert "BoE Official Bank Rate" in titles
        assert "BoJ Policy Rate Decision" in titles

    def test_get_calendar_events_for_pair_filtering(self):
        # Query EURUSD: should only return EUR and USD events
        events = get_calendar_events_for_pair(
            symbol="EURUSD",
            start_date="2026-03-01",
            end_date="2026-03-31",
            curr_date="2026-03-15",
        )
        assert len(events) > 0
        for ev in events:
            assert ev.currency in {"EUR", "USD"}
            assert "2026-03-01" <= ev.date <= "2026-03-31"

            # Future events (after 2026-03-15) must have actual masked
            if ev.date > "2026-03-15":
                assert ev.actual is None

    def test_format_economic_calendar_table(self):
        events = [
            EconomicEvent(
                event_id="e1",
                currency="USD",
                title="NFP",
                impact=EventImpact.HIGH,
                date="2026-03-06",
                time_utc="12:30",
                actual=220.0,
                forecast=200.0,
                previous=180.0,
                unit="K",
            ),
        ]
        table = format_economic_calendar_table(events)
        assert "| Date (UTC) |" in table
        assert "USD" in table
        assert "NFP" in table
        assert "220.0K" in table
        assert "Hawkish" in table


# ---------------------------------------------------------------------------
# 3. Event Risk Regime & Blackout Window Tests
# ---------------------------------------------------------------------------


class TestEventRiskRegime:
    """Test blackout detection and risk regime classifications."""

    def test_high_risk_and_blackout_on_release_day(self):
        # 2026-03-06 is the first Friday of March 2026 (NFP day for USD)
        risk = evaluate_event_risk_regime(
            symbol="EURUSD",
            curr_date="2026-03-06",
            lookahead_hours=48.0,
        )
        assert risk.risk_regime == EventRiskRegime.HIGH_RISK
        assert risk.blackout_active is True
        assert risk.recommendation == TradingActionRecommendation.AVOID_NEW_POSITIONS
        assert risk.next_high_impact_event is not None
        assert risk.hours_to_next_high_impact is not None

    def test_moderate_or_low_risk_when_clear(self):
        # Test a date with no high-impact release on that day
        risk = evaluate_event_risk_regime(
            symbol="EURUSD",
            curr_date="2026-03-01",
            lookahead_hours=24.0,
        )
        # NFP is on March 6, more than 24h away
        assert risk.blackout_active is False
        assert risk.recommendation in (
            TradingActionRecommendation.NORMAL_TRADING,
            TradingActionRecommendation.TIGHTEN_STOPS,
        )


# ---------------------------------------------------------------------------
# 4. Economic Surprise Momentum & Pair Bias Tests
# ---------------------------------------------------------------------------


class TestEconomicSurpriseMomentum:
    """Test surprise momentum calculations."""

    def test_calculate_pair_event_bias(self):
        bias = calculate_pair_event_bias(
            symbol="EURUSD",
            curr_date="2026-03-15",
            lookback_days=30,
        )
        assert bias.pair == "EURUSD"
        assert bias.base_currency == "EUR"
        assert bias.quote_currency == "USD"
        assert bias.directional_bias in {"BULLISH_BASE", "BEARISH_BASE", "NEUTRAL"}
        assert 0.0 <= bias.confidence <= 1.0
        assert len(bias.summary) > 0


# ---------------------------------------------------------------------------
# 5. Agent Tools Tests
# ---------------------------------------------------------------------------


class TestForexNewsTools:
    """Test LangChain tool execution and output formatting."""

    def test_forex_news_tools_list(self):
        assert len(FOREX_NEWS_TOOLS) == 4
        names = {t.name for t in FOREX_NEWS_TOOLS}
        assert names == {
            "get_forex_economic_calendar",
            "get_economic_event_risk",
            "get_forex_currency_news",
            "get_event_surprise_history",
        }

    def test_get_forex_economic_calendar_tool(self):
        res = get_forex_economic_calendar.invoke({
            "symbol": "EURUSD",
            "curr_date": "2026-03-10",
            "days_ahead": 7,
            "days_behind": 7,
        })
        assert "Economic Calendar: EURUSD" in res
        assert "Upcoming Scheduled Events" in res
        assert "Recent Economic Releases" in res

    def test_get_economic_event_risk_tool(self):
        res = get_economic_event_risk.invoke({
            "symbol": "EURUSD",
            "curr_date": "2026-03-06",
            "lookahead_hours": 48.0,
        })
        assert "Event Risk Regime Assessment: EURUSD" in res
        assert "HIGH RISK" in res
        assert "Blackout Window" in res

    def test_get_event_surprise_history_tool(self):
        res = get_event_surprise_history.invoke({
            "symbol": "EURUSD",
            "curr_date": "2026-03-15",
            "lookback_days": 30,
        })
        assert "Economic Surprise Momentum Analysis: EURUSD" in res
        assert "Surprise Momentum Scorecard" in res
        assert "Directional Bias" in res

    @patch("tradingagents.agents.utils.forex_news_tools.fetch_forex_news")
    def test_get_forex_currency_news_tool(self, mock_vendor):
        from tradingagents.dataflows.forex_news import ForexNewsArticle
        mock_vendor.return_value = [ForexNewsArticle(
            headline=title, publisher="Test publisher", source="Yahoo", url=f"https://example.com/{i}",
            published_at_utc="2026-03-14T10:00:00Z", retrieved_at_utc="2026-03-14T11:00:00Z",
            currencies=("USD", "EUR"), category="monetary_policy", relevance=1.0,
        ) for i, title in enumerate(("Fed Signals Caution", "ECB Holds Rates"))]
        res = get_forex_currency_news.invoke({
            "symbol": "EURUSD",
            "curr_date": "2026-03-15",
            "look_back_days": 5,
            "limit": 5,
        })
        assert "Forex Currency News: EURUSD" in res
        assert "Fed Signals Caution" in res
        assert "ECB Holds Rates" in res


# ---------------------------------------------------------------------------
# 6. Deterministic Report Generator Tests
# ---------------------------------------------------------------------------


class TestDeterministicForexNewsReport:
    """Test deterministic report generation with zero-LLM dependencies."""

    def test_generate_report_eurusd(self):
        report = generate_deterministic_forex_news_report("EURUSD", "2026-03-06")
        assert "# Forex Event & News Analysis Report: EURUSD" in report
        assert "## 1. Executive Summary & Event Bias" in report
        assert "## 2. Event Risk Regime & Blackout Advisory" in report
        assert "## 3. Scheduled High-Impact Economic Calendar" in report
        assert "## 4. Economic Surprise Momentum" in report
        assert "## 5. Event Catalyst Scorecard & Risk Factors" in report
        assert "Blackout Status" in report

    def test_generate_report_unknown_pair(self):
        report = generate_deterministic_forex_news_report("UNKNOWN", "2026-03-06")
        assert "Unknown Forex pair" in report


# ---------------------------------------------------------------------------
# 7. Pydantic Assessment Schema Tests
# ---------------------------------------------------------------------------


class TestForexNewsAssessmentSchema:
    """Test ForexNewsAssessment schema validation."""

    def test_valid_schema_instantiation(self):
        assessment = ForexNewsAssessment(
            pair="EURUSD",
            news_bias="BULLISH_BASE",
            confidence=0.75,
            base_currency="EUR",
            quote_currency="USD",
            risk_regime="HIGH_RISK",
            blackout_recommended=True,
            trading_recommendation="AVOID_NEW_POSITIONS",
            hours_to_next_high_impact=2.5,
            next_high_impact_event="Non-Farm Payrolls (USD)",
            upcoming_high_impact_events=["Non-Farm Payrolls (USD)"],
            recent_event_surprises=["Eurozone CPI (+0.2%)"],
            catalysts_favoring_base=["Strong Eurozone inflation beat"],
            catalysts_favoring_quote=[],
            spread_and_liquidity_warnings=["Expected 5x spread widening ahead of NFP"],
            summary_narrative="NFP release scheduled within 2.5 hours creates acute blackout conditions.",
        )
        assert assessment.pair == "EURUSD"
        assert assessment.blackout_recommended is True
        assert assessment.confidence == 0.75

    def test_invalid_confidence_raises(self):
        with pytest.raises(ValueError):
            ForexNewsAssessment(

                pair="EURUSD",
                news_bias="BULLISH_BASE",
                confidence=1.5,  # Out of [0, 1] range
                base_currency="EUR",
                quote_currency="USD",
                summary_narrative="Test",
            )


# ---------------------------------------------------------------------------
# 8. LangGraph Node Factory Tests
# ---------------------------------------------------------------------------

class TestForexNewsAnalystNode:
    """Test LangGraph node factory execution with mock LLM."""

    def test_node_generates_dual_reports(self):
        expected_report = "# Mock Forex News Report\n\n- All clear"
        ai_msg = AIMessage(content=expected_report, tool_calls=[])

        mock_llm = RunnableLambda(lambda x: ai_msg)
        mock_llm.bind_tools = lambda tools: mock_llm

        node = create_forex_news_analyst(mock_llm)

        state = {
            "company_of_interest": "EURUSD",
            "trade_date": "2026-03-06",
            "messages": [HumanMessage(content="Analyze EURUSD news")],
        }

        result = node(state)

        # Must write to both news_report and forex_news_report
        assert result["news_report"] == expected_report
        assert result["forex_news_report"] == expected_report
        assert len(result["messages"]) == 1

    def test_node_handles_tool_calls(self):
        tool_call_msg = AIMessage(
            content="",
            tool_calls=[{"name": "get_economic_event_risk", "args": {"symbol": "EURUSD", "curr_date": "2026-03-06"}, "id": "call_123"}],
        )

        mock_llm = RunnableLambda(lambda x: tool_call_msg)
        mock_llm.bind_tools = lambda tools: mock_llm

        node = create_forex_news_analyst(mock_llm)

        state = {
            "company_of_interest": "EURUSD",
            "trade_date": "2026-03-06",
            "messages": [HumanMessage(content="Analyze EURUSD news")],
        }

        result = node(state)
        # Empty string report while tool calls are pending
        assert result["news_report"] == ""
        assert result["forex_news_report"] == ""
        assert len(result["messages"][0].tool_calls) == 1

    def test_node_accepts_custom_tools(self):
        ai_msg = AIMessage(content="Final Report", tool_calls=[])
        mock_llm = RunnableLambda(lambda x: ai_msg)
        bound_tools = []
        mock_llm.bind_tools = lambda tools: (bound_tools.extend(tools), mock_llm)[1]

        custom_tools = [get_economic_event_risk]
        node = create_forex_news_analyst(mock_llm, tools=custom_tools)

        state = {
            "messages": [HumanMessage(content="Analyze EURUSD news")],
            "company_of_interest": "EURUSD",
            "trade_date": "2026-03-06",
        }
        result = node(state)
        assert bound_tools == custom_tools
        assert result["news_report"] == "Final Report"


# ---------------------------------------------------------------------------
# 9. Graph Orchestration & Plan Integration Tests
# ---------------------------------------------------------------------------


class TestGraphIntegration:
    """Test integration into LangGraph workflow and analyst plan."""

    def test_analyst_node_specs_includes_forex_news(self):
        assert "forex_news" in ANALYST_NODE_SPECS
        spec = ANALYST_NODE_SPECS["forex_news"]
        assert spec.agent_node == "Forex News Analyst"
        assert spec.tool_node == "tools_forex_news"
        assert spec.clear_node == "Msg Clear Forex News"
        assert spec.report_key == "news_report"

    def test_conditional_logic_should_continue_forex_news(self):
        logic = ConditionalLogic()

        # Tool calls present -> route to tool node
        msg_with_tools = MagicMock()
        msg_with_tools.tool_calls = [{"name": "foo"}]
        state_tools: AgentState = {"messages": [msg_with_tools]}  # type: ignore
        assert logic.should_continue_forex_news(state_tools) == "tools_forex_news"

        # No tool calls -> route to clear node
        msg_no_tools = MagicMock()
        msg_no_tools.tool_calls = []
        state_no_tools: AgentState = {"messages": [msg_no_tools]}  # type: ignore
        assert logic.should_continue_forex_news(state_no_tools) == "Msg Clear Forex News"

    def test_build_execution_plan_with_forex_news(self):
        plan = build_analyst_execution_plan(["forex_technical", "forex_macro", "forex_news"])
        assert len(plan.specs) == 3
        assert plan.specs[0].key == "forex_technical"
        assert plan.specs[1].key == "forex_macro"
        assert plan.specs[2].key == "forex_news"


@pytest.fixture(autouse=True)
def explicit_calendar_fixture(monkeypatch):
    from tests.forex_calendar_fixture import fixture_query
    monkeypatch.setattr("tradingagents.dataflows.trading_economics.TradingEconomicsCalendar.query", fixture_query)

