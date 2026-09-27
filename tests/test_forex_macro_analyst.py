"""Tests for Forex Currency Macro Analyst Agent (Phase 7).

Validates:
- Structured schema (ForexMacroAssessment) validation and serialization.
- Pure deterministic report generation (generate_deterministic_forex_macro_report).
- Forex macro tools: get_central_bank_rates, get_rate_differential,
  get_risk_regime_indicators, get_treasury_yield_curve.
- LangGraph node factory (create_forex_macro_analyst) execution with mock LLM.
- State updates: setting both fundamentals_report and forex_macro_report.
- Integration with GraphSetup, ConditionalLogic, AnalystExecutionPlan, and Propagator.
- CLI integration with AnalystType.FOREX_MACRO.
"""

from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableLambda

from cli.models import AnalystType
from tradingagents.agents import create_forex_macro_analyst
from tradingagents.agents.analysts.forex_macro import (
    FOREX_MACRO_TOOLS,
    ForexMacroAssessment,
    generate_deterministic_forex_macro_report,
)
from tradingagents.agents.utils.agent_states import AgentState
from tradingagents.agents.utils.forex_macro_tools import (
    _extract_latest_value,
    get_central_bank_rates,
    get_rate_differential,
    get_risk_regime_indicators,
    get_treasury_yield_curve,
)
from tradingagents.graph.analyst_execution import (
    ANALYST_NODE_SPECS,
    build_analyst_execution_plan,
)
from tradingagents.graph.conditional_logic import ConditionalLogic
from tradingagents.graph.propagation import Propagator
from tradingagents.graph.setup import GraphSetup

# ---------------------------------------------------------------------------
# 1. Pydantic Assessment Schema Tests
# ---------------------------------------------------------------------------


class TestForexMacroAssessmentSchema:
    def test_valid_assessment(self):
        assessment = ForexMacroAssessment(
            pair="EURUSD",
            macro_bias="BULLISH_BASE",
            confidence=0.75,
            base_currency="EUR",
            quote_currency="USD",
            base_rate=3.75,
            quote_rate=5.25,
            rate_differential=-1.50,
            rate_differential_direction="NEGATIVE",
            vix_level=13.5,
            risk_regime="RISK_ON",
            dxy_level=102.4,
            yield_curve_spread=0.25,
            yield_curve_shape="NORMAL",
            bullish_factors=["Risk-on environment favors high-beta flows"],
            bearish_factors=["Negative rate differential (-1.50%) favors USD"],
            risk_factors=["Upcoming FOMC meeting on Wednesday"],
            summary_narrative="Mixed macro picture with carry favoring USD but risk appetite supporting EUR.",
        )
        data = assessment.model_dump()
        assert data["pair"] == "EURUSD"
        assert data["macro_bias"] == "BULLISH_BASE"
        assert data["confidence"] == 0.75
        assert data["base_rate"] == 3.75
        assert data["quote_rate"] == 5.25
        assert data["rate_differential"] == -1.50
        assert data["risk_regime"] == "RISK_ON"
        assert len(data["bullish_factors"]) == 1
        assert len(data["bearish_factors"]) == 1

    def test_invalid_confidence_raises(self):
        with pytest.raises(ValueError):
            ForexMacroAssessment(
                pair="EURUSD",
                macro_bias="NEUTRAL",
                confidence=1.5,  # Must be <= 1.0
                base_currency="EUR",
                quote_currency="USD",
                summary_narrative="Test",
            )

    def test_negative_confidence_raises(self):
        with pytest.raises(ValueError):
            ForexMacroAssessment(
                pair="EURUSD",
                macro_bias="NEUTRAL",
                confidence=-0.1,  # Must be >= 0.0
                base_currency="EUR",
                quote_currency="USD",
                summary_narrative="Test",
            )



# ---------------------------------------------------------------------------
# 2. Macro Data Tools Tests
# ---------------------------------------------------------------------------


