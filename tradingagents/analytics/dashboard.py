"""Institutional Analytics Dashboard Builder (Phase 19).

Assembles performance metrics, Monte Carlo stress testing, empirical stop/target
calibration, and multi-agent ablation into a unified executive report.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from tradingagents.analytics.ablation import ForexAblationEngine
from tradingagents.analytics.calibration import RiskStopCalibrator
from tradingagents.analytics.metrics import calculate_deep_metrics
from tradingagents.analytics.models import (
    AblationStudyResult,
    ExecutiveAnalyticsDashboard,
    MonteCarloConfig,
    MonteCarloResult,
    StopTargetCalibration,
)
from tradingagents.analytics.monte_carlo import MonteCarloSimulator

logger = logging.getLogger(__name__)


class ForexAnalyticsDashboard:
    """Builder and renderer for institutional Forex performance and risk dashboards."""

    def __init__(
        self,
        monte_carlo_trials: int = 1000,
        enable_monte_carlo: bool = True,
        enable_calibration: bool = True,
        enable_ablation: bool = True,
    ) -> None:
        self.monte_carlo_trials = monte_carlo_trials
        self.enable_monte_carlo = enable_monte_carlo
        self.enable_calibration = enable_calibration
        self.enable_ablation = enable_ablation

    def generate_dashboard(
        self,
        trades: Sequence[Any],
        equity_curve: Sequence[float] | None = None,
        initial_capital: float = 100000.0,
        period_start: str = "N/A",
        period_end: str = "N/A",
        pair: str = "GLOBAL",
        atr_pips: float | None = None,
    ) -> ExecutiveAnalyticsDashboard:
        """Construct full executive analytics dashboard across settled trades.

        Args:
            trades: List of trade objects or dictionaries.
            equity_curve: Sequential portfolio equity curve floats (if None, synthesized from trade PnLs).
            initial_capital: Starting portfolio balance.
            period_start: Date string or timestamp of simulation/trading start.
            period_end: Date string or timestamp of simulation/trading end.
            pair: Target currency pair or "GLOBAL".
            atr_pips: Optional pair ATR in pips for calibration.

        Returns:
            ExecutiveAnalyticsDashboard instance.
        """
        # Synthesize equity curve if not explicitly passed
        if equity_curve is not None and len(equity_curve) > 0:
            equities = list(equity_curve)
        else:
            equities = [initial_capital]
            for t in trades:
                pnl = getattr(t, "net_profit", None)
                if pnl is None and isinstance(t, dict):
                    pnl = t.get("net_profit", 0.0)
                equities.append(equities[-1] + float(pnl or 0.0))

        # 1. Executive Performance Aggregates
        final_equity = round(float(equities[-1]), 2)
        total_net_profit = round(final_equity - initial_capital, 2)
        total_return_pct = round((total_net_profit / max(1.0, initial_capital)) * 100.0, 2)

        total_trades = len(trades)
        wins = sum(1 for t in trades if (getattr(t, "net_profit", 0.0) or 0.0) > 0)
        win_rate = round((wins / total_trades) * 100.0, 1) if total_trades else 0.0

        gross_profit = sum(float(getattr(t, "net_profit", 0.0) or 0.0) for t in trades if (getattr(t, "net_profit", 0.0) or 0.0) > 0)
        gross_loss = abs(sum(float(getattr(t, "net_profit", 0.0) or 0.0) for t in trades if (getattr(t, "net_profit", 0.0) or 0.0) < 0))

        if gross_loss > 0:
            profit_factor = round(gross_profit / gross_loss, 2)
        elif gross_profit > 0:
            profit_factor = 999.0
        else:
            profit_factor = 0.0

        # Peak drawdown %
        peak = initial_capital
        max_dd_pct = 0.0
        for eq in equities:
            if eq > peak:
                peak = eq
            dd_pct = ((peak - eq) / max(1.0, peak)) * 100.0
            if dd_pct > max_dd_pct:
                max_dd_pct = dd_pct
        max_dd_pct = round(max_dd_pct, 2)

        # Sharpe ratio
        if len(equities) > 1:
            diffs = [equities[i] - equities[i - 1] for i in range(1, len(equities))]
            rets = [diffs[i] / max(1.0, equities[i]) for i in range(len(diffs))]
            import numpy as np
            r_mean = float(np.mean(rets))
            r_std = float(np.std(rets))
            import math
            sharpe = round((r_mean / r_std) * math.sqrt(252), 2) if r_std > 1e-6 else 0.0
        else:
            sharpe = 0.0

        # 2. Deep Quantitative Metrics
        deep_metrics = calculate_deep_metrics(
            equity_curve=equities,
            trades=trades,
            initial_capital=initial_capital,
        )

        # 3. Monte Carlo Simulation
        mc_result: MonteCarloResult | None = None
        if self.enable_monte_carlo and total_trades > 0:
            mc_sim = MonteCarloSimulator(MonteCarloConfig(trials=self.monte_carlo_trials))
            mc_result = mc_sim.run(trades, initial_capital)

        # 4. Stop & Target Calibration
        calib_result: StopTargetCalibration | None = None
        if self.enable_calibration and total_trades > 0:
            calibrator = RiskStopCalibrator()
            calib_result = calibrator.calibrate(trades, pair=pair, atr_pips=atr_pips)

        # 5. Ablation Study
        ablation_result: AblationStudyResult | None = None
        if self.enable_ablation and total_trades > 0:
            ablation_engine = ForexAblationEngine(initial_capital=initial_capital)
            ablation_result = ablation_engine.run_default_synthetic_ablation(trades)

        return ExecutiveAnalyticsDashboard(
            period_start=period_start,
            period_end=period_end,
            initial_capital=initial_capital,
            final_equity=final_equity,
            total_net_profit=total_net_profit,
            total_return_pct=total_return_pct,
            total_trades=total_trades,
            win_rate_pct=win_rate,
            profit_factor=profit_factor,
            sharpe_ratio=sharpe,
            max_drawdown_pct=max_dd_pct,
            deep_metrics=deep_metrics,
            monte_carlo=mc_result,
            calibration=calib_result,
            ablation=ablation_result,
            generated_at_utc=datetime.now(timezone.utc),
        )
