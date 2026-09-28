"""Forex Multi-Agent & Component Ablation Testing Engine (Phase 22).

Evaluates the marginal alpha contribution, robustness, execution efficiency,
and LLM resource consumption of individual system components over the EXACT
same historical candle sample:

1. Analyst Selection:
   - Technical only
   - Technical + Macro
   - Technical + News
   - Technical + Macro + News (Full System)
2. Debate Mechanism:
   - With debate (Bull/Bear adversarial interaction)
   - Without debate (Direct aggregation)
3. Structured Memory:
   - With memory (Contextual lesson injection)
   - Without memory (Zero memory context)
4. Model Tiering:
   - Quick Model A (e.g., gpt-4.1-mini)
   - Quick Model B (e.g., claude-3-5-haiku)

Comparison Metrics:
- Expectancy (in R)
- Profit Factor
- Maximum Drawdown (%)
- Average R
- Trade Count
- Estimated LLM Cost ($)
- Latency (execution time)

Sample Size Protection:
- Explicitly enforces statistical threshold rules:
  "Do not declare a winner based on tiny samples."
- Flags exploratory small-sample studies and disables winner declaration
  when trade counts fall below statistical thresholds (default: 30 trades).
"""

from __future__ import annotations

import copy
import logging
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from tradingagents.agents.schemas_forex import (
    ForexRiskDecisionAction,
    ForexTraderProposal,
)
from tradingagents.backtest.agent_backtester import (
    AgentBacktestConfig,
    HistoricalForexAgentBacktester,
)
from tradingagents.backtest.forex_engine import (
    ForexBacktestConfig,
    ForexBacktestEngine,
    ForexBacktestResult,
)
from tradingagents.database.journal import ForexTradeJournal
from tradingagents.database.models import TradeExitReason
from tradingagents.dataflows.forex_data import ForexBar
from tradingagents.forex.domain import normalize_forex_pair

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration Models
# ---------------------------------------------------------------------------


@dataclass
class AblationConfigVariant:
    """Specification of an ablated system configuration variant."""

    variant_id: str
    name: str
    description: str
    analyst_selection: list[str] = field(
        default_factory=lambda: ["forex_technical", "forex_macro", "forex_news"]
    )
    enable_debate: bool = True
    enable_memory: bool = True
    quick_model: str = "gpt-4.1-mini"
    deep_model: str = "gpt-4.1"
    provider: str = "openai"
    sampling_interval: int = 1
    max_analysis_points: int | None = None
    avg_tokens_per_call: int = 1200
    cost_per_1k_tokens: float = 0.003
    extra_params: dict[str, Any] = field(default_factory=dict)

    def estimate_calls_per_point(self) -> int:
        """Estimate number of LLM API calls required per evaluation point."""
        count = len(self.analyst_selection)
        if self.enable_debate:
            count += 2  # Bull and Bear
        if self.enable_memory:
            count += 1  # Memory lookup/synthesis
        count += 2  # Manager + Trader
        return max(1, count)


