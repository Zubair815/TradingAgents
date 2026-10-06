"""Unified Forex Metrics Manager Façade & Performance Dashboard (Phase 16).

Coordinates MFE/MAE calculations, diagnostic outcome classification,
execution quality benchmarking, and institutional markdown dashboard reporting.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from tradingagents.agents.schemas_forex import ForexAction
from tradingagents.database.journal import ForexTradeJournal
from tradingagents.database.models import (
    OrderExecutionRecord,
    ProposalRecord,
    ProposalStatus,
    TradeJournalRecord,
    TradeStatus,
)
from tradingagents.learning.history_provider import TradeHistoryProvider
from tradingagents.metrics.confidence_calibration import (
    CalibratedConfidenceResult,
    ConfidenceCalibrationEngine,
    ConfidenceCalibrationReport,
)
from tradingagents.metrics.execution import (
    ExecutionQualityAnalyzer,
)
from tradingagents.metrics.mfe_mae import (
    calculate_trade_mfe_mae,
    parse_utc_timestamp,
)
from tradingagents.metrics.models import (
    ExecutionQuality,
    MetricsSummary,
    ProposalExecutionComparison,
    TradeMfeMae,
    TradeOutcomeCategory,
    TradeOutcomeResult,
)
from tradingagents.metrics.outcome import TradeOutcomeEngine
from tradingagents.metrics.skipped_proposals import (
    ComparativePerformanceSummary,
    SkippedProposalEvaluator,
    SkippedProposalSimulation,
    compare_ai_vs_user_performance,
)

logger = logging.getLogger(__name__)


class ForexMetricsManager:
    """Unified coordinator for post-trade excursion and broker execution analytics."""

    def __init__(
        self,
        journal: ForexTradeJournal | None = None,
        db_path: str | Path | None = None,
        account_currency: str = "USD",
    ) -> None:
        self.account_currency = account_currency.upper()
        if journal is not None:
            self.journal = journal
        elif db_path is not None:
            self.journal = ForexTradeJournal(db_path=db_path, auto_migrate=True, account_currency=account_currency)
        else:
            self.journal = None

        self.execution_analyzer = ExecutionQualityAnalyzer(account_currency=self.account_currency)
        self.outcome_engine = TradeOutcomeEngine()
        self.proposal_evaluator = SkippedProposalEvaluator()
        self.confidence_engine = ConfidenceCalibrationEngine()

    def analyze_trade(
        self,
        trade: TradeJournalRecord | str,
        candles: Sequence[Any] | pd.DataFrame,
    ) -> tuple[TradeMfeMae, TradeOutcomeResult]:
        """Analyze excursions and classify diagnostic outcome for a trade.

        Parameters
        ----------
        trade:
            TradeJournalRecord instance or string trade_id (if journal is attached).
        candles:
            Intraday candles covering the hold period.

        Returns
        -------
        tuple[TradeMfeMae, TradeOutcomeResult]
            Excursion metrics and diagnostic outcome classification.
        """
        if isinstance(trade, str):
            if self.journal is None:
                raise ValueError("Cannot resolve trade_id string without an attached journal instance.")
            trade_rec = self.journal.get_trade(trade)
            if trade_rec is None:
                raise ValueError(f"Trade with id {trade!r} not found in journal.")
        else:
            trade_rec = trade

        mfe_mae = calculate_trade_mfe_mae(trade=trade_rec, candles=candles)
        outcome = self.outcome_engine.classify_outcome(mfe_mae=mfe_mae, trade=trade_rec)
        return mfe_mae, outcome

    def analyze_execution(
        self,
        trade_id: str,
        pair: str,
        action: ForexAction | str,
        requested_price: float,
        fill_price: float,
        volume: float = 0.1,
        spread_at_open_pips: float = 0.0,
        deal_id: str | None = None,
        proposal_id: str | None = None,
        order_type: str = "MARKET",
        execution_delay_ms: float | None = None,
        session: str | None = None,
        broker: str = "MetaTrader5",
    ) -> ExecutionQuality:
        """Analyze a broker fill deal and compute execution quality metrics."""
        return self.execution_analyzer.analyze_execution(
            trade_id=trade_id,
            pair=pair,
            action=action,
            requested_price=requested_price,
            fill_price=fill_price,
            volume=volume,
            spread_at_open_pips=spread_at_open_pips,
            deal_id=deal_id,
            proposal_id=proposal_id,
            order_type=order_type,
            execution_delay_ms=execution_delay_ms,
            session=session,
            broker=broker,
        )

    def compare_proposal_execution(
        self,
        proposal: Any,
        trade: Any,
        execution: Any = None,
        mfe_mae: Any = None,
        spread_pips: float = 0.0,
        fees: float = 0.0,
    ) -> ProposalExecutionComparison:
        """Compare an immutable proposal against actual broker execution (Phase 12)."""
        if isinstance(proposal, str) and self.journal is not None:
            proposal_rec = self.journal.get_proposal(proposal)
            if proposal_rec:
                proposal = proposal_rec
        if isinstance(trade, str) and self.journal is not None:
            trade_rec = self.journal.get_trade(trade)
            if trade_rec:
                trade = trade_rec
        if execution is None and self.journal is not None and hasattr(trade, "trade_id"):
            execs = self.journal.list_executions_for_trade(trade.trade_id)
            if execs:
                execution = execs[0]

        return self.execution_analyzer.compare_proposal_execution(
            proposal=proposal,
            trade=trade,
            execution=execution,
            mfe_mae=mfe_mae,
            spread_pips=spread_pips,
            fees=fees,
        )

    def compute_summary(
        self,
        trades: Sequence[TradeJournalRecord],
        candles_by_trade: dict[str, Sequence[Any] | pd.DataFrame] | None = None,
        executions: Sequence[ExecutionQuality | OrderExecutionRecord | dict[str, Any]] | None = None,
    ) -> MetricsSummary:
        """Compute aggregated portfolio-level metrics across trades and executions."""
        candles_map = candles_by_trade or {}
        closed_trades = [t for t in trades if t.status == TradeStatus.CLOSED]
        total_trades = len(closed_trades)

        if total_trades == 0:
            return MetricsSummary(total_trades_analyzed=0)

        mfe_mae_list: list[TradeMfeMae] = []
        outcomes_list: list[TradeOutcomeResult] = []

        for t in closed_trades:
            c = candles_map.get(t.trade_id, [])
            mfe_mae = calculate_trade_mfe_mae(t, c)
            outcome = self.outcome_engine.classify_outcome(mfe_mae, t)
            mfe_mae_list.append(mfe_mae)
            outcomes_list.append(outcome)

        # Average excursion metrics
        avg_mfe_pips = round(sum(m.mfe_pips for m in mfe_mae_list) / total_trades, 1)
        avg_mae_pips = round(sum(m.mae_pips for m in mfe_mae_list) / total_trades, 1)
        avg_mfe_r = round(sum(m.mfe_r for m in mfe_mae_list) / total_trades, 2)
        avg_mae_r = round(sum(m.mae_r for m in mfe_mae_list) / total_trades, 2)
        avg_realized_r = round(sum(m.realized_r for m in mfe_mae_list) / total_trades, 2)

        # Average efficiency metrics
        avg_runup_eff = round(sum(m.runup_efficiency_pct for m in mfe_mae_list) / total_trades, 1)
        avg_dd_eff = round(sum(m.drawdown_efficiency_pct for m in mfe_mae_list) / total_trades, 1)
        avg_exit_eff = round(sum(m.exit_efficiency_pct for m in mfe_mae_list) / total_trades, 1)

        # Outcome distribution counts
        outcome_counts: dict[str, int] = {}
        for o in outcomes_list:
            cat_name = o.category.value
            outcome_counts[cat_name] = outcome_counts.get(cat_name, 0) + 1

        # Execution benchmarks
        exec_benchmark: dict[str, Any] = {}
        exec_list: list[Any] = list(executions) if executions is not None else []
        if not exec_list and self.journal is not None:
            # Query executions for closed trades
            for t in closed_trades:
                trade_execs = self.journal.list_executions_for_trade(t.trade_id)
                exec_list.extend(trade_execs)

        if exec_list:
            exec_benchmark = self.execution_analyzer.benchmark_broker_execution(exec_list)

        avg_slip = exec_benchmark.get("avg_slippage_pips", 0.0)
        max_slip = exec_benchmark.get("max_adverse_slippage_pips", 0.0)
        best_slip = exec_benchmark.get("best_price_improvement_pips", 0.0)
        slip_cost = exec_benchmark.get("total_slippage_cost_usd", 0.0)
        spread_cost = exec_benchmark.get("total_spread_cost_usd", 0.0)
        friction_cost = exec_benchmark.get("total_friction_usd", 0.0)
        quality_score = exec_benchmark.get("avg_quality_score", 100.0)

        pair_scores: dict[str, float] = {}
        by_pair_data = exec_benchmark.get("by_pair", {})
        for p, d in by_pair_data.items():
            pair_scores[p] = d.get("avg_score", 100.0)

        return MetricsSummary(
            total_trades_analyzed=total_trades,
            total_executions_analyzed=int(exec_benchmark.get("total_executions", 0)),
            total_executions_excluded=int(exec_benchmark.get("excluded_executions", 0)),
            avg_mfe_pips=avg_mfe_pips,
            avg_mae_pips=avg_mae_pips,
            avg_mfe_r=avg_mfe_r,
            avg_mae_r=avg_mae_r,
            avg_realized_r=avg_realized_r,
            avg_runup_efficiency_pct=avg_runup_eff,
            avg_drawdown_efficiency_pct=avg_dd_eff,
            avg_exit_efficiency_pct=avg_exit_eff,
            avg_slippage_pips=avg_slip,
            max_adverse_slippage_pips=max_slip,
            best_price_improvement_pips=best_slip,
            total_slippage_cost_usd=slip_cost,
            total_spread_cost_usd=spread_cost,
            total_execution_friction_usd=friction_cost,
            avg_execution_quality_score=quality_score,
            outcome_counts=outcome_counts,
            pair_quality_scores=pair_scores,
        )

    analyze_portfolio = compute_summary

    def render_markdown_dashboard(
        self,
        summary: MetricsSummary,
        title: str = "Forex MFE/MAE & Execution Quality Dashboard",
        comparative: ComparativePerformanceSummary | None = None,
        confidence_report: ConfidenceCalibrationReport | None = None,
    ) -> str:
        """Render a formatted institutional Markdown dashboard with KPI tables."""
        lines: list[str] = [
            f"# {title}",
            f"*Generated at: {summary.generated_at_utc} UTC | Analyzed Trades: {summary.total_trades_analyzed}*",
            "",
            "## 1. Excursion & Trade Efficiency Metrics",
            "",
            "| Metric | Value | Benchmark / Target | Institutional Diagnostic |",
            "|:---|:---:|:---:|:---|",
            f"| **Average Realized R** | `{summary.avg_realized_r:+.2f}R` | `> +0.30R` | {'Healthy positive edge' if summary.avg_realized_r >= 0.3 else 'Marginal / sub-optimal edge'} |",
            f"| **Average Peak MFE** | `{summary.avg_mfe_r:.2f}R` (`{summary.avg_mfe_pips:.1f}` pips) | `> 1.50R` | Potential opportunity created by setup selection |",
            f"| **Average Deepest MAE** | `{summary.avg_mae_r:.2f}R` (`{summary.avg_mae_pips:.1f}` pips) | `< 0.70R` | {'Well-contained adverse drift' if summary.avg_mae_r <= 0.7 else 'Excessive drawdown tolerance'} |",
            f"| **Runup Capture Efficiency** | `{summary.avg_runup_efficiency_pct:.1f}%` | `> 50.0%` | {'Optimal runner capture' if summary.avg_runup_efficiency_pct >= 50 else 'Exiting early; money left on table'} |",
            f"| **Drawdown Buffer Efficiency** | `{summary.avg_drawdown_efficiency_pct:.1f}%` | `> 30.0%` | Capital protection buffer before stop loss |",
            f"| **Exit Efficiency** | `{summary.avg_exit_efficiency_pct:.1f}%` | `> 60.0%` | Proximity of exit to maximum favorable crest |",
            "",
            "## 2. Trade Outcome Categorization",
            "",
            "| Outcome Category | Count | % of Trades | Diagnostic Implication |",
            "|:---|:---:|:---:|:---|",
        ]

        total = summary.total_trades_analyzed if summary.total_trades_analyzed > 0 else 1
        for cat in TradeOutcomeCategory:
            count = summary.outcome_counts.get(cat.value, 0)
            pct = round((count / total) * 100.0, 1)

            diagnostic = ""
            if cat == TradeOutcomeCategory.PERFECT_EXIT:
                diagnostic = "Top-tier execution capturing crest of move"
            elif cat == TradeOutcomeCategory.PREMATURE_EXIT:
                diagnostic = "Positions cut early; high runner potential lost"
            elif cat == TradeOutcomeCategory.GREEDY_EXIT:
                diagnostic = "Open profits unprotected; reversed into scratch/loss"
            elif cat == TradeOutcomeCategory.RUNAWAY_LOSS:
                diagnostic = "CRITICAL: Stop loss breached by slippage or holding"
            elif cat == TradeOutcomeCategory.STANDARD_WIN:
                diagnostic = "Systematic target achieved as planned"
            elif cat == TradeOutcomeCategory.STANDARD_LOSS:
                diagnostic = "Controlled loss within acceptable statistical variance"
            elif cat == TradeOutcomeCategory.BREAKEVEN:
                diagnostic = "Capital preservation scratch"
            else:
                diagnostic = "Neutral or flat settlement"

            lines.append(f"| **`{cat.value}`** | {count} | {pct:.1f}% | {diagnostic} |")

        lines.extend([
            "",
            "## 3. Broker Execution Quality & Friction Analytics",
            "",
            "| Execution KPI | Value | Institutional Standard |",
            "|:---|:---:|:---|",
            f"| **Broker Executions Analyzed** | `{summary.total_executions_analyzed}` | Fills logged across deals |",
            f"| **Execution Quality Score** | `{summary.avg_execution_quality_score:.1f} / 100` | `> 85.0` (Institutional tier) |",
            f"| **Average Slippage** | `{summary.avg_slippage_pips:+.2f} pips` | `< +0.30 pips` |",
            f"| **Worst Adverse Slippage** | `{summary.max_adverse_slippage_pips:+.2f} pips` | `< +1.50 pips` |",
            f"| **Best Price Improvement** | `{summary.best_price_improvement_pips:+.2f} pips` | Broker price improvement |",
            f"| **Total Realized Slippage Drag** | `${summary.total_slippage_cost_usd:.2f}` | Direct slippage friction |",
            f"| **Total Spread Cost Drag** | `${summary.total_spread_cost_usd:.2f}` | Market spread friction |",
            f"| **Total Execution Friction** | `${summary.total_execution_friction_usd:.2f}` | Combined transaction drag |",
            "",
        ])

        if summary.pair_quality_scores:
            lines.extend([
                "### Pair Execution Quality Scores",
                "",
                "| Pair | Quality Score (0-100) | Execution Rating |",
                "|:---|:---:|:---|",
            ])
            for pair, score in summary.pair_quality_scores.items():
                rating = "EXCELLENT" if score >= 90 else "GOOD" if score >= 75 else "POOR / SLIPPAGE DRAG"
                lines.append(f"| `{pair}` | `{score:.1f}` | {rating} |")
            lines.append("")

        if comparative:
            lines.extend([
                "## 4. AI Theoretical Edge vs Actual Human Discretion",
                "",
                "| Performance Dimension | AI Theoretical (Baseline) | Actual Human Execution | Discretion Variance / Alpha |",
                "|:---|:---:|:---:|:---|",
                f"| **Win Rate** | `{comparative.ai_theoretical.win_rate_pct:.1f}%` ({comparative.ai_theoretical.win_count}/{comparative.ai_theoretical.win_count + comparative.ai_theoretical.loss_count}) | `{comparative.user_execution.win_rate_pct:.1f}%` ({comparative.user_execution.win_count}/{comparative.user_execution.win_count + comparative.user_execution.loss_count}) | `{comparative.user_execution.win_rate_pct - comparative.ai_theoretical.win_rate_pct:+.1f}%` |",
                f"| **Average R-Multiple** | `{comparative.ai_theoretical.avg_theoretical_r:+.2f}R` | `{comparative.user_execution.avg_realized_r:+.2f}R` | `{comparative.user_execution.avg_realized_r - comparative.ai_theoretical.avg_theoretical_r:+.2f}R` |",
                f"| **Expectancy per Trade** | `{comparative.ai_theoretical.expectancy_r:+.2f}R` | `{comparative.user_execution.expectancy_r:+.2f}R` | `{comparative.user_execution.expectancy_r - comparative.ai_theoretical.expectancy_r:+.2f}R` |",
                f"| **User Skip Alpha** | — | `{comparative.user_skip_alpha_r:+.2f}R` | Avoided {comparative.skipped_avoided_losses} losses, missed {comparative.skipped_missed_winners} wins |",
                "",
                f"> **Audit Verdict:** {comparative.verdict}",
                "",
            ])

        if confidence_report:
            lines.extend([
                "## 5. Model Confidence Calibration & Probabilistic Reliability",
                "",
                confidence_report.summary_markdown,
                "",
            ])

        return "\n".join(lines)

    def evaluate_proposal(
        self,
        proposal: ProposalRecord | dict[str, Any] | str,
        candles: Sequence[Any] | pd.DataFrame,
        sub_resolution_candles: Sequence[Any] | pd.DataFrame | None = None,
        history_provider: TradeHistoryProvider | None = None,
    ) -> SkippedProposalSimulation:
        """Simulate market outcome for an individual proposal against historical candles."""
        if isinstance(proposal, str):
            if self.journal is None:
                raise ValueError("Cannot resolve proposal_id string without an attached journal instance.")
            prop_rec = self.journal.get_proposal(proposal)
            if prop_rec is None:
                raise ValueError(f"Proposal with id {proposal!r} not found in journal.")
            prop_obj = prop_rec
        else:
            prop_obj = proposal

        return self.proposal_evaluator.evaluate(
            proposal=prop_obj,
            candles=candles,
            sub_resolution_candles=sub_resolution_candles,
            history_provider=history_provider,
        )

    def evaluate_skipped_proposals(
        self,
        history_provider: TradeHistoryProvider | None = None,
        default_window_hours: int = 24,
    ) -> list[SkippedProposalSimulation]:
        """Query all SKIPPED / SKIPPED_BY_USER proposals and simulate their outcomes."""
        if self.journal is None:
            return []

        skipped_props: list[ProposalRecord] = []
        for st in (ProposalStatus.SKIPPED, ProposalStatus.SKIPPED_BY_USER):
            skipped_props.extend(self.journal.list_proposals(status=st, limit=1000))

        provider = history_provider or getattr(self, "history_provider", None)
        results: list[SkippedProposalSimulation] = []

        for p in skipped_props:
            dt_start = parse_utc_timestamp(p.created_at_utc) or datetime.now(timezone.utc)
            valid_until_dt = parse_utc_timestamp(p.valid_until)
            dt_end = valid_until_dt or (dt_start + timedelta(hours=default_window_hours))

            candles: list[Any] = []
            if provider is not None:
                hist_res = provider.get_history(
                    pair=p.pair,
                    start_time=dt_start,
                    end_time=dt_end,
                    timeframe=getattr(p, "timeframe", "H1") or "H1",
                )
                if hist_res and hist_res.is_available and hist_res.candles:
                    candles = list(hist_res.candles)

            sim = self.proposal_evaluator.evaluate(
                proposal=p,
                candles=candles,
                history_provider=provider,
            )
            results.append(sim)

        return results

    def get_comparative_performance(
        self,
        simulations: Sequence[SkippedProposalSimulation] | None = None,
        history_provider: TradeHistoryProvider | None = None,
    ) -> ComparativePerformanceSummary:
        """Compute comparative performance between AI theoretical recommendations and actual user executions."""
        proposals = self.journal.list_proposals(limit=5000) if self.journal else []
        trades = self.journal.list_trades(limit=5000) if self.journal else []
        executions = self.journal.list_executions(limit=10000) if self.journal else []

        active_sims = (
            list(simulations)
            if simulations is not None
            else self.evaluate_skipped_proposals(history_provider=history_provider)
        )

        return compare_ai_vs_user_performance(
            proposals=proposals,
            simulations=active_sims,
            trades=trades,
            executions=executions,
        )

    def get_confidence_calibration(
        self,
        pair: str | None = None,
        min_samples: int = 10,
        trades: Sequence[Any] | None = None,
    ) -> ConfidenceCalibrationReport:
        """Compute calibration report for closed trades and/or historical simulations (Phase 18)."""
        if trades is not None:
            trade_list = list(trades)
        elif self.journal is not None:
            trade_list = self.journal.list_trades(pair=pair, limit=10000)
        else:
            trade_list = []
        return self.confidence_engine.compute_calibration(trades=trade_list, min_samples=min_samples)

    def calibrate_proposal_confidence(
        self,
        raw_confidence: float,
        pair: str | None = None,
        report: ConfidenceCalibrationReport | None = None,
    ) -> CalibratedConfidenceResult:
        """Map raw model confidence score to calibrated probability or safe uncalibrated warning (Phase 18)."""
        active_report = report or self.get_confidence_calibration(pair=pair)
        return self.confidence_engine.calibrate_score(raw_confidence=raw_confidence, report=active_report)

    def get_comprehensive_performance(
        self,
        trades: Sequence[Any] | None = None,
        proposals: Sequence[Any] | None = None,
        events: Sequence[Any] | None = None,
        initial_capital: float = 100000.0,
    ) -> Any:
        """Compute full deterministic metrics, execution analytics, and multi-dimensional segmentation (Phase 19)."""
        from tradingagents.analytics.performance import ForexPerformanceEngine

        engine = ForexPerformanceEngine(initial_capital=initial_capital)
        trade_list = list(trades) if trades is not None else (self.journal.list_trades(limit=10000) if self.journal else [])
        prop_list = list(proposals) if proposals is not None else (self.journal.list_proposals(limit=5000) if self.journal else [])
        event_list = list(events) if events is not None else []
        return engine.generate_performance_report(
            trades=trade_list,
            proposals=prop_list,
            events=event_list,
            initial_capital=initial_capital,
        )