class TestForexMacroTools:
    def test_extract_latest_value(self):
        report = (
            "## FRED: Federal Funds Effective Rate (FEDFUNDS)\n"
            "- Window: 2025-03-10 to 2026-03-10\n"
            "\n**Latest:** 4.50 (2026-03-01) | **Change over window:** -0.75\n"
        )
        assert _extract_latest_value(report) == 4.50

        # With commas
        report_with_comma = "**Latest:** 1,234.56 (2026-03-01)"
        assert _extract_latest_value(report_with_comma) == 1234.56

        # Missing
        assert _extract_latest_value("No data available") is None

    @patch("tradingagents.agents.utils.forex_macro_tools._fetch_macro_safe")
    def test_get_central_bank_rates_eurusd(self, mock_fetch):
        mock_fetch.side_effect = lambda series, date, look_back_days=365: (
            f"## FRED: {series}\n**Latest:** 3.75 (2026-03-01)"
            if series == "ECBDFR"
            else f"## FRED: {series}\n**Latest:** 4.50 (2026-03-01)"
        )
        result = get_central_bank_rates.invoke({"symbol": "EURUSD", "curr_date": "2026-03-10"})
        assert "## Central Bank Policy Rates: EURUSD" in result
        assert "Base Currency: EUR" in result
        assert "Quote Currency: USD" in result
        assert "ECBDFR" in result
        assert "FEDFUNDS" in result

    def test_get_central_bank_rates_unknown_symbol(self):
        result = get_central_bank_rates.invoke({"symbol": "UNKNOWN", "curr_date": "2026-03-10"})
        assert "ERROR: Unknown Forex pair" in result

    @patch("tradingagents.agents.utils.forex_macro_tools._fetch_macro_safe")
    def test_get_rate_differential_positive(self, mock_fetch):
        # Base=5.0%, Quote=3.0% -> Diff = +2.00%
        def fake_fetch(series, date, look_back_days=180):
            if series == "FEDFUNDS":
                return "**Latest:** 5.00 (2026-03-01)"
            return "**Latest:** 3.00 (2026-03-01)"

        mock_fetch.side_effect = fake_fetch
        result = get_rate_differential.invoke({"symbol": "USDJPY", "curr_date": "2026-03-10"})
        assert "## Interest Rate Differential: USDJPY" in result
        assert "+2.00%" in result
        assert "POSITIVE (favors base)" in result
        assert "USD" in result

    @patch("tradingagents.agents.utils.forex_macro_tools._fetch_macro_safe")
    def test_get_risk_regime_indicators(self, mock_fetch):
        def fake_fetch(series, date, look_back_days=90):
            if series == "vix":
                return "**Latest:** 12.5 (2026-03-10)"
            return "**Latest:** 103.20 (2026-03-10)"

        mock_fetch.side_effect = fake_fetch
        result = get_risk_regime_indicators.invoke({"curr_date": "2026-03-10"})
        assert "## Macro Risk Regime Assessment" in result
        assert "12.5" in result
        assert "RISK_ON" in result
        assert "103.20" in result

    @patch("tradingagents.agents.utils.forex_macro_tools._fetch_macro_safe")
    def test_get_treasury_yield_curve_inverted(self, mock_fetch):
        def fake_fetch(series, date, look_back_days=180):
            mapping = {
                "yield_curve": "**Latest:** -0.35 (2026-03-10)",
                "2y_treasury": "**Latest:** 4.60 (2026-03-10)",
                "10y_treasury": "**Latest:** 4.25 (2026-03-10)",
            }
            return mapping.get(series, "**Latest:** 4.50 (2026-03-10)")


        mock_fetch.side_effect = fake_fetch
        result = get_treasury_yield_curve.invoke({"curr_date": "2026-03-10"})
        assert "## US Treasury Yield Curve" in result
        assert "INVERTED" in result
        assert "-0.35%" in result


# ---------------------------------------------------------------------------
# 3. Deterministic Macro Report Generator Tests
# ---------------------------------------------------------------------------