def create_standard_ablation_matrix(
    quick_model_a: str = "gpt-4.1-mini",
    quick_model_b: str = "claude-3-5-haiku",
    base_provider: str = "openai",
    sampling_interval: int = 1,
    max_analysis_points: int | None = None,
) -> list[AblationConfigVariant]:
    """Generate the standard suite of Phase 22 ablation variants."""
    full_analysts = ["forex_technical", "forex_macro", "forex_news"]

    return [
        # Baseline: Full System
        AblationConfigVariant(
            variant_id="baseline_full",
            name="Full System (Tech + Macro + News + Debate + Memory)",
            description="Complete unablated institutional multi-agent pipeline with debate and memory.",
            analyst_selection=list(full_analysts),
            enable_debate=True,
            enable_memory=True,
            quick_model=quick_model_a,
            provider=base_provider,
            sampling_interval=sampling_interval,
            max_analysis_points=max_analysis_points,
        ),
        # 1. Analyst Selection Variations
        AblationConfigVariant(
            variant_id="tech_only",
            name="Technical Only",
            description="Pure technical momentum, market structure, and indicator analysis without macro or news.",
            analyst_selection=["forex_technical"],
            enable_debate=True,
            enable_memory=True,
            quick_model=quick_model_a,
            provider=base_provider,
            sampling_interval=sampling_interval,
            max_analysis_points=max_analysis_points,
        ),
        AblationConfigVariant(
            variant_id="tech_macro",
            name="Technical + Macro",
            description="Technical price action combined with central bank rate differentials, without news sentiment.",
            analyst_selection=["forex_technical", "forex_macro"],
            enable_debate=True,
            enable_memory=True,
            quick_model=quick_model_a,
            provider=base_provider,
            sampling_interval=sampling_interval,
            max_analysis_points=max_analysis_points,
        ),
        AblationConfigVariant(
            variant_id="tech_news",
            name="Technical + News",
            description="Technical setups filtered by breaking market news and sentiment, omitting macro carry.",
            analyst_selection=["forex_technical", "forex_news"],
            enable_debate=True,
            enable_memory=True,
            quick_model=quick_model_a,
            provider=base_provider,
            sampling_interval=sampling_interval,
            max_analysis_points=max_analysis_points,
        ),
        AblationConfigVariant(
            variant_id="tech_macro_news",
            name="Technical + Macro + News",
            description="All three analytical domains active simultaneously.",
            analyst_selection=list(full_analysts),
            enable_debate=True,
            enable_memory=True,
            quick_model=quick_model_a,
            provider=base_provider,
            sampling_interval=sampling_interval,
            max_analysis_points=max_analysis_points,
        ),
        # 2. Debate Mechanism Variations
        AblationConfigVariant(
            variant_id="with_debate",
            name="With Debate",
            description="Adversarial Bull vs Bear debate synthesis enabled.",
            analyst_selection=list(full_analysts),
            enable_debate=True,
            enable_memory=True,
            quick_model=quick_model_a,
            provider=base_provider,
            sampling_interval=sampling_interval,
            max_analysis_points=max_analysis_points,
        ),
        AblationConfigVariant(
            variant_id="without_debate",
            name="Without Debate",
            description="Bypasses Bull/Bear adversarial debate; direct aggregation of analyst viewpoints.",
            analyst_selection=list(full_analysts),
            enable_debate=False,
            enable_memory=True,
            quick_model=quick_model_a,
            provider=base_provider,
            sampling_interval=sampling_interval,
            max_analysis_points=max_analysis_points,
        ),
        # 3. Structured Memory Variations
        AblationConfigVariant(
            variant_id="with_memory",
            name="With Memory",
            description="Contextual post-trade lesson retrieval active.",
            analyst_selection=list(full_analysts),
            enable_debate=True,
            enable_memory=True,
            quick_model=quick_model_a,
            provider=base_provider,
            sampling_interval=sampling_interval,
            max_analysis_points=max_analysis_points,
        ),
        AblationConfigVariant(
            variant_id="without_memory",
            name="Without Memory",
            description="Zero historical lesson memory context injected into research manager or trader.",
            analyst_selection=list(full_analysts),
            enable_debate=True,
            enable_memory=False,
            quick_model=quick_model_a,
            provider=base_provider,
            sampling_interval=sampling_interval,
            max_analysis_points=max_analysis_points,
        ),
        # 4. Model Selection Variations
        AblationConfigVariant(
            variant_id="model_quick_a",
            name=f"Quick Model A ({quick_model_a})",
            description=f"Standard primary reasoning model: {quick_model_a}.",
            analyst_selection=list(full_analysts),
            enable_debate=True,
            enable_memory=True,
            quick_model=quick_model_a,
            provider=base_provider,
            sampling_interval=sampling_interval,
            max_analysis_points=max_analysis_points,
        ),
        AblationConfigVariant(
            variant_id="model_quick_b",
            name=f"Quick Model B ({quick_model_b})",
            description=f"Alternative high-efficiency model: {quick_model_b}.",
            analyst_selection=list(full_analysts),
            enable_debate=True,
            enable_memory=True,
            quick_model=quick_model_b,
            provider="anthropic" if "claude" in quick_model_b.lower() else base_provider,
            sampling_interval=sampling_interval,
            max_analysis_points=max_analysis_points,
        ),
    ]


