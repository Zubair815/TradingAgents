"""Forex Metrics, MFE/MAE, Trade Outcome & Execution Quality Package (Phase 16).

Exports:
- MFE/MAE excursion calculations in pips and R-multiples: ``calculate_mfe_mae``, ``calculate_trade_mfe_mae``.
- Diagnostic trade outcome engine: ``TradeOutcomeEngine``, ``TradeOutcomeCategory``, ``TradeOutcomeResult``.
- Execution quality & slippage analytics: ``ExecutionQualityAnalyzer``, ``ExecutionQuality``, ``SlippageType``,
  ``calculate_execution_slippage``, ``calculate_slippage_cost``, ``calculate_spread_cost``,
  ``calculate_execution_quality_score``.
- Unified manager & institutional markdown dashboard: ``ForexMetricsManager``, ``MetricsSummary``.
"""

from tradingagents.metrics.confidence_calibration import (
    CalibratedConfidenceResult,
    ConfidenceBucketMetrics,
    ConfidenceCalibrationEngine,
    ConfidenceCalibrationReport,
    calibrate_confidence_score,
)
from tradingagents.metrics.execution import (
    ExecutionQualityAnalyzer,
    calculate_execution_quality_score,
    calculate_execution_slippage,
    calculate_slippage_cost,
    calculate_spread_cost,
    compare_proposal_against_execution,
)
from tradingagents.metrics.manager import ForexMetricsManager
from tradingagents.metrics.mfe_mae import (
    calculate_mfe_mae,
    calculate_trade_mfe_mae,
    parse_utc_timestamp,
)
from tradingagents.metrics.models import (
    ExecutionQuality,
    MetricsSummary,
    ProposalExecutionComparison,
    SlippageType,
    TradeMfeMae,
    TradeOutcomeCategory,
    TradeOutcomeResult,
)
from tradingagents.metrics.outcome import TradeOutcomeEngine
from tradingagents.metrics.skipped_proposals import (
    AITheoreticalMetrics,
    ComparativePerformanceSummary,
    ProposalSimulationStatus,
    SkippedProposalEvaluator,
    SkippedProposalSimulation,
    UserExecutionMetrics,
    calculate_ai_theoretical_performance,
    calculate_user_execution_performance,
    compare_ai_vs_user_performance,
)

__all__ = [
    # Facade Manager
    "ForexMetricsManager",
    # Models
    "TradeMfeMae",
    "TradeOutcomeCategory",
    "TradeOutcomeResult",
    "ExecutionQuality",
    "ProposalExecutionComparison",
    "SlippageType",
    "MetricsSummary",
    "ProposalSimulationStatus",
    "SkippedProposalSimulation",
    "AITheoreticalMetrics",
    "UserExecutionMetrics",
    "ComparativePerformanceSummary",
    "ConfidenceBucketMetrics",
    "ConfidenceCalibrationReport",
    "CalibratedConfidenceResult",
    # MFE / MAE Calculations
    "calculate_mfe_mae",
    "calculate_trade_mfe_mae",
    "parse_utc_timestamp",
    # Outcome Engine
    "TradeOutcomeEngine",
    # Execution & Slippage
    "ExecutionQualityAnalyzer",
    "calculate_execution_slippage",
    "calculate_slippage_cost",
    "calculate_spread_cost",
    "calculate_execution_quality_score",
    "compare_proposal_against_execution",
    # Skipped Proposal Simulation & Comparative Performance
    "SkippedProposalEvaluator",
    "calculate_ai_theoretical_performance",
    "calculate_user_execution_performance",
    "compare_ai_vs_user_performance",
    # Confidence Calibration (Phase 18)
    "ConfidenceCalibrationEngine",
    "calibrate_confidence_score",
]