class TestDeterministicReportGenerator:
    @patch("tradingagents.agents.analysts.forex_macro._fetch_macro_safe")
    def test_generate_report_eurusd_with_data(self, mock_fetch):
        def fake_fetch(indicator, curr_date, look_back_days=365):
            mapping = {
                "ECBDFR": "**Latest:** 3.75 (2026-03-01)",
                "FEDFUNDS": "**Latest:** 5.00 (2026-03-01)",
                "vix": "**Latest:** 14.0 (2026-03-01)",
                "dollar_index": "**Latest:** 104.50 (2026-03-01)",
                "yield_curve": "**Latest:** 0.20 (2026-03-01)",
                "10y_treasury": "**Latest:** 4.30 (2026-03-01)",
            }
            return mapping.get(indicator, "N/A")


        mock_fetch.side_effect = fake_fetch
        report = generate_deterministic_forex_macro_report("EURUSD", "2026-03-10")

        assert "# Forex Macro Analysis Report: EURUSD" in report
        assert "## 1. Central Bank Policy & Rate Differential" in report
        assert "## 2. Risk Regime Assessment" in report
        assert "## 3. Yield Curve & Monetary Outlook" in report
        assert "## 4. Executive Summary & Macro Bias" in report
        assert "## 5. Macro Factor Scorecard" in report
        assert "-1.25%" in report
        assert "Rate Differential" in report

    def test_generate_report_unknown_pair(self):
        report = generate_deterministic_forex_macro_report("UNKNOWN", "2026-03-10")
        assert "⚠️ Unknown Forex pair" in report


# ---------------------------------------------------------------------------
# 4. LangGraph Node Factory Tests
# ---------------------------------------------------------------------------


class TestForexMacroAnalystNode:
    def test_node_generates_report_when_no_tool_calls(self):
        expected_text = "# Forex Macro Analysis Report: EURUSD\nMacro Bias: BULLISH_BASE"
        ai_msg = AIMessage(content=expected_text, tool_calls=[])

        mock_llm = RunnableLambda(lambda x: ai_msg)
        mock_llm.bind_tools = lambda tools: mock_llm

        node = create_forex_macro_analyst(mock_llm)

        state = {
            "messages": [HumanMessage(content="Analyze EURUSD macro factors")],
            "company_of_interest": "EURUSD",
            "trade_date": "2026-03-10",
            "instrument_context": "The instrument to analyze is EURUSD.",
        }

        result = node(state)

        assert "messages" in result
        assert result["fundamentals_report"] == expected_text
        assert result["forex_macro_report"] == expected_text

    def test_node_with_tool_call(self):
        tool_call_msg = AIMessage(
            content="",
            tool_calls=[{"name": "get_central_bank_rates", "args": {"symbol": "EURUSD"}, "id": "call_456"}],
        )

        mock_llm = RunnableLambda(lambda x: tool_call_msg)
        mock_llm.bind_tools = lambda tools: mock_llm

        node = create_forex_macro_analyst(mock_llm)

        state = {
            "messages": [HumanMessage(content="Analyze EURUSD macro")],
            "company_of_interest": "EURUSD",
            "trade_date": "2026-03-10",
        }

        result = node(state)

        # In intermediate tool calls, report remains empty
        assert result["fundamentals_report"] == ""
        assert result["forex_macro_report"] == ""
        assert len(result["messages"][0].tool_calls) == 1

    def test_node_accepts_custom_tools(self):
        ai_msg = AIMessage(content="Final Report", tool_calls=[])
        mock_llm = RunnableLambda(lambda x: ai_msg)
        bound_tools = []
        mock_llm.bind_tools = lambda tools: (bound_tools.extend(tools), mock_llm)[1]

        custom_tools = [get_central_bank_rates]
        node = create_forex_macro_analyst(mock_llm, tools=custom_tools)
        node({
            "messages": [HumanMessage(content="Analyze macro")],
            "company_of_interest": "EURUSD",
            "trade_date": "2026-03-10",
        })
        assert len(bound_tools) == 1
        assert bound_tools[0].name == "get_central_bank_rates"