# ---------------------------------------------------------------------------
# Metric and Report Models
# ---------------------------------------------------------------------------


@dataclass
class AblationVariantMetric:
    """Audited comparative metrics for a single ablation variant."""

    variant_id: str
    name: str
    description: str
    trade_count: int
    win_rate_pct: float
    profit_factor: float
    expectancy_r: float
    average_r: float
    max_drawdown_pct: float
    total_net_profit: float
    estimated_llm_cost_usd: float
    latency_seconds: float
    analyses_performed: int
    sample_size_adequate: bool
    delta_expectancy: float = 0.0
    delta_profit_factor: float = 0.0
    delta_drawdown: float = 0.0
    delta_net_profit: float = 0.0
    delta_avg_r: float = 0.0
    delta_cost_usd: float = 0.0
    delta_latency_sec: float = 0.0
    result: ForexBacktestResult | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "variant_id": self.variant_id,
            "name": self.name,
            "description": self.description,
            "trade_count": self.trade_count,
            "win_rate_pct": self.win_rate_pct,
            "profit_factor": self.profit_factor,
            "expectancy_r": self.expectancy_r,
            "average_r": self.average_r,
            "max_drawdown_pct": self.max_drawdown_pct,
            "total_net_profit": self.total_net_profit,
            "estimated_llm_cost_usd": self.estimated_llm_cost_usd,
            "latency_seconds": self.latency_seconds,
            "analyses_performed": self.analyses_performed,
            "sample_size_adequate": self.sample_size_adequate,
            "delta_expectancy": self.delta_expectancy,
            "delta_profit_factor": self.delta_profit_factor,
            "delta_drawdown": self.delta_drawdown,
            "delta_net_profit": self.delta_net_profit,
            "delta_avg_r": self.delta_avg_r,
            "delta_cost_usd": self.delta_cost_usd,
            "delta_latency_sec": self.delta_latency_sec,
        }


@dataclass
class ForexAblationReport:
    """Consolidated institutional report for multi-variant ablation study."""

    study_id: str
    pair: str
    timeframe: str
    start_date: str
    end_date: str
    total_bars: int
    min_sample_size: int
    baseline_variant_id: str
    variants: list[AblationVariantMetric]
    winner_declared: bool
    winning_variant_id: str | None
    winning_variant_name: str | None
    sample_size_warning: str | None
    component_rankings: list[tuple[str, float]]
    markdown_summary: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "study_id": self.study_id,
            "pair": self.pair,
            "timeframe": self.timeframe,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "total_bars": self.total_bars,
            "min_sample_size": self.min_sample_size,
            "baseline_variant_id": self.baseline_variant_id,
            "winner_declared": self.winner_declared,
            "winning_variant_id": self.winning_variant_id,
            "winning_variant_name": self.winning_variant_name,
            "sample_size_warning": self.sample_size_warning,
            "component_rankings": self.component_rankings,
            "variants": [v.to_dict() for v in self.variants],
            "markdown_summary": self.markdown_summary,
        }


# ---------------------------------------------------------------------------
# Ablation Execution Runner
# ---------------------------------------------------------------------------


