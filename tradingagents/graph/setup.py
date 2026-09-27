# TradingAgents/graph/setup.py

from typing import Any

from langchain_core.runnables import Runnable, RunnableConfig, RunnableLambda
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from tradingagents.agents import (
    create_aggressive_debater,
    create_bear_researcher,
    create_bull_researcher,
    create_conservative_debater,
    create_forex_macro_analyst,
    create_forex_news_analyst,
    create_forex_technical_analyst,
    create_fundamentals_analyst,
    create_market_analyst,
    create_msg_delete,
    create_neutral_debater,
    create_news_analyst,
    create_portfolio_manager,
    create_research_manager,
    create_sentiment_analyst,
    create_trader,
)
from tradingagents.agents.utils.agent_states import AgentState
from tradingagents.dataflows.config import config_scope

from .analyst_execution import build_analyst_execution_plan
from .conditional_logic import ConditionalLogic

# Every target a shared conditional router can return. Each edge driven by the
# router maps all of them, so a fall-through return (e.g. under prompt/i18n/
# refactor drift in the speaker labels) can never hit a missing path_map entry
# and crash LangGraph mid-run (#1088).
DEBATE_PATH_MAP = {
    "Bull Researcher": "Bull Researcher",
    "Bear Researcher": "Bear Researcher",
    "Research Manager": "Research Manager",
}
RISK_ANALYSIS_PATH_MAP = {
    "Aggressive Analyst": "Aggressive Analyst",
    "Conservative Analyst": "Conservative Analyst",
    "Neutral Analyst": "Neutral Analyst",
    "Portfolio Manager": "Portfolio Manager",
}


class GraphSetup:
    """Handles the setup and configuration of the agent graph."""

    def __init__(
        self,
        quick_thinking_llm: Any,
        deep_thinking_llm: Any,
        tool_nodes: dict[str, ToolNode],
        conditional_logic: ConditionalLogic,
        config: dict | None = None,
    ):
        """Initialize with required components."""
        self.quick_thinking_llm = quick_thinking_llm
        self.deep_thinking_llm = deep_thinking_llm
        self.tool_nodes = tool_nodes
        self.conditional_logic = conditional_logic
        self.config = config

    def _bind_config(self, node):
        """Bind every node, including tools invoked by direct graph.stream()."""
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
        self, selected_analysts=("market", "social", "news", "fundamentals")
    ):
        """Set up and compile the agent workflow graph.

        Args:
            selected_analysts (list): List of analyst types to include. Options are:
                - "market": Market analyst
                - "social": Social media analyst
                - "news": News analyst
                - "fundamentals": Fundamentals analyst
        """
        plan = build_analyst_execution_plan(selected_analysts)

        analyst_factories = {
            "market": lambda: create_market_analyst(self.quick_thinking_llm),
            "social": lambda: create_sentiment_analyst(self.quick_thinking_llm),
            "news": lambda: create_news_analyst(self.quick_thinking_llm),
            "fundamentals": lambda: create_fundamentals_analyst(self.quick_thinking_llm),
            "forex_technical": lambda: create_forex_technical_analyst(self.quick_thinking_llm),
            "forex_macro": lambda: create_forex_macro_analyst(self.quick_thinking_llm),
            "forex_news": lambda: create_forex_news_analyst(self.quick_thinking_llm),
        }

        # Create researcher and manager nodes
        bull_researcher_node = create_bull_researcher(self.quick_thinking_llm)
        bear_researcher_node = create_bear_researcher(self.quick_thinking_llm)
        research_manager_node = create_research_manager(self.deep_thinking_llm)
        trader_node = create_trader(self.quick_thinking_llm)

        # Create risk analysis nodes
        aggressive_analyst = create_aggressive_debater(self.quick_thinking_llm)
        neutral_analyst = create_neutral_debater(self.quick_thinking_llm)
        conservative_analyst = create_conservative_debater(self.quick_thinking_llm)
        portfolio_manager_node = create_portfolio_manager(self.deep_thinking_llm)

        # Create workflow
        workflow = StateGraph(AgentState)

        # Add analyst nodes to the graph
        for spec in plan.specs:
            workflow.add_node(spec.agent_node, self._bind_config(analyst_factories[spec.key]()))
            workflow.add_node(spec.clear_node, self._bind_config(create_msg_delete()))
            workflow.add_node(spec.tool_node, self._bind_config(self.tool_nodes[spec.key]))

        # Add other nodes
        workflow.add_node("Bull Researcher", self._bind_config(bull_researcher_node))
        workflow.add_node("Bear Researcher", self._bind_config(bear_researcher_node))
        workflow.add_node("Research Manager", self._bind_config(research_manager_node))
        workflow.add_node("Trader", self._bind_config(trader_node))
        workflow.add_node("Aggressive Analyst", self._bind_config(aggressive_analyst))
        workflow.add_node("Neutral Analyst", self._bind_config(neutral_analyst))
        workflow.add_node("Conservative Analyst", self._bind_config(conservative_analyst))
        workflow.add_node("Portfolio Manager", self._bind_config(portfolio_manager_node))

        # Define edges
        # Start with the first analyst
        workflow.add_edge(START, plan.specs[0].agent_node)

        # Connect analysts in sequence
        for i, spec in enumerate(plan.specs):
            current_analyst = spec.agent_node
            current_tools = spec.tool_node
            current_clear = spec.clear_node

            # Add conditional edges for current analyst
            workflow.add_conditional_edges(
                current_analyst,
                getattr(self.conditional_logic, f"should_continue_{spec.key}"),
                [current_tools, current_clear],
            )
            workflow.add_edge(current_tools, current_analyst)

            # Connect to next analyst or to Bull Researcher if this is the last analyst
            if i < len(plan.specs) - 1:
                workflow.add_edge(current_clear, plan.specs[i + 1].agent_node)
            else:
                workflow.add_edge(current_clear, "Bull Researcher")

        # Both research-debate edges share the complete DEBATE_PATH_MAP (#1088).
        for debate_node in ("Bull Researcher", "Bear Researcher"):
            workflow.add_conditional_edges(
                debate_node,
                self.conditional_logic.should_continue_debate,
                DEBATE_PATH_MAP,
            )
        workflow.add_edge("Research Manager", "Trader")
        workflow.add_edge("Trader", "Aggressive Analyst")
        # All three risk edges share the complete RISK_ANALYSIS_PATH_MAP (#1088).
        for risk_node in ("Aggressive Analyst", "Conservative Analyst", "Neutral Analyst"):
            workflow.add_conditional_edges(
                risk_node,
                self.conditional_logic.should_continue_risk_analysis,
                RISK_ANALYSIS_PATH_MAP,
            )

        workflow.add_edge("Portfolio Manager", END)

        return workflow