# ---------------------------------------------------------------------------
# 5. Graph Integration & Workflow Tests
# ---------------------------------------------------------------------------


class TestGraphIntegration:
    def test_analyst_node_specs_includes_forex_macro(self):
        assert "forex_macro" in ANALYST_NODE_SPECS
        spec = ANALYST_NODE_SPECS["forex_macro"]
        assert spec.agent_node == "Forex Macro Analyst"
        assert spec.clear_node == "Msg Clear Forex Macro"
        assert spec.tool_node == "tools_forex_macro"
        assert spec.report_key == "fundamentals_report"

    def test_build_analyst_execution_plan(self):
        plan = build_analyst_execution_plan(["forex_technical", "forex_macro", "news"])
        assert len(plan.specs) == 3
        assert plan.specs[0].key == "forex_technical"
        assert plan.specs[1].key == "forex_macro"
        assert plan.specs[2].key == "news"

    def test_conditional_logic_forex_macro(self):
        logic = ConditionalLogic()

        # Tool calls present -> tools_forex_macro
        state_with_tools: AgentState = {
            "messages": [AIMessage(content="", tool_calls=[{"name": "foo", "args": {}, "id": "1"}])],
        }  # type: ignore
        assert logic.should_continue_forex_macro(state_with_tools) == "tools_forex_macro"

        # No tool calls -> Msg Clear Forex Macro
        state_no_tools: AgentState = {
            "messages": [AIMessage(content="Done", tool_calls=[])],
        }  # type: ignore
        assert logic.should_continue_forex_macro(state_no_tools) == "Msg Clear Forex Macro"

    def test_conditional_logic_forex_technical(self):
        logic = ConditionalLogic()

        state_with_tools: AgentState = {
            "messages": [AIMessage(content="", tool_calls=[{"name": "foo", "args": {}, "id": "1"}])],
        }  # type: ignore
        assert logic.should_continue_forex_technical(state_with_tools) == "tools_forex_technical"

        state_no_tools: AgentState = {
            "messages": [AIMessage(content="Done", tool_calls=[])],
        }  # type: ignore
        assert logic.should_continue_forex_technical(state_no_tools) == "Msg Clear Forex Technical"

    def test_propagator_initializes_forex_macro_report(self):
        propagator = Propagator()
        state = propagator.create_initial_state("EURUSD", "2026-03-10", asset_type="forex")
        assert "forex_macro_report" in state
        assert state["forex_macro_report"] == ""
        assert "forex_technical_report" in state
        assert state["forex_technical_report"] == ""

    def test_graph_setup_compiles_forex_macro(self):
        from unittest.mock import MagicMock

        from langgraph.prebuilt import ToolNode

        mock_llm = MagicMock()
        mock_llm.bind_tools = lambda tools: mock_llm

        tool_nodes = {
            "market": ToolNode([]),
            "social": ToolNode([]),
            "news": ToolNode([]),
            "fundamentals": ToolNode([]),
            "forex_technical": ToolNode([]),
            "forex_macro": ToolNode(FOREX_MACRO_TOOLS),
        }

        setup = GraphSetup(
            quick_thinking_llm=mock_llm,
            deep_thinking_llm=mock_llm,
            tool_nodes=tool_nodes,
            conditional_logic=ConditionalLogic(),
        )

        workflow = setup.setup_graph(selected_analysts=["forex_technical", "forex_macro"])
        compiled = workflow.compile()
        assert compiled is not None


# ---------------------------------------------------------------------------
# 6. CLI Integration Tests
# ---------------------------------------------------------------------------


class TestCliForexMacroIntegration:
    def test_analyst_type_forex_macro_exists(self):
        assert AnalystType.FOREX_MACRO.value == "forex_macro"
