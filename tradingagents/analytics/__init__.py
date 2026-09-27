"""Institutional Forex Analytics, Performance Dashboard, Calibration & Ablation (Phase 19).

Exports:
- MonteCarloSimulator, MonteCarloConfig, MonteCarloResult, DrawdownPercentiles, TerminalWealthPercentiles
- DeepMetrics, DrawdownEpisode, SQNRating, calculate_deep_metrics
- RiskStopCalibrator, StopTargetCalibration
- ForexAblationEngine, AblationVariantResult, AblationStudyResult
- ForexAnalyticsDashboard, ExecutiveAnalyticsDashboard
- ForexAnalyticsManager
"""

from tradingagents.analytics.ablation import ForexAblationEngine
from tradingagents.analytics.calibration import RiskStopCalibrator
from tradingagents.analytics.dashboard import ForexAnalyticsDashboard
from tradingagents.analytics.manager import ForexAnalyticsManager
from tradingagents.analytics.metrics import calculate_deep_metrics
from tradingagents.analytics.models import (
    AblationStudyResult,
    AblationVariantResult,
    DeepMetrics,
    DrawdownEpisode,
    DrawdownPercentiles,
    ExecutiveAnalyticsDashboard,
    MonteCarloConfig,
    MonteCarloResult,
    SQNRating,
    StopTargetCalibration,
    TerminalWealthPercentiles,
)
from tradingagents.analytics.monte_carlo import MonteCarloSimulator

__all__ = [
    # Monte Carlo
    "MonteCarloSimulator",
    "MonteCarloConfig",
    "MonteCarloResult",
    "DrawdownPercentiles",
    "TerminalWealthPercentiles",
    # Deep Metrics & Drawdowns
    "DeepMetrics",
    "DrawdownEpisode",
    "SQNRating",
    "calculate_deep_metrics",
    # Risk & Stop Calibration
    "RiskStopCalibrator",
    "StopTargetCalibration",
    # Multi-Agent Ablation
    "ForexAblationEngine",
    "AblationVariantResult",
    "AblationStudyResult",
    # Dashboard & Manager
    "ForexAnalyticsDashboard",
    "ExecutiveAnalyticsDashboard",
    "ForexAnalyticsManager",
]
