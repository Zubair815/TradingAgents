"""Tests for Forex Technical Analyst Agent (Phase 6).

Validates:
- Structured schema (ForexTechnicalAssessment) validation and serialization.
- Pure deterministic report generation (generate_deterministic_forex_technical_report).
- LangGraph node factory (create_forex_technical_analyst) execution with mock LLM.
- State updates: setting both market_report and forex_technical_report.
- Integration with GraphSetup and AnalystExecutionPlan.
- CLI asset detection and analyst filtering for Forex instruments.
"""

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest
from langchain_core.messages import AIMessage, HumanMessage

from cli.models import AnalystType, AssetType as CliAssetType
from cli.utils import detect_asset_type, filter_analysts_for_asset_type
from tradingagents.agents import create_forex_technical_analyst
from tradingagents.agents.analysts.forex_technical import (
    ForexTechnicalAssessment,
    generate_deterministic_forex_technical_report,
)
from tradingagents.dataflows.forex_data import MultiTimeframeData
from tradingagents.forex.domain import Timeframe, get_forex_pair
from tradingagents.graph.analyst_execution import ANALYST_NODE_SPECS, build_analyst_execution_plan

# ---------------------------------------------------------------------------
# Helpers & Fixtures
# ---------------------------------------------------------------------------


def make_candles_df(n: int = 40, trend: str = "bullish", start_price: float = 1.0800) -> pd.DataFrame:
    """Create OHLCV DataFrame with clean trend structure."""
    timestamps = [
        datetime(2026, 3, 10, tzinfo=timezone.utc) + timedelta(hours=i)
        for i in range(n)
    ]
    step = 0.0005 if trend == "bullish" else -0.0005
    closes = [start_price + i * step for i in range(n)]
    opens = [c - (step * 0.5) for c in closes]
    highs = [max(o, c) + 0.0005 for o, c in zip(opens, closes, strict=True)]
    lows = [min(o, c) - 0.0005 for o, c in zip(opens, closes, strict=True)]

    volumes = [1000 + i * 10 for i in range(n)]

    return pd.DataFrame({
        "Date": timestamps,
        "Open": opens,
        "High": highs,
        "Low": lows,
        "Close": closes,
        "Volume": volumes,
    })


def make_test_bundle(symbol: str = "EURUSD", trend: str = "bullish") -> MultiTimeframeData:
    """Create multi-timeframe bundle for testing."""
    pair = get_forex_pair(symbol)
    start_p = 1.0800 if symbol == "EURUSD" else 150.00
    return MultiTimeframeData(
        symbol=symbol,
        pair=pair,
        candles={
            Timeframe.H4: make_candles_df(35, trend, start_p),
            Timeframe.H1: make_candles_df(30, trend, start_p + 0.0020),
            Timeframe.M15: make_candles_df(25, trend, start_p + 0.0030),
        },
        as_of=datetime(2026, 3, 10, 14, 0, tzinfo=timezone.utc),  # London / NY overlap
    )


# ---------------------------------------------------------------------------
# 1. Pydantic Assessment Schema Tests
# ---------------------------------------------------------------------------


class TestForexTechnicalAssessmentSchema:
    def test_valid_assessment(self):
        assessment = ForexTechnicalAssessment(
            pair="EURUSD",
            bias="LONG",
            confidence=0.85,
            market_regime="TRENDING_BULLISH",
            macro_trend="BULLISH (HH/HL above 200 EMA on H4)",
            tactical_structure="Bullish BOS on H1, pullback into M15 FVG",
            nearest_support=1.0820,
            nearest_resistance=1.0920,
            support_distance_pips=25.0,
            resistance_distance_pips=75.0,
            atr_14_pips=55.0,
            spread_atr_ratio=0.02,
            is_cost_favorable=True,
            confluence_factors=["H4 and H1 EMA stacks aligned", "ADX > 30"],
            warning_factors=["Approaching key daily resistance"],
            invalidation_level=1.0790,
            summary_narrative="Strong institutional bullish structure in play.",
        )

        assert assessment.pair == "EURUSD"
        assert assessment.bias == "LONG"
        assert assessment.confidence == 0.85
        assert assessment.is_cost_favorable is True
        assert len(assessment.confluence_factors) == 2

    def test_invalid_confidence_raises(self):
        with pytest.raises(ValueError):
            ForexTechnicalAssessment(

                pair="EURUSD",
                bias="LONG",
                confidence=1.5,  # Must be <= 1.0
                market_regime="TRENDING",
                macro_trend="BULLISH",
                tactical_structure="BULLISH",
                summary_narrative="Test",
            )


# ---------------------------------------------------------------------------
# 2. Deterministic Technical Report Tests
# ---------------------------------------------------------------------------


