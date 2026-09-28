"""Forex Metrics, MFE/MAE, Trade Outcome & Execution Quality Package (Phase 16).

Exports:
- MFE/MAE excursion calculations in pips and R-multiples: ``calculate_mfe_mae``, ``calculate_trade_mfe_mae``.
- Diagnostic trade outcome engine: ``TradeOutcomeEngine``, ``TradeOutcomeCategory``, ``TradeOutcomeResult``.
- Execution quality & slippage analytics: ``ExecutionQualityAnalyzer``, ``ExecutionQuality``, ``SlippageType``,
  ``calculate_execution_slippage``, ``calculate_slippage_cost``, ``calculate_spread_cost``,
  ``calculate_execution_quality_score``.
- Unified manager & institutional markdown dashboard: ``ForexMetricsManager``, ``MetricsSummary``.
"""

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
]
