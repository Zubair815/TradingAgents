"""Comprehensive Test Suite for Forex LangGraph Workflow Orchestration (Phase 13).

Validates:
- ForexGraphSetup compilation and node topology.
- Support for customizable and subset analyst configurations.
- Institutional Forex Trader agent node (create_forex_trader):
  - Structured output parsing.
  - JSON and markdown fallback parsing.
  - Geometry inversion & incomplete level risk safety (safe fallback to NO_TRADE).
- Deterministic Forex Risk Evaluator node:
  - Hard risk rule validation (geometry, R:R >= 1.0, news blackout, market open).
  - Deterministic volume sizing and margin validation.
  - Audit trail logging and reconciliation.
- Portfolio Manager node and SQLite ForexTradeJournal auto-persistence:
  - Proposals table auto-logging.
  - Approved and rejected proposals never create actual trade records.
  - Legacy automatic execution flags fail before journal writes.
- Primary ForexTradingAgentsGraph class:
  - End-to-end execution (.run() and .propagate()).
  - Per-node event streaming (.stream()).
  - Inspection helpers (get_last_proposal, get_last_risk_decision, get_last_sizing_result).
  - Directional signal extraction.
  - Report tree generation (save_reports).
  - Checkpointing support.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable, RunnableLambda
from langgraph.checkpoint.memory import MemorySaver

from tradingagents.agents import create_forex_trader
from tradingagents.agents.schemas import PortfolioRating, ResearchPlan
from tradingagents.agents.schemas_forex import (
    ForexAction,
    ForexRiskDecision,
    ForexRiskDecisionAction,
    ForexTraderProposal,
    OrderType,
    SetupType,
    render_forex_trader_proposal,
)
from tradingagents.agents.trader.forex_trader import parse_forex_proposal_from_text
from tradingagents.database.journal import ForexTradeJournal, ProposalStatus
from tradingagents.forex import (
    EventRiskAssessment,
    EventRiskRegime,
    ForexTradingAgentsGraph,
)
from tradingagents.forex.calendar import TradingActionRecommendation
from tradingagents.risk.engine import ForexRiskLimits
from tradingagents.risk.sizing import (
    ForexAccountProfile,
    PositionSizingResult,
)

# ---------------------------------------------------------------------------
# Robust Mock LLMs for LangGraph Runnables
# ---------------------------------------------------------------------------


class MockChatModel(Runnable):
    """LangChain-compatible mock chat model that implements Runnable protocol."""

    def __init__(self, response_msg: AIMessage | None = None, structured_response: Any = None):
        self.response_msg = response_msg or AIMessage(content="Mock LLM response", tool_calls=[])
        self.structured_response = structured_response

    def invoke(self, input: Any, config: Any = None) -> AIMessage:
        return self.response_msg

    def bind_tools(self, tools: Any, **kwargs: Any) -> MockChatModel:
        return self

    def with_structured_output(self, schema: Any, **kwargs: Any) -> Any:
        schema_name = getattr(schema, "__name__", str(schema))
        if "ResearchPlan" in schema_name:
            res = ResearchPlan(
                recommendation=PortfolioRating.BUY,
                rationale="Institutional trend alignment and momentum bias.",
                strategic_actions="Execute buy on pullback to key support.",
            )
            return RunnableLambda(lambda x: res)
        if "ForexTraderProposal" in schema_name and self.structured_response is not None:
            return RunnableLambda(lambda x: self.structured_response)
        if self.structured_response is not None:
            return RunnableLambda(lambda x: self.structured_response)

        return self


# ---------------------------------------------------------------------------
# 1. Graph Topology & Compilation Tests
# ---------------------------------------------------------------------------


class TestForexGraphTopology:
    """Test graph assembly, node registration, and execution plan compilation."""

    def test_default_graph_compilation(self):
        mock_llm = MockChatModel()
        graph = ForexTradingAgentsGraph(
            selected_analysts=("forex_technical", "forex_macro", "forex_news"),
            quick_thinking_llm=mock_llm,
            deep_thinking_llm=mock_llm,
            db_path=":memory:",
        )
        assert graph.graph is not None

        # Verify key nodes in the compiled workflow
        nodes = graph.workflow.nodes
        assert "Forex Technical Analyst" in nodes
        assert "Forex Macro Analyst" in nodes
        assert "Forex News Analyst" in nodes
        assert "Bull Researcher" in nodes
        assert "Bear Researcher" in nodes
        assert "Research Manager" in nodes
        assert "Forex Trader" in nodes
        assert "Forex Risk Evaluator" in nodes
        assert "Portfolio Manager" in nodes

    def test_subset_analysts_compilation(self):
        mock_llm = MockChatModel()
        graph = ForexTradingAgentsGraph(
            selected_analysts=("forex_technical", "forex_news"),
            quick_thinking_llm=mock_llm,
            deep_thinking_llm=mock_llm,
            db_path=":memory:",
        )
        assert graph.graph is not None
        nodes = graph.workflow.nodes
        assert "Forex Technical Analyst" in nodes
        assert "Forex News Analyst" in nodes
        assert "Forex Macro Analyst" not in nodes
        assert "Forex Trader" in nodes
        assert "Forex Risk Evaluator" in nodes

    def test_invalid_analyst_rejection(self):
        mock_llm = MockChatModel()
        with pytest.raises(ValueError, match="unknown analyst key"):
            ForexTradingAgentsGraph(
                selected_analysts=("forex_technical", "invalid_analyst_xyz"),
                quick_thinking_llm=mock_llm,
                deep_thinking_llm=mock_llm,
                db_path=":memory:",
            )


# ---------------------------------------------------------------------------
# 2. Institutional Forex Trader Agent Tests
# ---------------------------------------------------------------------------


class TestForexTraderAgent:
    """Test create_forex_trader and parse_forex_proposal_from_text."""

    def test_trader_structured_output_long(self):
        proposal = ForexTraderProposal(
            pair="EURUSD",
            action=ForexAction.LONG,
            order_type=OrderType.MARKET,
            setup_type=SetupType.TREND_CONTINUATION,
            timeframe="H1",
            entry_price=1.08500,
            stop_loss=1.08100,
            take_profit_1=1.09200,
            suggested_risk_percent=1.0,
            reasoning="H4 EMA trend alignment and bullish FVG retest.",
        )
        mock_llm = MockChatModel(structured_response=proposal)
        trader_node = create_forex_trader(mock_llm)

        state = {
            "company_of_interest": "EURUSD",
            "trade_date": "2026-03-04",
            "forex_technical_report": "Bullish structure above 1.0830",
            "forex_macro_report": "ECB hawkish stance",
            "forex_news_report": "No high-impact releases scheduled",
            "investment_plan": "Overweight EUR against USD",
        }

        result = trader_node(state)
        assert "forex_proposal" in result
        assert result["forex_proposal"]["pair"] == "EURUSD"
        assert result["forex_proposal"]["action"] == "LONG"
        assert result["forex_proposal"]["entry_price"] == 1.08500
        assert result["forex_proposal"]["stop_loss"] == 1.08100
        assert "FINAL FOREX PROPOSAL: **LONG**" in result["trader_investment_plan"]

    def test_trader_text_fallback_json_parsing(self):
        json_content = json.dumps({
            "pair": "GBPJPY",
            "action": "SHORT",
            "order_type": "MARKET",
            "setup_type": "BREAKOUT",
            "timeframe": "H4",
            "entry_price": 190.500,
            "stop_loss": 191.200,
            "take_profit_1": 189.000,
            "suggested_risk_percent": 1.5,
            "reasoning": "Break of daily support level.",
        })
        text_response = f"Here is my institutional proposal:\n```json\n{json_content}\n```"

        ai_msg = AIMessage(content=text_response, tool_calls=[])
        mock_plain = MockChatModel(response_msg=ai_msg)

        trader_node = create_forex_trader(mock_plain)
        state = {"company_of_interest": "GBPJPY", "trade_date": "2026-03-04"}

        result = trader_node(state)
        assert result["forex_proposal"]["pair"] == "GBPJPY"
        assert result["forex_proposal"]["action"] == "SHORT"
        assert result["forex_proposal"]["entry_price"] == 190.500
        assert result["forex_proposal"]["stop_loss"] == 191.200
        assert result["forex_proposal"]["take_profit_1"] == 189.000

    def test_trader_text_fallback_markdown_regex(self):
        markdown_text = (
            "# Forex Trade Proposal: AUDUSD\n\n"
            "- **Action**: **LONG**\n"
            "- **Order Type**: MARKET\n"
            "- **Entry Price**: 0.65500\n"
            "- **Stop Loss**: 0.65100\n"
            "- **Take Profit 1**: 0.66300\n"
            "- **Risk Allocation**: 1.0%\n"
            "### Trade Rationale\n"
            "RBA rate hike anticipation supporting Aussie dollar.\n"
        )
        parsed = parse_forex_proposal_from_text(markdown_text, "AUDUSD")
        assert parsed.pair == "AUDUSD"
        assert parsed.action == ForexAction.LONG
        assert parsed.entry_price == 0.65500
        assert parsed.stop_loss == 0.65100
        assert parsed.take_profit_1 == 0.66300
        assert parsed.risk_reward_ratio == 2.0

    def test_trader_inverted_geometry_safety(self):
        # LONG with entry <= stop_loss must safely default to NO_TRADE
        inverted_text = (
            "- Action: LONG\n"
            "- Entry Price: 1.08000\n"
            "- Stop Loss: 1.08500\n"  # Inverted! Stop loss above entry for LONG
            "- Take Profit 1: 1.09000\n"
        )
        parsed = parse_forex_proposal_from_text(inverted_text, "EURUSD")
        assert parsed.action == ForexAction.NO_TRADE
        assert "Inverted LONG geometry" in parsed.trade_rationale_summary

    def test_trader_incomplete_levels_safety(self):
        # Directional action without stop loss must default to NO_TRADE
        incomplete_text = (
            "- Action: SHORT\n"
            "- Entry Price: 1.08500\n"
            # Missing stop loss
        )
        parsed = parse_forex_proposal_from_text(incomplete_text, "EURUSD")
        assert parsed.action == ForexAction.NO_TRADE
        assert "Incomplete price levels" in parsed.trade_rationale_summary

    def test_trader_empty_text_safety(self):
        parsed = parse_forex_proposal_from_text("", "EURUSD")
        assert parsed.action == ForexAction.NO_TRADE


# ---------------------------------------------------------------------------
# 3. Deterministic Forex Risk Evaluator Tests
# ---------------------------------------------------------------------------


class TestDeterministicForexRiskEvaluator:
    """Test deterministic risk enforcement, R:R thresholds, blackout windows, and sizing."""

    def test_risk_evaluator_approves_valid_setup(self):
        proposal = ForexTraderProposal(
            pair="EURUSD",
            action=ForexAction.LONG,
            entry_price=1.08500,
            stop_loss=1.08100,  # 40 pips SL
            take_profit_1=1.09200,  # 70 pips TP -> R:R 1.75
            suggested_risk_percent=1.0,
            reasoning="Valid setup with 1.75:1 R:R",
        )

        risk_limits = ForexRiskLimits(
            min_risk_reward_ratio=1.5,
            hard_min_risk_reward_ratio=1.0,
            enforce_market_open=False,
            enforce_news_blackout=False,
        )

        account = ForexAccountProfile(equity=50000.0, currency="USD", leverage=100.0)
        holder: dict[str, PositionSizingResult] = {}

        from tradingagents.graph.forex_graph import create_forex_risk_evaluator

        evaluator = create_forex_risk_evaluator(
            risk_limits=risk_limits,
            sizing_account=account,
            sizing_result_holder=holder,
        )

        state = {
            "company_of_interest": "EURUSD",
            "trade_date": "2026-03-04",
            "forex_proposal": proposal.model_dump(),
        }

        result = evaluator(state)
        decision_dict = result["forex_risk_decision"]

        assert decision_dict["decision"] == "APPROVE"
        assert decision_dict["approved_action"] == "LONG"
        assert decision_dict["approved_lot_size"] > 0
        assert decision_dict["risk_reward_ratio"] == 1.75
        assert "latest" in holder
        assert holder["latest"].lot_size == decision_dict["approved_lot_size"]

    def test_risk_evaluator_rejects_sub_1_rr(self):
        proposal = ForexTraderProposal(
            pair="EURUSD",
            action=ForexAction.LONG,
            entry_price=1.08500,
            stop_loss=1.08000,  # 50 pips SL
            take_profit_1=1.08700,  # 20 pips TP -> R:R 0.40 (< 1.0)
            suggested_risk_percent=1.0,
            reasoning="Negative expectancy setup",
        )

        risk_limits = ForexRiskLimits(
            hard_min_risk_reward_ratio=1.0,
            enforce_market_open=False,
            enforce_news_blackout=False,
        )

        from tradingagents.graph.forex_graph import create_forex_risk_evaluator

        evaluator = create_forex_risk_evaluator(risk_limits=risk_limits)
        state = {
            "company_of_interest": "EURUSD",
            "trade_date": "2026-03-04",
            "forex_proposal": proposal.model_dump(),
        }

        result = evaluator(state)
        decision = result["forex_risk_decision"]
        assert decision["decision"] == "REJECT"
        assert decision["approved_action"] == "NO_TRADE"
        assert decision["approved_lot_size"] == 0.0
        assert any("sub-1.0" in v or "Risk:Reward" in v for v in decision["risk_violations"])

    def test_risk_evaluator_rejects_economic_news_blackout(self):
        proposal = ForexTraderProposal(
            pair="EURUSD",
            action=ForexAction.LONG,
            entry_price=1.08500,
            stop_loss=1.08100,
            take_profit_1=1.09200,
            suggested_risk_percent=1.0,
            reasoning="Setup right before Tier-1 NFP release",
        )

        risk_limits = ForexRiskLimits(
            enforce_market_open=False,
            enforce_news_blackout=True,
            blackout_lookahead_hours=2.0,
        )

        from tradingagents.graph.forex_graph import create_forex_risk_evaluator

        mock_assessment = EventRiskAssessment(
            pair="EURUSD",
            base_currency="EUR",
            quote_currency="USD",
            as_of_date="2026-03-04",
            risk_regime=EventRiskRegime.HIGH_RISK,
            recommendation=TradingActionRecommendation.AVOID_NEW_POSITIONS,
            blackout_active=True,
            hours_to_next_high_impact=0.5,
            next_high_impact_event=None,
            upcoming_events=[],
            recent_events=[],
        )

        with patch("tradingagents.risk.engine.evaluate_event_risk_regime", return_value=mock_assessment):
            evaluator = create_forex_risk_evaluator(risk_limits=risk_limits)
            state = {
                "company_of_interest": "EURUSD",
                "trade_date": "2026-03-04",
                "forex_proposal": proposal.model_dump(),
            }
            result = evaluator(state)
            decision = result["forex_risk_decision"]
            assert decision["decision"] == "REJECT"
            assert any("blackout" in v.lower() for v in decision["risk_violations"])

    def test_risk_evaluator_handles_no_trade_proposal(self):
        proposal = ForexTraderProposal(
            pair="EURUSD",
            action=ForexAction.NO_TRADE,
            reasoning="Neutral market structure, no clear edge.",
        )

        from tradingagents.graph.forex_graph import create_forex_risk_evaluator

        evaluator = create_forex_risk_evaluator()
        state = {
            "company_of_interest": "EURUSD",
            "trade_date": "2026-03-04",
            "forex_proposal": proposal.model_dump(),
        }

        result = evaluator(state)
        decision = result["forex_risk_decision"]
        assert decision["decision"] == "APPROVE"
        assert decision["approved_action"] == "NO_TRADE"
        assert decision["approved_lot_size"] == 0.0


# ---------------------------------------------------------------------------
# 4. Portfolio Manager & Journal Auto-Persistence Tests
# ---------------------------------------------------------------------------


class TestForexPortfolioManager:
    """Test executive synthesis and SQLite ForexTradeJournal auto-persistence."""

    def test_portfolio_manager_auto_saves_proposal(self):
        journal = ForexTradeJournal(db_path=":memory:")
        proposal = ForexTraderProposal(
            pair="EURUSD",
            action=ForexAction.LONG,
            entry_price=1.08500,
            stop_loss=1.08100,
            take_profit_1=1.09200,
            suggested_risk_percent=1.0,
            reasoning="Valid breakout setup",
        )
        decision = ForexRiskDecision(
            pair="EURUSD",
            decision=ForexRiskDecisionAction.APPROVE,
            original_action=ForexAction.LONG,
            approved_action=ForexAction.LONG,
            max_risk_percent=1.0,
            approved_lot_size=1.25,
            entry_price=1.08500,
            stop_loss=1.08100,
            take_profit=1.09200,
            risk_reward_ratio=1.75,
            min_rr_threshold=1.5,
            executive_rationale="Approved within risk boundaries.",
        )

        from tradingagents.graph.forex_graph import create_forex_portfolio_manager

        pm = create_forex_portfolio_manager(journal=journal)
        state = {
            "company_of_interest": "EURUSD",
            "trade_date": "2026-03-04",
            "forex_proposal": proposal.model_dump(),
            "forex_risk_decision": decision.model_dump(),
            "final_trade_decision": render_forex_trader_proposal(proposal),
        }

        pm(state)

        # An approved proposal remains a proposal until execution is observed.
        proposals = journal.list_proposals(pair="EURUSD")
        assert len(proposals) == 1
        assert proposals[0].pair == "EURUSD"
        assert proposals[0].action == ForexAction.LONG
        assert proposals[0].status == ProposalStatus.APPROVED

        assert journal.list_trades(pair="EURUSD") == []

    @pytest.mark.parametrize(
        ("factory_name", "kwargs"),
        [
            ("create_forex_portfolio_manager", {}),
            ("ForexTradingAgentsGraph", {}),
            ("ForexGraphSetup", {
                "quick_thinking_llm": None,
                "deep_thinking_llm": None,
                "tool_nodes": {},
                "conditional_logic": None,
            }),
        ],
    )
    def test_legacy_auto_execution_cannot_create_journal_trades(self, factory_name, kwargs):
        from tradingagents.graph import forex_graph

        with ForexTradeJournal(db_path=":memory:") as journal:
            with pytest.raises(ValueError, match="Record actual executions"):
                getattr(forex_graph, factory_name)(
                    journal=journal, auto_record_trades=True, **kwargs
                )
            assert journal.list_trades() == []
            assert journal.list_proposals() == []

    def test_portfolio_manager_no_trade_on_rejection(self):
        journal = ForexTradeJournal(db_path=":memory:")
        proposal = ForexTraderProposal(
            pair="EURUSD",
            action=ForexAction.SHORT,
            entry_price=1.08500,
            stop_loss=1.08900,
            take_profit_1=1.08400,
            suggested_risk_percent=1.0,
            reasoning="Sub-1.0 R:R setup",
        )
        decision = ForexRiskDecision(
            pair="EURUSD",
            decision=ForexRiskDecisionAction.REJECT,
            original_action=ForexAction.SHORT,
            approved_action=ForexAction.NO_TRADE,
            max_risk_percent=0.0,
            approved_lot_size=0.0,
            min_rr_threshold=1.5,
            risk_violations=["Risk:Reward ratio 0.25 below minimum threshold 1.0:1."],
            executive_rationale="Rejected due to negative expectancy.",
        )

        from tradingagents.graph.forex_graph import create_forex_portfolio_manager

        pm = create_forex_portfolio_manager(journal=journal)
        state = {
            "company_of_interest": "EURUSD",
            "trade_date": "2026-03-04",
            "forex_proposal": proposal.model_dump(),
            "forex_risk_decision": decision.model_dump(),
            "final_trade_decision": "FINAL RISK DECISION: **REJECT**",
        }

        pm(state)

        # Proposal recorded as REJECTED
        proposals = journal.list_proposals(pair="EURUSD")
        assert len(proposals) == 1
        assert proposals[0].status == ProposalStatus.REJECTED

        # NO trade should be recorded in trade_journal
        trades = journal.list_trades(pair="EURUSD")
        assert len(trades) == 0


# ---------------------------------------------------------------------------
# 5. End-to-End Workflow Execution Tests
# ---------------------------------------------------------------------------


class TestForexTradingAgentsGraphE2E:
    """End-to-end execution of ForexTradingAgentsGraph."""

    @pytest.fixture
    def setup_e2e_graph(self):
        """Provide a fully configured ForexTradingAgentsGraph with deterministic LLMs."""
        journal = ForexTradeJournal(db_path=":memory:")

        # Proposal generated by trader
        valid_proposal = ForexTraderProposal(
            pair="EURUSD",
            action=ForexAction.LONG,
            order_type=OrderType.MARKET,
            setup_type=SetupType.TREND_CONTINUATION,
            timeframe="H1",
            entry_price=1.08500,
            stop_loss=1.08100,
            take_profit_1=1.09200,
            suggested_risk_percent=1.0,
            reasoning="H1 Bullish structure confirmation",
        )

        # Quick thinking mock handles analysts, debate, and trader
        mock_quick = MockChatModel(
            response_msg=AIMessage(content="Analyst report or debate stance", tool_calls=[]),
            structured_response=valid_proposal,
        )

        # Deep thinking mock handles research manager and portfolio manager
        mock_deep = MockChatModel(
            response_msg=AIMessage(content="Synthesis plan", tool_calls=[]),
        )

        risk_limits = ForexRiskLimits(
            enforce_market_open=False,
            enforce_news_blackout=False,
            min_risk_reward_ratio=1.5,
        )

        account = ForexAccountProfile(equity=25000.0, currency="USD", leverage=50.0)

        graph = ForexTradingAgentsGraph(
            selected_analysts=("forex_technical", "forex_macro", "forex_news"),
            risk_limits=risk_limits,
            sizing_account=account,
            journal=journal,
            quick_thinking_llm=mock_quick,
            deep_thinking_llm=mock_deep,
        )
        return graph, journal

    def test_e2e_run_approved_long(self, setup_e2e_graph):
        graph, journal = setup_e2e_graph

        final_state, signal = graph.run("EURUSD", trade_date="2026-03-04")

        # Verify signal
        assert signal == "LONG"

        # Verify state content
        assert final_state["company_of_interest"] == "EURUSD"
        assert final_state["asset_type"] == "forex"
        assert "forex_proposal" in final_state
        assert "forex_risk_decision" in final_state

        # Verify inspection helpers
        proposal = graph.get_last_proposal()
        assert proposal is not None
        assert proposal.pair == "EURUSD"
        assert proposal.action == ForexAction.LONG

        decision = graph.get_last_risk_decision()
        assert decision is not None
        assert decision.decision == ForexRiskDecisionAction.APPROVE
        assert decision.approved_action == ForexAction.LONG
        assert decision.approved_lot_size > 0

        sizing_res = graph.get_last_sizing_result()
        assert sizing_res is not None
        assert sizing_res.lot_size == decision.approved_lot_size

        # Verify journal auto-logging
        proposals = journal.list_proposals(pair="EURUSD")
        assert len(proposals) == 1
        assert proposals[0].status == ProposalStatus.APPROVED

        assert journal.list_trades(pair="EURUSD") == []

    def test_e2e_streaming_execution(self, setup_e2e_graph):
        graph, journal = setup_e2e_graph

        chunks = list(graph.stream("EURUSD", trade_date="2026-03-04"))
        assert len(chunks) > 0

        # State should be updated after streaming
        assert graph.get_state() is not None
        assert graph.get_last_proposal() is not None
        assert graph.get_last_risk_decision() is not None
        assert journal.list_proposals()[0].status == ProposalStatus.APPROVED
        assert journal.list_trades() == []

    def test_signal_processing(self):
        mock_llm = MockChatModel()
        graph = ForexTradingAgentsGraph(
            selected_analysts=("forex_technical",),
            quick_thinking_llm=mock_llm,
            deep_thinking_llm=mock_llm,
            db_path=":memory:",
        )

        # Dict input with APPROVE LONG
        state_long = {
            "forex_risk_decision": {
                "decision": "APPROVE",
                "approved_action": "LONG",
            }
        }
        assert graph.process_signal(state_long) == "LONG"

        # Dict input with REJECT
        state_reject = {
            "forex_risk_decision": {
                "decision": "REJECT",
                "approved_action": "NO_TRADE",
            }
        }
        assert graph.process_signal(state_reject) == "REJECT"

        # Text input with markdown
        text_short = "FINAL RISK DECISION: **APPROVE**\nAuthorized Execution Action: **SHORT**"
        assert graph.process_signal(text_short) == "SHORT"

    def test_checkpointing_with_memory_saver(self):
        memory_saver = MemorySaver()

        proposal = ForexTraderProposal(
            pair="EURUSD",
            action=ForexAction.NO_TRADE,
            reasoning="Neutral test",
        )
        mock_llm = MockChatModel(
            response_msg=AIMessage(content="Neutral plan", tool_calls=[]),
            structured_response=proposal,
        )

        risk_limits = ForexRiskLimits(enforce_market_open=False, enforce_news_blackout=False)

        graph = ForexTradingAgentsGraph(
            selected_analysts=("forex_technical",),
            risk_limits=risk_limits,
            quick_thinking_llm=mock_llm,
            deep_thinking_llm=mock_llm,
            checkpointer=memory_saver,
            db_path=":memory:",
        )

        final_state, signal = graph.run("EURUSD", trade_date="2026-03-04", thread_id="test_thread_1")
        assert signal == "NO_TRADE"
        assert final_state is not None

    def test_save_reports_generation(self, tmp_path):
        mock_llm = MockChatModel()
        graph = ForexTradingAgentsGraph(
            selected_analysts=("forex_technical",),
            quick_thinking_llm=mock_llm,
            deep_thinking_llm=mock_llm,
            db_path=":memory:",
            config={"results_dir": str(tmp_path)},
        )

        dummy_state = {
            "company_of_interest": "EURUSD",
            "trade_date": "2026-03-04",
            "forex_technical_report": "# Technical Analysis Report",
            "trader_investment_plan": "# Trader Proposal",
            "final_trade_decision": "# Final Risk Decision",
        }

        save_path = tmp_path / "custom_forex_report"
        report_dir = graph.save_reports(dummy_state, "EURUSD", save_path=save_path)
        assert report_dir.exists()


class TestForexGraphConfiguration:
    """Validate ForexTradingAgentsGraph LLM creation, signature order, and configuration precedence."""

    def test_llm_creation_signature_and_model_names(self):
        mock_client = MockChatModel()
        fake_wrapper = type("FakeWrapper", (), {"get_llm": lambda self: mock_client})()

        with patch("tradingagents.graph.forex_graph.create_llm_client", return_value=fake_wrapper) as mock_create:
            custom_cfg = {
                "llm_provider": "anthropic",
                "quick_think_llm": "claude-haiku-4-5",
                "deep_think_llm": "claude-sonnet-4-5",
                "backend_url": "https://api.custom.com",
                "temperature": 0.3,
                "max_tokens": 4096,
                "llm_max_retries": 5,
                "anthropic_effort": "low",
            }
            graph = ForexTradingAgentsGraph(
                selected_analysts=("forex_technical",),
                config=custom_cfg,
                db_path=":memory:",
            )

            assert graph.quick_thinking_llm is mock_client
            assert graph.deep_thinking_llm is mock_client

            assert mock_create.call_count == 2
            # Quick LLM call
            quick_call = mock_create.call_args_list[0]
            assert quick_call.kwargs.get("provider") == "anthropic"
            assert quick_call.kwargs.get("model") == "claude-haiku-4-5"
            assert quick_call.kwargs.get("base_url") == "https://api.custom.com"
            assert quick_call.kwargs.get("temperature") == 0.3
            assert quick_call.kwargs.get("max_tokens") == 4096
            assert quick_call.kwargs.get("max_retries") == 5
            assert quick_call.kwargs.get("effort") == "low"

            # Deep LLM call
            deep_call = mock_create.call_args_list[1]
            assert deep_call.kwargs.get("provider") == "anthropic"
            assert deep_call.kwargs.get("model") == "claude-sonnet-4-5"
            assert deep_call.kwargs.get("base_url") == "https://api.custom.com"

    def test_legacy_model_key_fallback(self):
        mock_client = MockChatModel()
        fake_wrapper = type("FakeWrapper", (), {"get_llm": lambda self: mock_client})()

        with patch("tradingagents.graph.forex_graph.create_llm_client", return_value=fake_wrapper) as mock_create:
            custom_cfg = {
                "llm_provider": "openai",
                "quick_model": "gpt-4o-mini-legacy",
                "deep_model": "gpt-4o-legacy",
            }
            _ = ForexTradingAgentsGraph(
                selected_analysts=("forex_technical",),
                config=custom_cfg,
                db_path=":memory:",
            )

            assert mock_create.call_count == 2
            assert mock_create.call_args_list[0].kwargs.get("model") == "gpt-4o-mini-legacy"
            assert mock_create.call_args_list[1].kwargs.get("model") == "gpt-4o-legacy"

    def test_env_var_precedence_in_forex_graph(self, monkeypatch):
        monkeypatch.setenv("TRADINGAGENTS_LLM_PROVIDER", "google")
        monkeypatch.setenv("TRADINGAGENTS_QUICK_THINK_LLM", "gemini-2.5-flash")
        monkeypatch.setenv("TRADINGAGENTS_DEEP_THINK_LLM", "gemini-2.5-pro")
        monkeypatch.setenv("TRADINGAGENTS_LLM_BACKEND_URL", "https://generativelanguage.googleapis.com")
        monkeypatch.setenv("TRADINGAGENTS_TEMPERATURE", "0.1")
        monkeypatch.setenv("TRADINGAGENTS_GOOGLE_THINKING_LEVEL", "low")

        mock_client = MockChatModel()
        fake_wrapper = type("FakeWrapper", (), {"get_llm": lambda self: mock_client})()

        with patch("tradingagents.graph.forex_graph.create_llm_client", return_value=fake_wrapper) as mock_create:
            graph = ForexTradingAgentsGraph(
                selected_analysts=("forex_technical",),
                db_path=":memory:",
            )

            assert graph.config["llm_provider"] == "google"
            assert graph.config["quick_think_llm"] == "gemini-2.5-flash"
            assert graph.config["deep_think_llm"] == "gemini-2.5-pro"
            assert graph.config["backend_url"] == "https://generativelanguage.googleapis.com"
            assert graph.config["temperature"] == 0.1

            quick_call = mock_create.call_args_list[0]
            assert quick_call.kwargs.get("provider") == "google"
            assert quick_call.kwargs.get("model") == "gemini-2.5-flash"
            assert quick_call.kwargs.get("thinking_level") == "low"
            assert quick_call.kwargs.get("temperature") == 0.1

