"""Unified Institutional Analytics Manager Façade (Phase 19).

Coordinates deep performance analytics, Monte Carlo stress testing, empirical
stop/target calibration, and multi-agent ablation studies across live journal
records and backtest results.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from tradingagents.analytics.ablation import ForexAblationEngine
from tradingagents.analytics.calibration import RiskStopCalibrator
from tradingagents.analytics.dashboard import ForexAnalyticsDashboard
from tradingagents.analytics.models import (
    AblationStudyResult,
    ExecutiveAnalyticsDashboard,
    MonteCarloConfig,
    MonteCarloResult,
    StopTargetCalibration,
)
from tradingagents.analytics.monte_carlo import MonteCarloSimulator
from tradingagents.database.journal import ForexTradeJournal

logger = logging.getLogger(__name__)


class ForexAnalyticsManager:
    """Unified façade orchestrating institutional Forex analytics and diagnostics."""

    def __init__(
        self,
        journal: ForexTradeJournal | None = None,
        initial_capital: float = 100000.0,
    ) -> None:
        self.journal = journal
        self.initial_capital = initial_capital
        self.dashboard_builder = ForexAnalyticsDashboard()

    def generate_dashboard(
        self,
        trades: Sequence[Any] | None = None,
        equity_curve: Sequence[float] | None = None,
        initial_capital: float | None = None,
        pair: str = "GLOBAL",
        period_start: str = "N/A",
        period_end: str = "N/A",
        atr_pips: float | None = None,
    ) -> ExecutiveAnalyticsDashboard:
        """Generate comprehensive institutional analytics dashboard.

        If trades are omitted, loads settled trades from the attached journal.
        """
        active_trades = trades
        if active_trades is None and self.journal is not None:
            active_trades = self.journal.list_trades(limit=10000)

        active_trades = active_trades or []
        cap = initial_capital if initial_capital is not None else self.initial_capital

        return self.dashboard_builder.generate_dashboard(
            trades=active_trades,
            equity_curve=equity_curve,
            initial_capital=cap,
            period_start=period_start,
            period_end=period_end,
            pair=pair,
            atr_pips=atr_pips,
        )

    def run_monte_carlo(
        self,
        trades: Sequence[Any] | None = None,
        trials: int = 1000,
        initial_capital: float | None = None,
        seed: int | None = 42,
    ) -> MonteCarloResult:
        """Execute Monte Carlo bootstrap resampling and risk of ruin stress test."""
        active_trades = trades
        if active_trades is None and self.journal is not None:
            active_trades = self.journal.list_trades(limit=10000)

        active_trades = active_trades or []
        cap = initial_capital if initial_capital is not None else self.initial_capital

        simulator = MonteCarloSimulator(MonteCarloConfig(trials=trials, random_seed=seed))
        return simulator.run(active_trades, cap)

    def calibrate_stops(
        self,
        trades: Sequence[Any] | None = None,
        pair: str = "GLOBAL",
        atr_pips: float | None = None,
    ) -> StopTargetCalibration:
        """Calibrate optimal stop loss and take profit levels from MFE/MAE excursions."""
        active_trades = trades
        if active_trades is None and self.journal is not None:
            active_trades = self.journal.list_trades(limit=10000)

        active_trades = active_trades or []
        calibrator = RiskStopCalibrator()
        return calibrator.calibrate(active_trades, pair=pair, atr_pips=atr_pips)

    def run_ablation(
        self,
        trades: Sequence[Any] | None = None,
        initial_capital: float | None = None,
    ) -> AblationStudyResult:
        """Execute standard multi-agent and risk guardrails ablation study."""
        active_trades = trades
        if active_trades is None and self.journal is not None:
            active_trades = self.journal.list_trades(limit=10000)

        active_trades = active_trades or []
        cap = initial_capital if initial_capital is not None else self.initial_capital

        engine = ForexAblationEngine(initial_capital=cap)
        return engine.run_default_synthetic_ablation(active_trades)

    def analyze_backtest_result(
        self,
        backtest_result: Any,
    ) -> ExecutiveAnalyticsDashboard:
        """Directly ingest a ForexBacktestResult (Phase 18) and produce deep analytics."""
        trades = getattr(backtest_result, "trades", [])
        raw_curve = getattr(backtest_result, "equity_curve", [])

        # Extract floats from EquityPoint objects or numeric list
        equity_curve: list[float] = []
        for pt in raw_curve:
            if hasattr(pt, "equity"):
                equity_curve.append(float(pt.equity))
            elif isinstance(pt, (int, float)):
                equity_curve.append(float(pt))

        init_cap = getattr(backtest_result, "initial_balance", self.initial_capital)
        start_str = getattr(backtest_result, "start_date", "N/A")
        end_str = getattr(backtest_result, "end_date", "N/A")
        pair = trades[0].pair if trades and hasattr(trades[0], "pair") else "GLOBAL"

        return self.dashboard_builder.generate_dashboard(
            trades=trades,
            equity_curve=equity_curve,
            initial_capital=init_cap,
            period_start=start_str,
            period_end=end_str,
            pair=pair,
        )