class TestDeterministicReportGenerator:
    def test_generate_report_eurusd(self):
        bundle = make_test_bundle("EURUSD", "bullish")
        report = generate_deterministic_forex_technical_report("EURUSD", bundle, spread_pips=1.2)

        assert "# Forex Technical Analysis Report: EURUSD" in report
        assert "Technical Bias" in report
        assert "Active Trading Sessions" in report
        assert "ATR(14)" in report
        assert "Multi-Timeframe Market Structure Matrix" in report
        assert "Multi-Timeframe Technical Indicator Matrix" in report
        assert "| **H4** |" in report
        assert "| **H1** |" in report

    def test_generate_report_usdjpy(self):
        bundle = make_test_bundle("USDJPY", "bearish")
        report = generate_deterministic_forex_technical_report("USDJPY", bundle, spread_pips=1.5)

        assert "# Forex Technical Analysis Report: USDJPY" in report
        assert "Technical Bias" in report
        assert "ATR(14)" in report


# ---------------------------------------------------------------------------
# 3. LangGraph Node Factory Tests
# ---------------------------------------------------------------------------


class TestForexTechnicalAnalystNode:
    def test_node_generates_report_when_no_tool_calls(self):
        from langchain_core.runnables import RunnableLambda

        expected_text = "# Forex Technical Analysis Report: EURUSD\nDirection: LONG"
        ai_msg = AIMessage(content=expected_text, tool_calls=[])

        mock_llm = RunnableLambda(lambda x: ai_msg)
        mock_llm.bind_tools = lambda tools: mock_llm

        node = create_forex_technical_analyst(mock_llm)

        state = {
            "messages": [HumanMessage(content="Analyze EURUSD technicals")],
            "company_of_interest": "EURUSD",
            "trade_date": "2026-03-10",
            "instrument_context": "The instrument to analyze is EURUSD.",
        }

        result = node(state)

        assert "messages" in result
        assert result["market_report"] == expected_text
        assert result["forex_technical_report"] == expected_text

    def test_node_with_tool_call(self):
        from langchain_core.runnables import RunnableLambda

        tool_call_msg = AIMessage(
            content="",
            tool_calls=[{"name": "get_forex_indicators_tool", "args": {"symbol": "EURUSD"}, "id": "call_123"}],
        )

        mock_llm = RunnableLambda(lambda x: tool_call_msg)
        mock_llm.bind_tools = lambda tools: mock_llm

        node = create_forex_technical_analyst(mock_llm)

        state = {
            "messages": [HumanMessage(content="Analyze EURUSD technicals")],
            "company_of_interest": "EURUSD",
            "trade_date": "2026-03-10",
        }

        result = node(state)

        # In intermediate tool calls, report remains empty until final answer
        assert result["market_report"] == ""
        assert result["forex_technical_report"] == ""
        assert len(result["messages"][0].tool_calls) == 1


# ---------------------------------------------------------------------------
# 4. Graph Execution Plan Integration Tests
# ---------------------------------------------------------------------------


class TestGraphIntegration:
    def test_analyst_node_specs_includes_forex_technical(self):
        assert "forex_technical" in ANALYST_NODE_SPECS
        spec = ANALYST_NODE_SPECS["forex_technical"]
        assert spec.agent_node == "Forex Technical Analyst"
        assert spec.clear_node == "Msg Clear Forex Technical"
        assert spec.tool_node == "tools_forex_technical"
        assert spec.report_key == "market_report"

    def test_build_analyst_execution_plan(self):
        plan = build_analyst_execution_plan(["forex_technical", "news"])
        assert len(plan.specs) == 2
        assert plan.specs[0].key == "forex_technical"
        assert plan.specs[1].key == "news"


# ---------------------------------------------------------------------------
# 5. CLI Detection and Filtering Tests
# ---------------------------------------------------------------------------


class TestCliAssetModeForex:
    def test_detect_asset_type_forex(self):
        assert detect_asset_type("EURUSD") == CliAssetType.FOREX
        assert detect_asset_type("USDJPY") == CliAssetType.FOREX
        assert detect_asset_type("GBPUSD") == CliAssetType.FOREX
        assert detect_asset_type("EURUSD=X") == CliAssetType.FOREX

    def test_filter_analysts_for_forex(self):
        analysts = [
            AnalystType.MARKET,
            AnalystType.SOCIAL,
            AnalystType.NEWS,
            AnalystType.FUNDAMENTALS,
        ]
        filtered = filter_analysts_for_asset_type(analysts, CliAssetType.FOREX)
        # Fundamentals analyst should be excluded for Forex currency pairs
        assert AnalystType.FUNDAMENTALS not in filtered
        assert AnalystType.MARKET in filtered
        assert AnalystType.NEWS in filtered