class ForexAblationRunner:
    """Orchestrates comparative ablation experiments over identical historical data."""

    def __init__(
        self,
        pair: str = "EURUSD",
        timeframe: str = "H1",
        backtest_config: ForexBacktestConfig | None = None,
        journal: ForexTradeJournal | None = None,
        min_sample_size: int = 30,
        cost_per_1k_tokens: float = 0.003,
        avg_tokens_per_call: int = 1200,
    ) -> None:
        self.pair = normalize_forex_pair(pair)
        self.timeframe = timeframe
        self.backtest_config = backtest_config or ForexBacktestConfig()
        self.journal = journal
        self.min_sample_size = max(5, min_sample_size)
        self.cost_per_1k_tokens = cost_per_1k_tokens
        self.avg_tokens_per_call = avg_tokens_per_call

    def run_ablation(
        self,
        candles: Sequence[ForexBar],
        lower_tf_candles: Sequence[ForexBar] | None = None,
        variants: Sequence[AblationConfigVariant] | None = None,
        baseline_variant_id: str = "baseline_full",
        pipeline_factory: Callable[[AblationConfigVariant], Callable[[str, datetime, list[ForexBar]], ForexTraderProposal | None]] | None = None,
        custom_variant_evaluator: Callable[[AblationConfigVariant, Sequence[ForexBar], Sequence[ForexBar] | None], tuple[ForexBacktestResult, int, float]] | None = None,
    ) -> ForexAblationReport:
        """Run controlled ablation experiment across variants over identical candles.

        Ensures:
        1. All variants see the EXACT same chronological sequence of candles.
        2. Point-in-time safety: bar T receives data <= T only.
        3. Measures: expectancy, profit factor, drawdown, average R, trade count, LLM cost, latency.
        4. Statistically guards against tiny samples: refuses to declare definitive winners on insufficient N.
        """
        study_id = f"ablation_{uuid.uuid4().hex[:10]}"

        if not candles:
            raise ValueError("No historical candles provided for ablation experiment.")

        sorted_candles = sorted(candles, key=lambda c: c.timestamp)
        total_bars = len(sorted_candles)
        start_date = sorted_candles[0].timestamp.strftime("%Y-%m-%d %H:%M")
        end_date = sorted_candles[-1].timestamp.strftime("%Y-%m-%d %H:%M")

        variant_list = list(variants) if variants is not None else create_standard_ablation_matrix()
        if not variant_list:
            raise ValueError("No ablation variants specified.")

        # Identify or default baseline
        baseline_variant = next(
            (v for v in variant_list if v.variant_id == baseline_variant_id),
            variant_list[0],
        )
        baseline_id = baseline_variant.variant_id

        variant_metrics: list[AblationVariantMetric] = []
        baseline_metric: AblationVariantMetric | None = None

        # Execute each variant over the identical dataset
        for var in variant_list:
            t0 = time.perf_counter()

            if custom_variant_evaluator is not None:
                # Direct test/custom evaluator
                b_res, analyses_done, est_cost = custom_variant_evaluator(
                    var, sorted_candles, lower_tf_candles
                )
                latency = round(time.perf_counter() - t0, 3)
            elif pipeline_factory is not None:
                # Built-in backtester using variant-specific pipeline callable
                pipeline_callable = pipeline_factory(var)
                ag_cfg = AgentBacktestConfig(
                    pair=self.pair,
                    timeframe=self.timeframe,
                    sampling_interval=var.sampling_interval,
                    max_analysis_points=var.max_analysis_points,
                    analyst_selection=var.analyst_selection,
                    provider=var.provider,
                    quick_model=var.quick_model,
                    deep_model=var.deep_model,
                    backtest_config=copy.deepcopy(self.backtest_config),
                )
                tester = HistoricalForexAgentBacktester(config=ag_cfg, journal=self.journal)
                rep = tester.run(
                    candles=sorted_candles,
                    lower_tf_candles=lower_tf_candles,
                    agent_pipeline_callable=pipeline_callable,
                )
                b_res = rep.result or ForexBacktestEngine(config=self.backtest_config)._build_empty_result()
                analyses_done = rep.analyses_performed
                latency = round(time.perf_counter() - t0, 3)
                calls_per_pt = var.estimate_calls_per_point()
                total_calls = analyses_done * calls_per_pt
                est_cost = round((total_calls * var.avg_tokens_per_call / 1000.0) * var.cost_per_1k_tokens, 4)
            else:
                # Fallback: run engine with default pipeline simulation
                sim_res, analyses_done, est_cost = self._simulate_variant_run(
                    var, sorted_candles, lower_tf_candles
                )
                b_res = sim_res
                latency = round(time.perf_counter() - t0, 3)

            trade_count = b_res.total_trades
            is_adequate = trade_count >= self.min_sample_size

            metric = AblationVariantMetric(
                variant_id=var.variant_id,
                name=var.name,
                description=var.description,
                trade_count=trade_count,
                win_rate_pct=round(b_res.win_rate_pct, 2),
                profit_factor=round(b_res.profit_factor, 2),
                expectancy_r=round(b_res.expectancy_r, 3),
                average_r=round(b_res.avg_r_multiple, 3),
                max_drawdown_pct=round(b_res.max_drawdown_pct, 2),
                total_net_profit=round(b_res.total_net_profit, 2),
                estimated_llm_cost_usd=round(est_cost, 4),
                latency_seconds=latency,
                analyses_performed=analyses_done,
                sample_size_adequate=is_adequate,
                result=b_res,
            )

            if var.variant_id == baseline_id:
                baseline_metric = metric
            variant_metrics.append(metric)

        # Baseline fallback
        if baseline_metric is None:
            baseline_metric = variant_metrics[0]
            baseline_id = baseline_metric.variant_id

        # Compute deltas vs baseline (Variant - Baseline)
        for m in variant_metrics:
            m.delta_expectancy = round(m.expectancy_r - baseline_metric.expectancy_r, 3)
            m.delta_profit_factor = round(m.profit_factor - baseline_metric.profit_factor, 2)
            m.delta_drawdown = round(m.max_drawdown_pct - baseline_metric.max_drawdown_pct, 2)
            m.delta_net_profit = round(m.total_net_profit - baseline_metric.total_net_profit, 2)
            m.delta_avg_r = round(m.average_r - baseline_metric.average_r, 3)
            m.delta_cost_usd = round(m.estimated_llm_cost_usd - baseline_metric.estimated_llm_cost_usd, 4)
            m.delta_latency_sec = round(m.latency_seconds - baseline_metric.latency_seconds, 3)

        # Component Importance Scores:
        # Measures performance damage when a component is ablated from baseline.
        # Importance = (Baseline Expectancy - Variant Expectancy) + 0.05 * (Variant MDD - Baseline MDD)
        component_rankings: list[tuple[str, float]] = []
        for m in variant_metrics:
            if m.variant_id != baseline_id:
                exp_loss = max(0.0, baseline_metric.expectancy_r - m.expectancy_r)
                dd_increase = max(0.0, m.max_drawdown_pct - baseline_metric.max_drawdown_pct) * 0.05
                score = round(exp_loss + dd_increase, 3)
                component_rankings.append((m.name, score))

        component_rankings.sort(key=lambda x: x[1], reverse=True)

        # -------------------------------------------------------------------
        # Small Sample Protection Gate:
        # "Do not declare a winner based on tiny samples."
        # -------------------------------------------------------------------
        max_trades = max((m.trade_count for m in variant_metrics), default=0)
        winner_declared = False
        winning_variant_id: str | None = None
        winning_variant_name: str | None = None
        sample_size_warning: str | None = None

        if max_trades < self.min_sample_size:
            sample_size_warning = (
                f"Sample size warning: Maximum trade count (N={max_trades}) is below statistical "
                f"significance threshold ({self.min_sample_size} trades). No definitive winner declared."
            )
            winner_declared = False
        else:
            # Only consider variants with adequate sample sizes
            qualified = [m for m in variant_metrics if m.sample_size_adequate]
            if qualified:
                def composite_score(x: AblationVariantMetric) -> float:
                    return (x.expectancy_r * 2.0) + min(x.profit_factor, 5.0) - (x.max_drawdown_pct / 10.0)

                best = max(qualified, key=composite_score)
                winner_declared = True
                winning_variant_id = best.variant_id
                winning_variant_name = best.name
            else:
                sample_size_warning = (
                    f"Sample size warning: No variant achieved the minimum {self.min_sample_size} "
                    f"trades required for audited selection."
                )

        markdown_summary = self._render_markdown_report(
            study_id=study_id,
            pair=self.pair,
            timeframe=self.timeframe,
            start_date=start_date,
            end_date=end_date,
            total_bars=total_bars,
            baseline_name=baseline_metric.name,
            metrics=variant_metrics,
            rankings=component_rankings,
            winner_declared=winner_declared,
            winning_name=winning_variant_name,
            warning=sample_size_warning,
        )

        return ForexAblationReport(
            study_id=study_id,
            pair=self.pair,
            timeframe=self.timeframe,
            start_date=start_date,
            end_date=end_date,
            total_bars=total_bars,
            min_sample_size=self.min_sample_size,
            baseline_variant_id=baseline_id,
            variants=variant_metrics,
            winner_declared=winner_declared,
            winning_variant_id=winning_variant_id,
            winning_variant_name=winning_variant_name,
            sample_size_warning=sample_size_warning,
            component_rankings=component_rankings,
            markdown_summary=markdown_summary,
        )

    def _simulate_variant_run(
        self,
        variant: AblationConfigVariant,
        candles: Sequence[ForexBar],
        lower_tf_candles: Sequence[ForexBar] | None,
    ) -> tuple[ForexBacktestResult, int, float]:
        """Internal deterministic simulation for default ablation execution."""
        engine = ForexBacktestEngine(config=self.backtest_config, journal=self.journal)
        engine.reset()

        interval = max(1, variant.sampling_interval)
        analyses_done = 0

        # Component signal multipliers
        tech_active = "forex_technical" in variant.analyst_selection
        macro_active = "forex_macro" in variant.analyst_selection
        news_active = "forex_news" in variant.analyst_selection

        for i, candle in enumerate(candles):
            proposals: list[ForexTraderProposal] = []
            if i % interval == 0:
                analyses_done += 1
                if variant.max_analysis_points and analyses_done > variant.max_analysis_points:
                    pass
                else:
                    # Synthetic proposal generated deterministically from candle pattern
                    # Filtering applied based on active analysts
                    if tech_active and (i % 8 == 0):
                        # Direction based on close > open
                        is_bull = candle.close >= candle.open
                        # If macro enabled, filter against conflicting macro regime
                        macro_aligned = True if not macro_active else (i % 16 != 0)
                        # If news enabled, filter high-volatility news shocks
                        news_clean = True if not news_active else (i % 24 != 0)

                        if macro_aligned and news_clean:
                            from tradingagents.agents.schemas_forex import ForexAction, OrderType
                            action = ForexAction.LONG if is_bull else ForexAction.SHORT
                            entry = candle.close
                            sl_dist = 0.0020
                            tp_dist = 0.0040 if variant.enable_debate else 0.0030
                            sl = entry - sl_dist if is_bull else entry + sl_dist
                            tp = entry + tp_dist if is_bull else entry - tp_dist

                            prop = ForexTraderProposal(
                                proposal_id=f"prop_abl_{variant.variant_id}_{i}",
                                pair=self.pair,
                                action=action,
                                order_type=OrderType.MARKET,
                                entry_price=entry,
                                stop_loss=sl,
                                take_profit=tp,
                                risk_reward_ratio=round(tp_dist / sl_dist, 2),
                                risk_percent=1.0,
                                setup_type="ABLATION_PULLBACK",
                                execution_timeframe=self.timeframe,
                                confidence=75.0 if variant.enable_memory else 65.0,
                                reasoning="Deterministic ablation pullback setup",
                                risk_action=ForexRiskDecisionAction.APPROVE,
                                approved=True,
                            )
                            proposals.append(prop)

            engine.step(
                candle=candle,
                pair=self.pair,
                new_proposals=proposals if proposals else None,
                lower_tf_candles=lower_tf_candles,
            )

        # Final settlement
        if engine.open_trades and candles:
            last = candles[-1]
            for t in list(engine.open_trades):
                engine._settle_trade(t, last.close, last.timestamp, TradeExitReason.MANUAL)
            engine.open_trades = []

        start_str = candles[0].timestamp.strftime("%Y-%m-%d %H:%M")
        end_str = candles[-1].timestamp.strftime("%Y-%m-%d %H:%M")
        result = engine._build_result(start_str, end_str)

        calls = analyses_done * variant.estimate_calls_per_point()
        est_cost = round((calls * variant.avg_tokens_per_call / 1000.0) * variant.cost_per_1k_tokens, 4)
        return result, analyses_done, est_cost

    def _render_markdown_report(
        self,
        study_id: str,
        pair: str,
        timeframe: str,
        start_date: str,
        end_date: str,
        total_bars: int,
        baseline_name: str,
        metrics: list[AblationVariantMetric],
        rankings: list[tuple[str, float]],
        winner_declared: bool,
        winning_name: str | None,
        warning: str | None,
    ) -> str:
        """Render institutional Markdown scorecards and audit comparisons."""
        lines = [
            f"# Forex Multi-Agent Ablation Study Report (`{study_id}`)",
            "",
            f"**Pair:** `{pair}` | **Timeframe:** `{timeframe}` | **Total Bars:** `{total_bars}`",
            f"**Interval:** `{start_date}` to `{end_date}`",
            f"**Baseline Benchmark:** *{baseline_name}*",
            "",
        ]

        if warning:
            lines.extend([
                "> [!WARNING]",
                f"> **STATISTICAL GUARD:** {warning}",
                "> Avoid drawing definitive conclusions or altering live models on undersized backtest distributions.",
                "",
            ])

        lines.extend([
            "## 1. Comparative Performance Matrix",
            "",
            "| Variant | Trades | Win Rate | Profit Factor | Expectancy (R) | Avg R | Max DD | Net P/L ($) | LLM Cost ($) | Latency (s) | Adequate? |",
            "| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |",
        ])

        for m in metrics:
            adeq_str = "Yes" if m.sample_size_adequate else "No (Tiny)"
            lines.append(
                f"| **{m.name}** | `{m.trade_count}` | `{m.win_rate_pct:.1f}%` | `{m.profit_factor:.2f}` | "
                f"`{m.expectancy_r:+.3f}` | `{m.average_r:+.3f}` | `{m.max_drawdown_pct:.1f}%` | "
                f"`${m.total_net_profit:+,.2f}` | `${m.estimated_llm_cost_usd:.4f}` | `{m.latency_seconds:.2f}s` | `{adeq_str}` |"
            )

        lines.extend([
            "",
            "## 2. Component Delta Matrix (vs Baseline)",
            "",
            "| Variant | Δ Expectancy (R) | Δ Profit Factor | Δ Max DD | Δ Net Profit | Δ LLM Cost ($) | Δ Latency |",
            "| :--- | :---: | :---: | :---: | :---: | :---: | :---: |",
        ])

        for m in metrics:
            lines.append(
                f"| **{m.name}** | `{m.delta_expectancy:+.3f}` | `{m.delta_profit_factor:+.2f}` | "
                f"`{m.delta_drawdown:+.1f}%` | `${m.delta_net_profit:+,.2f}` | "
                f"`${m.delta_cost_usd:+.4f}` | `{m.delta_latency_sec:+.2f}s` |"
            )

        if rankings:
            lines.extend([
                "",
                "## 3. Component Importance Ranking (Alpha Loss Without Component)",
                "",
            ])
            for rank, (comp_name, score) in enumerate(rankings, 1):
                lines.append(f"{rank}. **{comp_name}**: Importance Score `{score:.3f}`")

        lines.extend([
            "",
            "## 4. Final Audit Verdict",
            "",
        ])
        if winner_declared and winning_name:
            lines.append(f"- **Audited Superior Variant:** **{winning_name}** (statistically valid N >= {self.min_sample_size}).")
        else:
            lines.append(
                f"- **Verdict:** **NO WINNER DECLARED**. {warning or 'Insufficient sample sizes across variants.'}"
            )

        return "\n".join(lines)
