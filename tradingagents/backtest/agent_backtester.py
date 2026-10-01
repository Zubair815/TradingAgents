"""Real Historical Agent Backtesting Engine (Phase 20).

Provides point-in-time (PIT) safe historical simulation with multi-agent evaluation:
1. At historical timestamp T, only candles strictly <= T are provided to the pipeline.
2. Historical macro and calendar data only release figures published <= T.
3. Historical news data only exposes items with published_at <= T without future revisions.
4. Execution simulator enforces spread, commission, slippage, and overnight swap drag.
5. Respects MARKET, LIMIT, and STOP orders with proposal expiration.
6. Ambiguous intrabar collisions are resolved via sub-resolution bars or conservative assumption.
7. Cost control via sampling interval, max analysis points, model selection, and pre-launch estimation.
8. Distinguishes DEMO vs HISTORICAL_AGENT_BACKTEST modes.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Sequence
from contextlib import nullcontext
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta
from math import isfinite
from typing import Any

from tradingagents.agents.schemas_forex import (
    ForexAction,
    ForexRiskDecision,
    ForexRiskDecisionAction,
    ForexTraderProposal,
)
from tradingagents.backtest.forex_engine import (
    ForexBacktestConfig,
    ForexBacktestEngine,
    ForexBacktestResult,
)
from tradingagents.backtest.historical_data import (
    HistoricalDataUnavailable,
    candle_frame,
    validate_historical_input,
)
from tradingagents.database.journal import ForexTradeJournal
from tradingagents.database.models import TradeExitReason
from tradingagents.dataflows.forex_context import historical_market_scope
from tradingagents.dataflows.forex_data import ForexBar
from tradingagents.forex.conversion import (
    ForexConversionRate,
    FXConversionUnavailable,
    resolve_conversion_rate,
)
from tradingagents.forex.domain import Timeframe, normalize_forex_pair

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration and Estimate Models
# ---------------------------------------------------------------------------


@dataclass
class AgentBacktestConfig:
    """Institutional configuration for historical agent backtesting."""

    pair: str = "EURUSD"
    mode: str = "HISTORICAL_AGENT_BACKTEST"
    timeframe: str = "H1"
    date_from: str | None = None
    date_to: str | None = None
    sampling_interval: int = 1  # Evaluate agents every N bars
    max_analysis_points: int | None = None  # Cap total AI evaluations to control LLM cost
    analyst_selection: list[str] = field(
        default_factory=lambda: ["forex_technical", "forex_macro", "forex_news"]
    )
    provider: str = "openai"
    quick_model: str = "gpt-4.1-mini"
    deep_model: str = "gpt-4.1"
    token_limits: int | None = None
    research_depth: str = "standard"
    backtest_config: ForexBacktestConfig = field(default_factory=ForexBacktestConfig)
    conversion_rates: tuple[ForexConversionRate, ...] = ()
    max_conversion_age: timedelta | None = None


@dataclass
class AgentBacktestEstimate:
    """Pre-launch resource and financial estimation for historical AI runs."""

    total_bars: int
    sampling_interval: int
    max_analysis_points: int | None
    expected_analyses_count: int
    estimated_llm_calls: int
    estimated_tokens: int
    estimated_cost_usd: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_bars": self.total_bars,
            "sampling_interval": self.sampling_interval,
            "max_analysis_points": self.max_analysis_points,
            "expected_analyses_count": self.expected_analyses_count,
            "estimated_llm_calls": self.estimated_llm_calls,
            "estimated_tokens": self.estimated_tokens,
            "estimated_cost_usd": self.estimated_cost_usd,
        }


def estimate_agent_analyses(
    total_bars: int,
    sampling_interval: int = 1,
    max_analysis_points: int | None = None,
    analyst_count: int = 3,
    avg_tokens_per_analysis: int = 4000,
    cost_per_1k_tokens: float = 0.003,
) -> AgentBacktestEstimate:
    """Compute deterministic pre-launch estimates of LLM invocations and costs."""
    interval = max(1, sampling_interval)
    raw_points = total_bars // interval
    if max_analysis_points is not None and max_analysis_points > 0:
        expected = min(raw_points, max_analysis_points)
    else:
        expected = raw_points

    calls_per_run = analyst_count + 3  # Analysts + Bull/Bear + Manager + Trader + Risk
    total_calls = expected * calls_per_run
    est_tokens = expected * avg_tokens_per_analysis
    est_cost = round((est_tokens / 1000.0) * cost_per_1k_tokens, 2)

    return AgentBacktestEstimate(
        total_bars=total_bars,
        sampling_interval=interval,
        max_analysis_points=max_analysis_points,
        expected_analyses_count=expected,
        estimated_llm_calls=total_calls,
        estimated_tokens=est_tokens,
        estimated_cost_usd=est_cost,
    )


# ---------------------------------------------------------------------------
# Output Report Model
# ---------------------------------------------------------------------------


@dataclass
class HistoricalAgentBacktestReport:
    """Complete institutional audit report for historical agent backtesting."""

    backtest_id: str
    mode: str = "HISTORICAL_AGENT_BACKTEST"
    validated_strategy_performance: bool = False
    validation_status: str = "INSUFFICIENT_DATA"
    validation_reasons: list[str] = field(default_factory=list)
    market_data_provenance: dict[str, Any] = field(default_factory=dict)
    audit: dict[str, Any] = field(default_factory=dict)
    pair: str = "EURUSD"
    timeframe: str = "H1"
    start_date: str = "N/A"
    end_date: str = "N/A"
    result: ForexBacktestResult | None = None
    analyses_performed: int = 0
    proposals_generated: int = 0
    proposals_approved: int = 0
    proposals_rejected: int = 0
    proposals_modified: int = 0
    proposals_skipped: int = 0
    markdown_report: str = ""
    cost_control_summary: dict[str, Any] = field(default_factory=dict)
    execution_summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "backtest_id": self.backtest_id,
            "mode": self.mode,
            "validated_strategy_performance": self.validated_strategy_performance,
            "validation_status": self.validation_status,
            "validation_reasons": self.validation_reasons,
            "market_data_source": self.market_data_provenance.get("source", "unverified"),
            "market_data_provenance": self.market_data_provenance,
            **self.audit,
            "pair": self.pair,
            "timeframe": self.timeframe,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "analyses_performed": self.analyses_performed,
            "proposals_generated": self.proposals_generated,
            "proposals_approved": self.proposals_approved,
            "proposals_rejected": self.proposals_rejected,
            "proposals_modified": self.proposals_modified,
            "proposals_skipped": self.proposals_skipped,
            "cost_control_summary": self.cost_control_summary,
            "execution_summary": self.execution_summary,
            "markdown_report": self.markdown_report,
            "result": self.result.__dict__ if self.result else None,
        }


# ---------------------------------------------------------------------------
# Historical Agent Backtest Engine
# ---------------------------------------------------------------------------


class HistoricalForexAgentBacktester:
    """Historical Agent Backtesting Engine with strict PIT and cost controls."""

    def __init__(
        self,
        config: AgentBacktestConfig | None = None,
        journal: ForexTradeJournal | None = None,
        graph_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.config = config or AgentBacktestConfig()
        self.journal = journal
        self.graph_factory = graph_factory

    def estimate(self, total_bars: int) -> AgentBacktestEstimate:
        """Estimate required resources before starting the simulation."""
        return estimate_agent_analyses(
            total_bars=total_bars,
            sampling_interval=self.config.sampling_interval,
            max_analysis_points=self.config.max_analysis_points,
            analyst_count=len(self.config.analyst_selection),
        )

    def _require_historical_quote_conversion(self, pair: str, cutoff: datetime) -> None:
        quote_currency = pair[3:6]
        account_currency = self.config.backtest_config.account_currency.upper()
        try:
            resolve_conversion_rate(
                quote_currency,
                account_currency,
                self.config.conversion_rates,
                as_of_utc=cutoff,
                max_age=self.config.max_conversion_age,
            )
        except FXConversionUnavailable as exc:
            raise HistoricalDataUnavailable(
                f"historical conversion {quote_currency}->{account_currency} "
                f"is unavailable at {cutoff.isoformat()}"
            ) from exc

    def run(
        self,
        candles: Sequence[ForexBar],
        lower_tf_candles: Sequence[ForexBar] | None = None,
        agent_pipeline_callable: Callable[[str, datetime, list[ForexBar]], ForexTraderProposal | None] | None = None,
        market_data_provenance: dict[str, Any] | None = None,
        lower_tf_provenance: dict[str, Any] | None = None,
    ) -> HistoricalAgentBacktestReport:
        """Run full bar-by-bar historical agent simulation.

        Guarantees:
        - Strict PIT safety: at bar T, agent receives ONLY candles strictly <= T.
        - Future bars AFTER T are evaluated solely by the execution simulator.
        - Respects MARKET, LIMIT, STOP with expiry.
        - Respects spread, slippage, commission, swap.
        - Resolves same-bar SL/TP collisions with lower timeframe data or conservative stops.
        """
        pair = normalize_forex_pair(self.config.pair)
        backtest_id = f"bt_agent_{uuid.uuid4().hex[:10]}"
        if self.config.mode not in ("DEMO", "HISTORICAL_AGENT_BACKTEST"):
            raise ValueError("Unknown backtest mode")
        historical = self.config.mode == "HISTORICAL_AGENT_BACKTEST"
        provenance = {}
        if historical:
            costs = self.config.backtest_config
            if any(not isfinite(v) or v < 0 for v in (
                costs.default_spread_pips, costs.default_slippage_pips,
                costs.commission_per_lot_usd, costs.swap_per_day_usd,
            )) or not isfinite(costs.leverage) or costs.leverage <= 0:
                raise ValueError("finite nonnegative costs and positive leverage are required")
            costs.execution_timeframe = self.config.timeframe
            provenance = validate_historical_input(
                candles, market_data_provenance, pair, self.config.timeframe,
                self.config.date_from, self.config.date_to,
            )
            if lower_tf_candles:
                lower_meta = lower_tf_provenance or {}
                lower_tf = lower_meta.get("timeframe")
                if not lower_tf or Timeframe.from_string(lower_tf).seconds >= Timeframe.from_string(self.config.timeframe).seconds:
                    raise HistoricalDataUnavailable("verified lower timeframe provenance is required")
                validate_historical_input(lower_tf_candles, lower_meta, pair, lower_tf,
                                          provenance["actual_start"], provenance["actual_end"])
                if lower_meta["source"] != provenance["source"] or lower_meta["symbol"] != provenance["symbol"]:
                    raise HistoricalDataUnavailable("lower timeframe execution source mismatch")
        if not candles:
            raise HistoricalDataUnavailable("no candles provided")
        if agent_pipeline_callable is None and self.graph_factory is None:
            raise HistoricalDataUnavailable("analysis pipeline is required")

        # Sort candles strictly chronologically
        sorted_candles = sorted(candles, key=lambda c: c.timestamp)
        n_bars = len(sorted_candles)

        # Determine sampling analysis timestamps
        interval = max(1, self.config.sampling_interval)
        candidate_indices = list(range(0, n_bars, interval))
        if self.config.max_analysis_points is not None and self.config.max_analysis_points > 0:
            analysis_indices = set(candidate_indices[: self.config.max_analysis_points])
        else:
            analysis_indices = set(candidate_indices)

        engine = ForexBacktestEngine(
            config=self.config.backtest_config,
            journal=self.journal,
            conversion_rates=self.config.conversion_rates,
            max_conversion_age=self.config.max_conversion_age,
        )
        engine.reset()

        analyses_performed = 0
        proposals_generated = 0
        proposals_approved = 0
        proposals_rejected = 0
        proposals_modified = 0
        proposals_skipped = 0

        history_window: list[ForexBar] = []

        for i, candle in enumerate(sorted_candles):
            if historical:
                # Settle the completed bar before acting on information from its close.
                engine.step(candle=candle, pair=pair, lower_tf_candles=lower_tf_candles)
            history_window.append(candle)
            new_proposals: list[ForexTraderProposal] = []

            if i in analysis_indices:
                pit_candles = list(history_window)  # Strictly <= current timestamp
                cutoff = candle.close_time if historical else candle.timestamp
                proposal: ForexTraderProposal | None = None
                graph_risk_action = None

                # 1. User/Test-injected callable
                if agent_pipeline_callable is not None:
                    snapshot_setter = getattr(agent_pipeline_callable, "set_account_snapshot", None)
                    if callable(snapshot_setter):
                        snapshot_setter(engine.account_snapshot())
                    context_setter = getattr(agent_pipeline_callable, "set_deterministic_snapshot", None)
                    if callable(context_setter):
                        context_setter(
                            engine.account_snapshot(),
                            engine.open_position_snapshot(cutoff),
                            tuple(
                                rate
                                for rate in self.config.conversion_rates
                                if rate.observed_at is not None and rate.observed_at <= cutoff
                            ),
                            cutoff,
                        )
                    if historical:
                        with historical_market_scope(pair, candle_frame(pit_candles, provenance), cutoff):
                            proposal = agent_pipeline_callable(pair, cutoff, pit_candles)
                    else:
                        proposal = agent_pipeline_callable(pair, cutoff, pit_candles)
                # 2. Graph factory invocation
                elif self.graph_factory is not None:
                    scope = historical_market_scope(pair, candle_frame(pit_candles, provenance), cutoff) if historical else nullcontext()
                    with scope:
                        # Calendar coverage is mandatory for deterministic event risk.
                        from tradingagents.dataflows.trading_economics import (
                            TradingEconomicsCalendar,
                        )
                        if historical:
                            TradingEconomicsCalendar().query(
                                pair, (cutoff - timedelta(days=7)).date().isoformat(),
                                (cutoff + timedelta(days=7)).date().isoformat(), as_of=cutoff,
                            )
                        graph = self.graph_factory()
                        final_state, signal = graph.run(
                            pair=pair,
                            trade_date=cutoff.isoformat(),
                            execution_timeframe=self.config.timeframe,
                        )
                        proposal = final_state.get("forex_proposal")
                        if proposal is None:
                            raise ValueError("historical graph did not produce a typed decision")
                        if isinstance(proposal, dict):
                            proposal = ForexTraderProposal.model_validate(proposal)
                        decision = ForexRiskDecision.model_validate(final_state.get("forex_risk_decision"))
                        if decision.pair != pair or proposal.pair != pair:
                            raise ValueError("historical graph returned a different pair")
                        graph_risk_action = decision.decision
                        if (decision.approved_action != ForexAction.NO_TRADE
                                and decision.decision != ForexRiskDecisionAction.REJECT
                                and (not decision.approved_lot_size or decision.approved_lot_size <= 0)):
                            raise ValueError("historical graph did not authorize a positive lot size")
                        updates = {"action": decision.approved_action, "suggested_lot_size": decision.approved_lot_size}
                        for key, value in (("entry_price", decision.entry_price), ("stop_loss", decision.stop_loss),
                                           ("take_profit_1", decision.take_profit)):
                            if value is not None:
                                updates[key] = value
                        proposal = proposal.model_copy(update=updates)
                analyses_performed += 1

                if proposal is not None:
                    proposals_generated += 1
                    # Check risk action
                    action_val = graph_risk_action or getattr(proposal, "risk_action", ForexRiskDecisionAction.APPROVE)
                    if hasattr(action_val, "value"):
                        action_str = action_val.value
                    else:
                        action_str = str(action_val)

                    if action_str == "APPROVE":
                        if historical and proposal.action != ForexAction.NO_TRADE:
                            self._require_historical_quote_conversion(pair, cutoff)
                        proposals_approved += 1
                        new_proposals.append(proposal)
                    elif action_str == "MODIFY":
                        if historical and proposal.action != ForexAction.NO_TRADE:
                            self._require_historical_quote_conversion(pair, cutoff)
                        proposals_modified += 1
                        new_proposals.append(proposal)
                    elif action_str == "REJECT":
                        proposals_rejected += 1
                    else:
                        proposals_skipped += 1

            # Discrete step execution on incoming bar
            if historical:
                # Zero-latency close-price fill assumption, with normal costs and constraints.
                execution_bar = replace(candle, timestamp=candle.close_time,
                                        open=candle.close, high=candle.close, low=candle.close)
                for proposal in new_proposals:
                    engine.execute_proposal(proposal, execution_bar)
            else:
                engine.step(candle=candle, pair=pair, new_proposals=new_proposals or None,
                            lower_tf_candles=lower_tf_candles)

        # Force settlement on last bar
        if engine.open_trades and sorted_candles:
            last_bar = sorted_candles[-1]
            for t in list(engine.open_trades):
                engine._settle_trade(t, last_bar.close, last_bar.close_time if historical else last_bar.timestamp, TradeExitReason.MANUAL)
            engine.open_trades = []
            if historical:
                engine.equity = engine.balance

        start_str = sorted_candles[0].timestamp.strftime("%Y-%m-%d %H:%M")
        end_str = (sorted_candles[-1].close_time if historical else sorted_candles[-1].timestamp).strftime("%Y-%m-%d %H:%M")
        result = engine._build_result(start_str, end_str)

        # Set mode and validation flags
        result.mode = self.config.mode
        result.validated_strategy_performance = False
        reasons = (["Historical input integrity checked; statistical strategy validation has not been performed.",
                    "LLM pretrained knowledge cannot be proven point-in-time.",
                    "Macro series unavailable without verified intraday archives; news uses observed archives only."]
                   if historical else ["Illustrative/demo only; candle provenance is unverified."])
        result.validation_status = "PARTIALLY_VALIDATED" if historical else "DEMO"
        result.validation_reasons = reasons

        # Render Markdown Dashboard
        cost_control_meta = {
            "total_bars": n_bars,
            "sampling_interval": interval,
            "max_analysis_points": self.config.max_analysis_points,
            "analyses_performed": analyses_performed,
            "analyst_selection": self.config.analyst_selection,
            "provider": self.config.provider,
            "quick_model": self.config.quick_model,
            "deep_model": self.config.deep_model,
            "token_limits": self.config.token_limits,
            "research_depth": self.config.research_depth,
        }

        execution_meta = {
            "total_trades": result.total_trades,
            "winning_trades": result.winning_trades,
            "losing_trades": result.losing_trades,
            "win_rate_pct": result.win_rate_pct,
            "profit_factor": result.profit_factor,
            "expectancy_r": result.expectancy_r,
            "max_drawdown_pct": result.max_drawdown_pct,
            "total_net_profit": result.total_net_profit,
            "total_friction_usd": result.total_friction_usd,
            "total_swap_cost_usd": result.total_swap_cost_usd,
            "pending_orders_count": result.pending_orders_count,
            "filled_orders_count": result.filled_orders_count,
            "expired_orders_count": result.expired_orders_count,
            "ambiguous_trades_count": result.ambiguous_trades_count,
        }

        report_md = self._render_report_markdown(
            report_id=backtest_id,
            pair=pair,
            start_date=start_str,
            end_date=end_str,
            result=result,
            cost_meta=cost_control_meta,
            analyses_performed=analyses_performed,
            proposals_gen=proposals_generated,
            proposals_app=proposals_approved,
        )
        report_md += "\n\n" + "\n".join(f"- {reason}" for reason in reasons)
        if historical:
            report_md += (f"\n- Source: {provenance['source']} / {provenance['symbol']}"
                          f"; execution market: {provenance['execution_market']}"
                          f"\n- Coverage: {provenance['actual_start']} to {provenance['actual_end']}"
                          f"\n- Retrieved: {provenance['retrieved_at_utc']}")

        return HistoricalAgentBacktestReport(
            backtest_id=backtest_id,
            mode=self.config.mode,
            validated_strategy_performance=False,
            validation_status="PARTIALLY_VALIDATED" if historical else "DEMO",
            validation_reasons=reasons,
            market_data_provenance=provenance,
            audit={"analysis_cutoff_policy": "completed candles only; analysis and fills at bar close" if historical else "demo only",
                   "analysts": self.config.analyst_selection, "provider": self.config.provider,
                   "quick_model": self.config.quick_model, "deep_model": self.config.deep_model,
                   "strategy_version": "historical-agent-phase30", "prompt_system_version": None,
                   "execution_assumptions": {k: v for k, v in asdict(self.config.backtest_config).items() if k != "results_dir"},
                   "evidence_policy": "calendar mandatory for graph; news archive optional; unverified intraday macro unavailable"},
            pair=pair,
            timeframe=self.config.timeframe,
            start_date=start_str,
            end_date=end_str,
            result=result,
            analyses_performed=analyses_performed,
            proposals_generated=proposals_generated,
            proposals_approved=proposals_approved,
            proposals_rejected=proposals_rejected,
            proposals_modified=proposals_modified,
            proposals_skipped=proposals_skipped,
            markdown_report=report_md,
            cost_control_summary=cost_control_meta,
            execution_summary=execution_meta,
        )

    def _render_report_markdown(
        self,
        report_id: str,
        pair: str,
        start_date: str,
        end_date: str,
        result: ForexBacktestResult,
        cost_meta: dict[str, Any],
        analyses_performed: int,
        proposals_gen: int,
        proposals_app: int,
    ) -> str:
        """Build institutional markdown report."""
        lines = [
            "# Real Historical Agent Backtest Report",
            f"**Run ID:** `{report_id}` | **Mode:** `{self.config.mode}` | **Validated:** `False`",
            "Historical simulation is not statistical validation of strategy performance." if self.config.mode != "DEMO" else "Illustrative/demo only.",
            f"**Pair:** `{pair}` | **Period:** {start_date} to {end_date} | **Timeframe:** `{self.config.timeframe}`",
            "",
            "## 1. Agent Evaluation & Cost Control",
            "",
            "| Parameter | Value | Description |",
            "|:---|:---:|:---|",
            f"| **Sampling Interval** | `{cost_meta['sampling_interval']}` bars | Bar frequency between AI analyses |",
            f"| **Max Analysis Cap** | `{cost_meta['max_analysis_points'] or 'None'}` | Safety ceiling for LLM invocations |",
            f"| **Analyses Performed** | `{analyses_performed}` | Actual point-in-time AI evaluations |",
            f"| **Proposals Generated** | `{proposals_gen}` | Trade setups proposed by Forex Trader |",
            f"| **Proposals Approved** | `{proposals_app}` | Setups cleared by Risk Engine |",
            f"| **LLM Models** | `{cost_meta['quick_model']}` / `{cost_meta['deep_model']}` | Quick & Deep thinking models |",
            "",
            result.render_markdown_report(),
        ]
        return "\n".join(lines)
