"""Dedicated Forex LangGraph Workflow Orchestration (Phase 13).

Orchestrates the complete institutional Forex decision-support pipeline:
1. Multi-analyst Forex intelligence:
   - ``forex_technical``: Multi-timeframe structure, EMA alignment, ATR, RSI, MACD, Fair Value Gaps.
   - ``forex_macro``: Central bank monetary policy, policy rate differentials, bond yields, inflation.
   - ``forex_news``: Economic calendar high-impact events, release blackout windows, geopolitical news.
2. Research debate:
   - Bull Researcher vs Bear Researcher iterative debate.
   - Research Manager synthesis into structured investment thesis.
3. Institutional Forex Trader:
   - Synthesizes market structure and macro bias into a typed ``ForexTraderProposal``.
4. Deterministic Forex Risk Evaluator:
   - Enforces non-negotiable risk limits via ``ForexRiskEngine`` (geometry, R:R >= 1.5, blackout, session hours).
   - Sizes position and calculates margin via ``ForexPositionSizingEngine``.
   - Produces institutional ``ForexRiskDecision`` with auditable verification checks.
5. Portfolio Manager:
   - Executive sign-off and synthesis into ``final_trade_decision``.
   - Auto-persistence of proposals and risk decisions into SQLite ``ForexTradeJournal``.
   - Actual trade records require separate execution evidence.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Iterator, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable, RunnableConfig, RunnableLambda
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from tradingagents.agents import (
    create_bear_researcher,
    create_bull_researcher,
    create_forex_macro_analyst,
    create_forex_news_analyst,
    create_forex_technical_analyst,
    create_msg_delete,
    create_research_manager,
)
from tradingagents.agents.analysts.forex_macro import FOREX_MACRO_TOOLS
from tradingagents.agents.analysts.forex_technical import FOREX_TECHNICAL_TOOLS
from tradingagents.agents.schemas_forex import (
    ForexAction,
    ForexRiskDecision,
    ForexRiskDecisionAction,
    ForexTraderProposal,
    render_forex_risk_decision,
)
from tradingagents.agents.trader.forex_trader import (
    create_forex_trader,
    parse_forex_proposal_from_text,
)
from tradingagents.agents.utils.agent_states import AgentState
from tradingagents.agents.utils.forex_news_tools import FOREX_NEWS_TOOLS
from tradingagents.database.journal import ForexTradeJournal, ProposalStatus
from tradingagents.dataflows.config import build_config, config_scope
from tradingagents.dataflows.forex_context import prepare_live_forex_context
from tradingagents.dataflows.utils import safe_ticker_component
from tradingagents.forex.calendar import _calendar_cutoff
from tradingagents.forex.domain import (
    Timeframe,
    get_default_context_timeframes,
    normalize_forex_pair,
)
from tradingagents.graph.analyst_execution import build_analyst_execution_plan
from tradingagents.graph.conditional_logic import ConditionalLogic
from tradingagents.graph.propagation import Propagator
from tradingagents.graph.setup import DEBATE_PATH_MAP
from tradingagents.llm_clients import create_llm_client
from tradingagents.reporting import write_report_tree
from tradingagents.risk.engine import ForexRiskEngine, ForexRiskLimits
from tradingagents.risk.sizing import (
    BrokerExecutionConstraints,
    ForexAccountProfile,
    ForexPositionSizingEngine,
    PositionSizingMethod,
    PositionSizingResult,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Node Factories
# ---------------------------------------------------------------------------


def create_forex_risk_evaluator(
    risk_engine: ForexRiskEngine | None = None,
    sizing_engine: ForexPositionSizingEngine | None = None,
    risk_limits: ForexRiskLimits | None = None,
    sizing_account: ForexAccountProfile | None = None,
    sizing_constraints: BrokerExecutionConstraints | None = None,
    sizing_method: PositionSizingMethod = PositionSizingMethod.FIXED_RISK_PERCENT,
    sizing_result_holder: dict[str, PositionSizingResult] | None = None,
):
    """Factory for the deterministic institutional Forex Risk Evaluator node.

    Validates proposals against non-negotiable risk limits (geometry, session
    hours, economic calendar news blackout, stop-loss pip limits, R:R >= 1.0)
    and computes exact volume via the position sizing engine.
    """
    engine = risk_engine or ForexRiskEngine(default_limits=risk_limits)
    s_engine = sizing_engine or ForexPositionSizingEngine()
    account = sizing_account or ForexAccountProfile()
    constraints = sizing_constraints or BrokerExecutionConstraints()

    def forex_risk_evaluator_node(
        state: dict[str, Any], name: str = "Forex Risk Evaluator"
    ) -> dict[str, Any]:
        raw_pair = state.get("company_of_interest", "EURUSD")
        pair = normalize_forex_pair(raw_pair)
        curr_date = state.get("trade_date")

        # 1. Retrieve or reconstruct proposal
        selected_tf = state.get("forex_execution_timeframe") or state.get("timeframe") or "H1"
        if hasattr(selected_tf, "value"):
            selected_tf = selected_tf.value

        proposal: ForexTraderProposal | None = None
        raw_proposal = state.get("forex_proposal")
        if raw_proposal and isinstance(raw_proposal, dict):
            try:
                proposal = ForexTraderProposal.model_validate(raw_proposal)
            except Exception as exc:
                logger.warning("Failed deserializing forex_proposal from state: %s", exc)

        if proposal is None:
            raw_text = state.get("trader_investment_plan", "")
            proposal = parse_forex_proposal_from_text(raw_text, pair, default_timeframe=selected_tf)

        if proposal is not None and selected_tf and proposal.timeframe != selected_tf:
            proposal = proposal.model_copy(update={"timeframe": selected_tf})

        # 2. Deterministic Risk Limits Validation
        risk_decision: ForexRiskDecision = engine.validate_proposal(
            proposal=proposal,
            curr_date=state.get("forex_as_of_utc") or curr_date,
            account_balance=account.equity,
            account_currency=account.currency,
            limits=risk_limits,
        )

        # 3. Deterministic Position Sizing & Margin Validation
        sizing_result: PositionSizingResult = s_engine.size_proposal(
            proposal=proposal,
            sizing_method=sizing_method,
            account=account,
            constraints=constraints,
        )

        if sizing_result_holder is not None:
            sizing_result_holder["latest"] = sizing_result

        # Reconcile sizing with risk decision
        if proposal.action == ForexAction.NO_TRADE:
            risk_decision.approved_lot_size = 0.0
            risk_decision.approved_action = ForexAction.NO_TRADE
        elif risk_decision.decision in (ForexRiskDecisionAction.APPROVE, ForexRiskDecisionAction.MODIFY):
            if sizing_result.lot_size <= 0.0:
                risk_decision.decision = ForexRiskDecisionAction.REJECT
                risk_decision.approved_action = ForexAction.NO_TRADE
                risk_decision.approved_lot_size = 0.0
                reason = sizing_result.rejection_reason or "Position sizing returned 0.0 lots (insufficient capital/excessive risk)."
                risk_decision.risk_violations.append(f"Position Sizing Constraint: {reason}")
            else:
                risk_decision.approved_lot_size = sizing_result.lot_size
                risk_decision.max_risk_percent = sizing_result.risk_percent
                risk_decision.risk_checks_passed.append(
                    f"Position Sized: {sizing_result.lot_size:.2f} standard lots "
                    f"({sizing_result.risk_amount:.2f} {account.currency}, {sizing_result.risk_percent:.2f}% equity, "
                    f"req margin: {sizing_result.required_margin:.2f} {account.currency})."
                )
        else:
            risk_decision.approved_lot_size = 0.0
            risk_decision.approved_action = ForexAction.NO_TRADE

        rendered_decision = render_forex_risk_decision(risk_decision)

        return {
            "messages": [AIMessage(content=rendered_decision)],
            "forex_proposal": proposal.model_dump() if proposal else None,
            "forex_risk_decision": risk_decision.model_dump(),
            "final_trade_decision": rendered_decision,
            "sender": name,
        }

    return forex_risk_evaluator_node


def _require_manual_execution(auto_record_trades: bool) -> None:
    """Prevent legacy callers from turning proposals into actual journal trades."""
    if auto_record_trades:
        raise ValueError(
            "auto_record_trades=True is no longer supported. "
            "Record actual executions through the journal or MT5 reconciliation."
        )


def create_forex_portfolio_manager(
    llm: Any | None = None,
    journal: ForexTradeJournal | None = None,
    auto_record_trades: bool = False,
):
    """Factory for the Forex Portfolio Manager node.

    Delivers final executive sign-off and auto-persists approved/rejected
    proposals into the SQLite ``ForexTradeJournal``. Trade records require execution
    evidence supplied through the journal or MT5 reconciliation.
    """
    _require_manual_execution(auto_record_trades)

    def forex_portfolio_manager_node(
        state: dict[str, Any], name: str = "Portfolio Manager"
    ) -> dict[str, Any]:
        raw_pair = state.get("company_of_interest", "EURUSD")
        pair = normalize_forex_pair(raw_pair)
        curr_date = state.get("trade_date")

        raw_decision = state.get("forex_risk_decision")
        raw_proposal = state.get("forex_proposal")
        selected_tf = state.get("forex_execution_timeframe") or state.get("timeframe") or "H1"
        if hasattr(selected_tf, "value"):
            selected_tf = selected_tf.value

        proposal: ForexTraderProposal | None = None
        decision: ForexRiskDecision | None = None

        if raw_proposal and isinstance(raw_proposal, dict):
            with contextlib.suppress(Exception):
                proposal = ForexTraderProposal.model_validate(raw_proposal)
        if proposal is None:
            proposal = parse_forex_proposal_from_text(state.get("trader_investment_plan", ""), pair, default_timeframe=selected_tf)

        if proposal is not None and selected_tf and proposal.timeframe != selected_tf:
            proposal = proposal.model_copy(update={"timeframe": selected_tf})

        if raw_decision and isinstance(raw_decision, dict):
            with contextlib.suppress(Exception):
                decision = ForexRiskDecision.model_validate(raw_decision)


        rendered_decision = state.get("final_trade_decision", "")

        # Optional LLM executive synthesis if provided
        final_summary = rendered_decision
        if llm is not None:
            try:
                prompt = (
                    f"Synthesize the following Forex trade decision for {pair} into an executive brief:\n\n"
                    f"Research Plan:\n{state.get('investment_plan', '')}\n\n"
                    f"Trader Proposal:\n{state.get('trader_investment_plan', '')}\n\n"
                    f"Risk Decision:\n{rendered_decision}\n\n"
                    "Confirm the authorized action, volume, key levels, and risk parameters."
                )
                res = llm.invoke(prompt)
                content = getattr(res, "content", str(res)).strip()
                if content:
                    final_summary = f"{rendered_decision}\n\n### Executive Portfolio Brief\n{content}"
            except Exception as exc:
                logger.warning("Portfolio Manager LLM synthesis failed (%s); using risk decision rendering", exc)

        proposal_id = None
        # SQLite Journal Auto-Logging
        if journal is not None and proposal is not None:
            try:
                status = ProposalStatus.PROPOSED
                if decision is not None:
                    if decision.decision == ForexRiskDecisionAction.APPROVE:
                        status = ProposalStatus.APPROVED
                    elif decision.decision == ForexRiskDecisionAction.MODIFY:
                        status = ProposalStatus.MODIFIED
                    else:
                        status = ProposalStatus.REJECTED

                proposal_id = journal.save_proposal(
                    proposal=proposal,
                    risk_decision=decision,
                    status=status,
                    metadata={"trade_date": curr_date, "pair": pair},
                )

                # Newer analysis supersedes older unexecuted proposals for the same pair
                if (
                    status in (ProposalStatus.APPROVED, ProposalStatus.MODIFIED, ProposalStatus.PROPOSED)
                    and hasattr(journal, "supersede_proposals")
                ):
                    journal.supersede_proposals(pair=pair, exclude_proposal_id=proposal_id)

            except Exception as exc:
                logger.error("Failed auto-logging Forex proposal to journal: %s", exc, exc_info=True)
                raise

        return {
            "forex_proposal_id": proposal_id,
            "messages": [AIMessage(content=final_summary)],
            "final_trade_decision": final_summary,
            "sender": name,
        }

    return forex_portfolio_manager_node


# ---------------------------------------------------------------------------
# Forex Graph Setup
# ---------------------------------------------------------------------------


class ForexGraphSetup:
    """Configures and compiles the dedicated institutional Forex LangGraph workflow."""

    def __init__(
        self,
        quick_thinking_llm: Any,
        deep_thinking_llm: Any,
        tool_nodes: dict[str, ToolNode],
        conditional_logic: ConditionalLogic,
        risk_engine: ForexRiskEngine | None = None,
        sizing_engine: ForexPositionSizingEngine | None = None,
        risk_limits: ForexRiskLimits | None = None,
        sizing_account: ForexAccountProfile | None = None,
        sizing_constraints: BrokerExecutionConstraints | None = None,
        sizing_method: PositionSizingMethod = PositionSizingMethod.FIXED_RISK_PERCENT,
        sizing_result_holder: dict[str, PositionSizingResult] | None = None,
        journal: ForexTradeJournal | None = None,
        auto_record_trades: bool = False,
        config: dict | None = None,
    ):
        _require_manual_execution(auto_record_trades)
        self.quick_thinking_llm = quick_thinking_llm
        self.deep_thinking_llm = deep_thinking_llm
        self.tool_nodes = tool_nodes
        self.conditional_logic = conditional_logic
        self.risk_engine = risk_engine
        self.sizing_engine = sizing_engine
        self.risk_limits = risk_limits
        self.sizing_account = sizing_account
        self.sizing_constraints = sizing_constraints
        self.sizing_method = sizing_method
        self.sizing_result_holder = sizing_result_holder
        self.journal = journal
        self.auto_record_trades = auto_record_trades
        self.config = config

    def _bind_config(self, node):
        if self.config is None:
            return node
        runnable = node if isinstance(node, Runnable) else RunnableLambda(node)

        def invoke(state, config: RunnableConfig):
            with config_scope(self.config):
                return runnable.invoke(state, config)

        async def ainvoke(state, config: RunnableConfig):
            with config_scope(self.config):
                return await runnable.ainvoke(state, config)

        return RunnableLambda(invoke, afunc=ainvoke)

    def setup_graph(
        self,
        selected_analysts: Sequence[str] = ("forex_technical", "forex_macro", "forex_news"),
    ) -> StateGraph:
        """Set up and assemble the Forex workflow graph."""
        plan = build_analyst_execution_plan(selected_analysts)

        analyst_factories = {
            "forex_technical": lambda: create_forex_technical_analyst(self.quick_thinking_llm),
            "forex_macro": lambda: create_forex_macro_analyst(self.quick_thinking_llm),
            "forex_news": lambda: create_forex_news_analyst(self.quick_thinking_llm),
        }

        # Nodes
        bull_researcher = create_bull_researcher(self.quick_thinking_llm)
        bear_researcher = create_bear_researcher(self.quick_thinking_llm)
        research_manager = create_research_manager(self.deep_thinking_llm)
        forex_trader = create_forex_trader(self.quick_thinking_llm)

        forex_risk_evaluator = create_forex_risk_evaluator(
            risk_engine=self.risk_engine,
            sizing_engine=self.sizing_engine,
            risk_limits=self.risk_limits,
            sizing_account=self.sizing_account,
            sizing_constraints=self.sizing_constraints,
            sizing_method=self.sizing_method,
            sizing_result_holder=self.sizing_result_holder,
        )

        portfolio_manager = create_forex_portfolio_manager(
            llm=self.deep_thinking_llm,
            journal=self.journal,
            auto_record_trades=self.auto_record_trades,
        )

        workflow = StateGraph(AgentState)

        # 1. Register analyst nodes
        for spec in plan.specs:
            factory = analyst_factories.get(spec.key)
            if factory is None:
                raise ValueError(f"Unknown Forex analyst type: {spec.key!r}")
            workflow.add_node(spec.agent_node, self._bind_config(factory()))
            workflow.add_node(spec.clear_node, self._bind_config(create_msg_delete()))
            workflow.add_node(spec.tool_node, self._bind_config(self.tool_nodes[spec.key]))

        # 2. Register debate & decision nodes
        workflow.add_node("Bull Researcher", self._bind_config(bull_researcher))
        workflow.add_node("Bear Researcher", self._bind_config(bear_researcher))
        workflow.add_node("Research Manager", self._bind_config(research_manager))
        workflow.add_node("Forex Trader", self._bind_config(forex_trader))
        workflow.add_node("Forex Risk Evaluator", self._bind_config(forex_risk_evaluator))
        workflow.add_node("Portfolio Manager", self._bind_config(portfolio_manager))

        # 3. Connect Edges
        # START -> First Analyst
        workflow.add_edge(START, plan.specs[0].agent_node)

        # Sequential Analyst Chain
        for i, spec in enumerate(plan.specs):
            current_analyst = spec.agent_node
            current_tools = spec.tool_node
            current_clear = spec.clear_node

            router = getattr(self.conditional_logic, f"should_continue_{spec.key}")
            workflow.add_conditional_edges(
                current_analyst,
                router,
                [current_tools, current_clear],
            )
            workflow.add_edge(current_tools, current_analyst)

            if i < len(plan.specs) - 1:
                workflow.add_edge(current_clear, plan.specs[i + 1].agent_node)
            else:
                next_node = (
                    "Bull Researcher"
                    if (self.config or {}).get("historical_debate_enabled", True)
                    else "Research Manager"
                )
                workflow.add_edge(current_clear, next_node)

        # Debate Loop
        for debate_node in ("Bull Researcher", "Bear Researcher"):
            workflow.add_conditional_edges(
                debate_node,
                self.conditional_logic.should_continue_debate,
                DEBATE_PATH_MAP,
            )

        # Research Manager -> Forex Trader -> Forex Risk Evaluator -> Portfolio Manager -> END
        workflow.add_edge("Research Manager", "Forex Trader")
        workflow.add_edge("Forex Trader", "Forex Risk Evaluator")
        workflow.add_edge("Forex Risk Evaluator", "Portfolio Manager")
        workflow.add_edge("Portfolio Manager", END)

        return workflow


# ---------------------------------------------------------------------------
# Forex TradingAgents Graph (Primary Interface)
# ---------------------------------------------------------------------------


class ForexTradingAgentsGraph:
    """Primary high-level orchestrator for the institutional Forex workflow."""

    def __init__(
        self,
        selected_analysts: Sequence[str] = ("forex_technical", "forex_macro", "forex_news"),
        config: dict | None = None,
        risk_limits: ForexRiskLimits | None = None,
        risk_engine: ForexRiskEngine | None = None,
        sizing_engine: ForexPositionSizingEngine | None = None,
        sizing_account: ForexAccountProfile | None = None,
        sizing_constraints: BrokerExecutionConstraints | None = None,
        sizing_method: PositionSizingMethod = PositionSizingMethod.FIXED_RISK_PERCENT,
        journal: ForexTradeJournal | None = None,
        db_path: str | Path | None = None,
        auto_record_trades: bool = False,
        quick_thinking_llm: Any = None,
        deep_thinking_llm: Any = None,
        checkpointer: Any = None,
        debug: bool = False,
    ):
        _require_manual_execution(auto_record_trades)
        self.config = build_config(config)
        self.debug = debug
        self.selected_analysts = tuple(selected_analysts)

        # Risk & Sizing Components
        self.risk_limits = risk_limits or ForexRiskLimits()
        self.risk_engine = risk_engine or ForexRiskEngine(default_limits=self.risk_limits)
        self.sizing_engine = sizing_engine or ForexPositionSizingEngine()
        self.sizing_account = sizing_account or ForexAccountProfile()
        self.sizing_constraints = sizing_constraints or BrokerExecutionConstraints()
        self.sizing_method = sizing_method
        self.sizing_result_holder: dict[str, PositionSizingResult] = {}

        # Trade Journal
        if journal is not None:
            self.journal: ForexTradeJournal | None = journal
        elif db_path is not None:
            self.journal = ForexTradeJournal(db_path=db_path)
        else:
            self.journal = ForexTradeJournal(db_path=":memory:")
        self.auto_record_trades = auto_record_trades

        # LLMs
        llm_kwargs = self._get_provider_kwargs()
        provider = self.config.get("llm_provider", "openai")
        backend_url = self.config.get("backend_url") or self.config.get("llm_backend_url")
        user_cfg = config or {}

        quick_model = (
            user_cfg.get("quick_think_llm")
            or user_cfg.get("quick_model")
            or self.config.get("quick_think_llm")
            or self.config.get("quick_model")
            or "gpt-4.1-mini"
        )
        deep_model = (
            user_cfg.get("deep_think_llm")
            or user_cfg.get("deep_model")
            or self.config.get("deep_think_llm")
            or self.config.get("deep_model")
            or "gpt-4.1"
        )


        if quick_thinking_llm is not None:
            self.quick_thinking_llm = quick_thinking_llm
        else:
            quick_client = create_llm_client(
                provider=provider,
                model=quick_model,
                base_url=backend_url,
                **llm_kwargs,
            )
            self.quick_thinking_llm = quick_client.get_llm()

        if deep_thinking_llm is not None:
            self.deep_thinking_llm = deep_thinking_llm
        else:
            deep_client = create_llm_client(
                provider=provider,
                model=deep_model,
                base_url=backend_url,
                **llm_kwargs,
            )
            self.deep_thinking_llm = deep_client.get_llm()

        # Tools & Logic
        self.tool_nodes = {
            "forex_technical": ToolNode(FOREX_TECHNICAL_TOOLS),
            "forex_macro": ToolNode(FOREX_MACRO_TOOLS),
            "forex_news": ToolNode(FOREX_NEWS_TOOLS),
        }
        self.conditional_logic = ConditionalLogic(
            max_debate_rounds=self.config.get("max_debate_rounds", 1),
            max_risk_discuss_rounds=0,
        )
        self.propagator = Propagator(
            max_recur_limit=self.config.get("max_recur_limit", 100)
        )

        # Setup & Compilation
        self.graph_setup = ForexGraphSetup(
            quick_thinking_llm=self.quick_thinking_llm,
            deep_thinking_llm=self.deep_thinking_llm,
            tool_nodes=self.tool_nodes,
            conditional_logic=self.conditional_logic,
            risk_engine=self.risk_engine,
            sizing_engine=self.sizing_engine,
            risk_limits=self.risk_limits,
            sizing_account=self.sizing_account,
            sizing_constraints=self.sizing_constraints,
            sizing_method=self.sizing_method,
            sizing_result_holder=self.sizing_result_holder,
            journal=self.journal,
            auto_record_trades=self.auto_record_trades,
            config=self.config,
        )

        self.workflow = self.graph_setup.setup_graph(self.selected_analysts)
        self.checkpointer = checkpointer
        self.graph = self.workflow.compile(checkpointer=checkpointer)

        # Execution tracking
        self.curr_state: dict[str, Any] | None = None
        self.pair: str | None = None

    def _get_provider_kwargs(self) -> dict[str, Any]:
        """Get provider-specific kwargs for LLM creation."""
        kwargs: dict[str, Any] = {}
        provider = self.config.get("llm_provider", "").lower()

        if provider == "google":
            thinking_level = self.config.get("google_thinking_level")
            if thinking_level:
                kwargs["thinking_level"] = thinking_level
        elif provider == "openai":
            reasoning_effort = self.config.get("openai_reasoning_effort")
            if reasoning_effort:
                kwargs["reasoning_effort"] = reasoning_effort
        elif provider == "anthropic":
            effort = self.config.get("anthropic_effort")
            if effort:
                kwargs["effort"] = effort

        temperature = self.config.get("temperature")
        if temperature is not None and temperature != "":
            kwargs["temperature"] = float(temperature)

        if self.config.get("max_tokens") is not None:
            kwargs["max_tokens"] = int(self.config["max_tokens"])

        if self.config.get("llm_max_retries") is not None:
            kwargs["max_retries"] = int(self.config["llm_max_retries"])

        return kwargs


    def create_run_state(
        self,
        pair: str,
        trade_date: str | None = None,
        asset_type: str | Any = "forex",
        portfolio=None,
        execution_timeframe: str | Timeframe | None = None,
        context_timeframes: Sequence[str | Timeframe] | None = None,
        timeframe: str | Timeframe | None = None,
    ) -> dict[str, Any]:
        """Build the initial StateGraph input dictionary."""
        if portfolio is None and not isinstance(asset_type, str):
            portfolio = asset_type
            asset_type = "forex"
        canon_pair = normalize_forex_pair(pair)
        cutoff = _calendar_cutoff(trade_date) if trade_date else datetime.now(timezone.utc)
        t_date = cutoff.date().isoformat()

        raw_exec_tf = execution_timeframe or timeframe or "H1"
        exec_tf = Timeframe.from_string(raw_exec_tf) if isinstance(raw_exec_tf, str) else raw_exec_tf
        exec_tf_str = exec_tf.value

        if context_timeframes is not None:
            ctx_tfs = tuple(
                Timeframe.from_string(t) if isinstance(t, str) else t for t in context_timeframes
            )
        else:
            ctx_tfs = get_default_context_timeframes(exec_tf)
        ctx_tf_strs = [t.value for t in ctx_tfs]

        # Retrieve relevant contextual memory (Phase 16)
        past_ctx = ""
        applied_lesson_ids: list[str] = []
        learning_mgr = getattr(self, "learning_manager", None)
        journal_obj = getattr(self, "journal", None)
        if learning_mgr is None and journal_obj is not None:
            try:
                from tradingagents.learning.manager import ForexLearningManager
                learning_mgr = ForexLearningManager(journal=journal_obj)
                self.learning_manager = learning_mgr
            except Exception:
                learning_mgr = None

        from tradingagents.dataflows.forex_context import historical_market_context
        historical = getattr(self, "config", {}).get("historical_backtest") or historical_market_context() is not None
        historical_memory = bool(
            historical and getattr(self, "config", {}).get("historical_memory_enabled", False)
        )
        if (not historical or historical_memory) and learning_mgr is not None and hasattr(learning_mgr, "retriever"):
            try:
                retrieved = learning_mgr.retriever.retrieve_lessons(
                    pair=canon_pair,
                    timeframe=exec_tf_str,
                    limit=5,
                    min_relevance=0.35,
                )
                if historical_memory:
                    def known_by_cutoff(item):
                        created = datetime.fromisoformat(
                            item.lesson.created_at.replace("Z", "+00:00")
                        )
                        if created.tzinfo is None:
                            created = created.replace(tzinfo=timezone.utc)
                        return created <= cutoff

                    retrieved = [item for item in retrieved if known_by_cutoff(item)]
                applied_lesson_ids = [item.lesson.lesson_id for item in retrieved]
                past_ctx = learning_mgr.retriever.format_lessons_for_prompt(retrieved)
            except Exception as exc:
                logger.warning("Failed retrieving contextual lessons for %s: %s", canon_pair, exc)

        init_state = self.propagator.create_initial_state(
            company_name=canon_pair,
            trade_date=t_date,
            asset_type="forex",
            past_context=past_ctx,
            instrument_context=f"Instrument: {canon_pair} ({exec_tf_str}) | Context: {', '.join(ctx_tf_strs)} | Date: {t_date}",
            portfolio_context=portfolio.render(canon_pair) if portfolio is not None else "",
        )
        init_state["applied_lesson_ids"] = applied_lesson_ids
        init_state["forex_as_of_utc"] = cutoff.isoformat()
        init_state["forex_execution_timeframe"] = exec_tf_str
        init_state["forex_context_timeframes"] = ctx_tf_strs
        init_state["timeframe"] = exec_tf_str
        init_state["higher_timeframes"] = ctx_tf_strs
        init_state["forex_proposal_id"] = None
        init_state["forex_proposal"] = None
        init_state["forex_risk_decision"] = None
        return init_state

    def run(
        self,
        pair: str,
        trade_date: str | None = None,
        portfolio=None,
        thread_id: str | None = None,
        execution_timeframe: str | Timeframe | None = None,
        context_timeframes: Sequence[str | Timeframe] | None = None,
        timeframe: str | Timeframe | None = None,
    ) -> tuple[dict[str, Any], str]:
        """Synchronously execute the Forex trading agents graph.

        Returns:
            tuple[dict[str, Any], str]: (final_state, signal) where signal is
            one of 'LONG', 'SHORT', 'NO_TRADE', 'REJECT', 'MODIFY'.
        """
        return self.propagate(
            pair=pair,
            trade_date=trade_date,
            portfolio=portfolio,
            thread_id=thread_id,
            execution_timeframe=execution_timeframe,
            context_timeframes=context_timeframes,
            timeframe=timeframe,
        )

    def propagate(
        self,
        pair: str,
        trade_date: str | None = None,
        portfolio=None,
        thread_id: str | None = None,
        execution_timeframe: str | Timeframe | None = None,
        context_timeframes: Sequence[str | Timeframe] | None = None,
        timeframe: str | Timeframe | None = None,
    ) -> tuple[dict[str, Any], str]:
        """Execute the graph and return the final state and signal verdict."""
        canon_pair = normalize_forex_pair(pair)
        self.pair = canon_pair
        with config_scope(self.config):
            resolved_date = str(trade_date) if trade_date else prepare_live_forex_context(canon_pair)

        init_state = self.create_run_state(
            canon_pair,
            resolved_date,
            portfolio=portfolio,
            execution_timeframe=execution_timeframe,
            context_timeframes=context_timeframes,
            timeframe=timeframe,
        )
        args = self.propagator.get_graph_args()

        if thread_id is not None:
            args.setdefault("config", {}).setdefault("configurable", {})["thread_id"] = thread_id

        try:
            with config_scope(self.config):
                final_state = self.graph.invoke(init_state, **args)
        except Exception as exc:
            logger.error("Forex graph run failed for %s on %s: %s", canon_pair, resolved_date, exc, exc_info=True)
            raise

        self.curr_state = final_state
        signal = self.process_signal(final_state)
        return final_state, signal

    def stream(
        self,
        pair: str,
        trade_date: str | None = None,
        portfolio=None,
        thread_id: str | None = None,
        execution_timeframe: str | Timeframe | None = None,
        context_timeframes: Sequence[str | Timeframe] | None = None,
        timeframe: str | Timeframe | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Stream per-node state chunks from the graph."""
        canon_pair = normalize_forex_pair(pair)
        self.pair = canon_pair
        with config_scope(self.config):
            resolved_date = str(trade_date) if trade_date else prepare_live_forex_context(canon_pair)

        init_state = self.create_run_state(
            canon_pair,
            resolved_date,
            portfolio=portfolio,
            execution_timeframe=execution_timeframe,
            context_timeframes=context_timeframes,
            timeframe=timeframe,
        )
        args = self.propagator.get_graph_args()

        if thread_id is not None:
            args.setdefault("config", {}).setdefault("configurable", {})["thread_id"] = thread_id

        with config_scope(self.config):
            for chunk in self.graph.stream(init_state, **args):
                if isinstance(chunk, dict):
                    if self.curr_state is None:
                        self.curr_state = dict(chunk)
                    else:
                        self.curr_state.update(chunk)
                yield chunk

    def process_signal(self, final_state_or_text: dict[str, Any] | str) -> str:
        """Extract directional trade action or risk verdict string."""
        if isinstance(final_state_or_text, dict):
            raw_decision = final_state_or_text.get("forex_risk_decision")
            if raw_decision and isinstance(raw_decision, dict):
                decision_action = raw_decision.get("decision")
                if decision_action == ForexRiskDecisionAction.REJECT.value:
                    return "REJECT"
                if decision_action == ForexRiskDecisionAction.MODIFY.value:
                    return raw_decision.get("approved_action", "MODIFY")
                return raw_decision.get("approved_action", "NO_TRADE")

            final_text = final_state_or_text.get("final_trade_decision", "")
        else:
            final_text = str(final_state_or_text)

        if "FINAL RISK DECISION: **REJECT**" in final_text or "❌ REJECTED" in final_text:
            return "REJECT"
        if "Authorized Execution Action: **LONG**" in final_text or "FINAL FOREX PROPOSAL: **LONG**" in final_text:
            return "LONG"
        if "Authorized Execution Action: **SHORT**" in final_text or "FINAL FOREX PROPOSAL: **SHORT**" in final_text:
            return "SHORT"
        if "Authorized Execution Action: **NO_TRADE**" in final_text or "FINAL FOREX PROPOSAL: **NO_TRADE**" in final_text:
            return "NO_TRADE"

        return "NO_TRADE"

    # -----------------------------------------------------------------------
    # Inspection Helpers
    # -----------------------------------------------------------------------

    def get_last_proposal(self) -> ForexTraderProposal | None:
        """Return the parsed ForexTraderProposal from the most recent run."""
        if not self.curr_state:
            return None
        selected_tf = (
            self.curr_state.get("forex_execution_timeframe")
            or self.curr_state.get("timeframe")
        )
        if hasattr(selected_tf, "value"):
            selected_tf = selected_tf.value

        raw = self.curr_state.get("forex_proposal")
        if raw and isinstance(raw, dict):
            try:
                p = ForexTraderProposal.model_validate(raw)
                if selected_tf and p.timeframe != selected_tf:
                    p = p.model_copy(update={"timeframe": selected_tf})
                return p
            except Exception:
                pass
        raw_text = self.curr_state.get("trader_investment_plan", "")
        if raw_text and self.pair:
            p = parse_forex_proposal_from_text(raw_text, self.pair, default_timeframe=selected_tf or "H1")
            if selected_tf and p.timeframe != selected_tf:
                p = p.model_copy(update={"timeframe": selected_tf})
            return p
        return None

    def get_last_risk_decision(self) -> ForexRiskDecision | None:
        """Return the validated ForexRiskDecision from the most recent run."""
        if not self.curr_state:
            return None
        raw = self.curr_state.get("forex_risk_decision")
        if raw and isinstance(raw, dict):
            try:
                return ForexRiskDecision.model_validate(raw)
            except Exception:
                pass
        return None

    def get_last_sizing_result(self) -> PositionSizingResult | None:
        """Return the latest PositionSizingResult computed by the risk node."""
        return self.sizing_result_holder.get("latest")

    def get_journal(self) -> ForexTradeJournal | None:
        """Return the active ForexTradeJournal instance."""
        return self.journal

    def get_state(self) -> dict[str, Any] | None:
        """Return the raw final StateGraph state dictionary."""
        return self.curr_state

    def save_reports(
        self, final_state: dict[str, Any], pair: str, save_path: Path | None = None, trade_date: str | None = None
    ) -> Path:
        """Write the markdown report tree for a completed Forex run."""
        resolved_date = trade_date or (final_state.get("trade_date") if isinstance(final_state, dict) else None)
        if save_path is None:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            save_path = (
                Path(self.config["results_dir"])
                / "forex_reports"
                / f"{safe_ticker_component(pair)}_{stamp}"
            )
        return write_report_tree(final_state, pair, save_path, trade_date=resolved_date)

    def checkpoint_input(self, initial_state: dict[str, Any]) -> dict[str, Any]:
        """Return input for graph execution matching TradingAgentsGraph interface."""
        return initial_state

    def begin_checkpoint(self, *args: Any, **kwargs: Any) -> str | None:
        """Checkpointing hook matching TradingAgentsGraph interface."""
        return None

    def clear_checkpoint_on_success(self, *args: Any, **kwargs: Any) -> None:
        """Checkpoint cleanup hook matching TradingAgentsGraph interface."""
        pass
