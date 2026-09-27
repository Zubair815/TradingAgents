"""Monte Carlo Bootstrap Resampling & Risk of Ruin Stress Testing (Phase 19).

Provides:
- Bootstrap sampling with replacement across historical/backtested trades.
- Multi-threshold Probability of Ruin calculation (e.g. 10%, 20%, 30%, 50% max drawdown).
- Percentile distributions for Maximum Drawdown and Terminal Portfolio Wealth.
- 95% confidence intervals for total net profit and Sharpe ratio.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

import numpy as np

from tradingagents.analytics.models import (
    DrawdownPercentiles,
    MonteCarloConfig,
    MonteCarloResult,
    TerminalWealthPercentiles,
)


class MonteCarloSimulator:
    """Institutional Monte Carlo engine for stress-testing trade sequences."""

    def __init__(self, config: MonteCarloConfig | None = None) -> None:
        self.config = config or MonteCarloConfig()

    def run(
        self,
        trades: Sequence[Any],
        initial_capital: float = 100000.0,
    ) -> MonteCarloResult:
        """Execute bootstrap resampling simulation over trade returns or PnLs.

        Args:
            trades: Sequence of trades (objects with .net_profit, or numeric PnL floats).
            initial_capital: Starting portfolio balance.

        Returns:
            MonteCarloResult with drawdown distributions, ruin probabilities, and confidence intervals.
        """
        if not trades:
            return self._build_empty_result(initial_capital)

        # Extract numeric PnL values
        pnl_values: list[float] = []
        for t in trades:
            if hasattr(t, "net_profit") and t.net_profit is not None:
                pnl_values.append(float(t.net_profit))
            elif isinstance(t, (int, float)):
                pnl_values.append(float(t))
            elif isinstance(t, dict) and "net_profit" in t:
                pnl_values.append(float(t["net_profit"]))

        if not pnl_values:
            return self._build_empty_result(initial_capital)

        pnls = np.array(pnl_values, dtype=float)
        n_trades = len(pnls)
        sample_size = self.config.sample_size or n_trades
        n_trials = self.config.trials

        # Seed for reproducible testing
        rng = np.random.default_rng(self.config.random_seed)

        # Resample with replacement: shape (n_trials, sample_size)
        indices = rng.integers(0, n_trades, size=(n_trials, sample_size))
        sampled_pnls = pnls[indices]

        # Cumulative equity curves: prepend initial capital
        # shape: (n_trials, sample_size + 1)
        equity_paths = np.empty((n_trials, sample_size + 1), dtype=float)
        equity_paths[:, 0] = initial_capital
        np.cumsum(sampled_pnls, axis=1, out=equity_paths[:, 1:])
        equity_paths[:, 1:] += initial_capital

        # Compute running peaks and maximum drawdowns for each trial
        running_peaks = np.maximum.accumulate(equity_paths, axis=1)
        drawdowns_cash = running_peaks - equity_paths

        with np.errstate(divide="ignore", invalid="ignore"):
            drawdowns_pct = np.where(running_peaks > 0, (drawdowns_cash / running_peaks) * 100.0, 0.0)

        trial_max_dd_pct = np.max(drawdowns_pct, axis=1)  # shape (n_trials,)
        trial_terminal_equity = equity_paths[:, -1]
        trial_net_profits = trial_terminal_equity - initial_capital

        # Compute Sharpe ratio for each trial curve
        # Bar returns per trial
        with np.errstate(divide="ignore", invalid="ignore"):
            bar_returns = np.diff(equity_paths, axis=1) / np.maximum(1.0, equity_paths[:, :-1])
        means = np.mean(bar_returns, axis=1)
        stds = np.std(bar_returns, axis=1)
        # Annualized assuming ~252 periods
        ann_factor = math.sqrt(252)
        with np.errstate(divide="ignore", invalid="ignore"):
            trial_sharpes = np.where(stds > 1e-6, (means / stds) * ann_factor, 0.0)

        # 1. Ruin Probabilities across thresholds
        ruin_probs: dict[str, float] = {}
        for th in self.config.ruin_thresholds:
            breaches = np.sum(trial_max_dd_pct >= th)
            prob = float(breaches) / n_trials
            ruin_probs[f"{int(th)}%"] = round(prob, 4)

        # 2. Drawdown Percentiles
        dd_perc = DrawdownPercentiles(
            p5=round(float(np.percentile(trial_max_dd_pct, 5)), 2),
            p25=round(float(np.percentile(trial_max_dd_pct, 25)), 2),
            p50=round(float(np.percentile(trial_max_dd_pct, 50)), 2),
            p75=round(float(np.percentile(trial_max_dd_pct, 75)), 2),
            p95=round(float(np.percentile(trial_max_dd_pct, 95)), 2),
            p99=round(float(np.percentile(trial_max_dd_pct, 99)), 2),
            worst_case=round(float(np.max(trial_max_dd_pct)), 2),
        )

        # 3. Terminal Wealth Percentiles
        term_perc = TerminalWealthPercentiles(
            p5=round(float(np.percentile(trial_terminal_equity, 5)), 2),
            p25=round(float(np.percentile(trial_terminal_equity, 25)), 2),
            p50=round(float(np.percentile(trial_terminal_equity, 50)), 2),
            p75=round(float(np.percentile(trial_terminal_equity, 75)), 2),
            p95=round(float(np.percentile(trial_terminal_equity, 95)), 2),
        )

        # 4. Confidence Intervals
        alpha = 1.0 - self.config.confidence_level
        lower_p = (alpha / 2.0) * 100.0
        upper_p = (1.0 - alpha / 2.0) * 100.0

        profit_ci = (
            round(float(np.percentile(trial_net_profits, lower_p)), 2),
            round(float(np.percentile(trial_net_profits, upper_p)), 2),
        )

        sharpe_ci = (
            round(float(np.percentile(trial_sharpes, lower_p)), 2),
            round(float(np.percentile(trial_sharpes, upper_p)), 2),
        )

        median_terminal = round(float(np.median(trial_terminal_equity)), 2)
        profitable_pct = round(float(np.sum(trial_net_profits > 0) / n_trials) * 100.0, 1)

        return MonteCarloResult(
            trials=n_trials,
            initial_capital=initial_capital,
            ruin_probabilities=ruin_probs,
            drawdown_percentiles=dd_perc,
            terminal_wealth_percentiles=term_perc,
            profit_confidence_interval=profit_ci,
            sharpe_confidence_interval=sharpe_ci,
            median_terminal_equity=median_terminal,
            profitable_trials_pct=profitable_pct,
        )

    def _build_empty_result(self, initial_capital: float) -> MonteCarloResult:
        """Return zeroed result when no trades provided."""
        return MonteCarloResult(
            trials=self.config.trials,
            initial_capital=initial_capital,
            ruin_probabilities={f"{int(th)}%": 0.0 for th in self.config.ruin_thresholds},
            drawdown_percentiles=DrawdownPercentiles(),
            terminal_wealth_percentiles=TerminalWealthPercentiles(
                p5=initial_capital,
                p25=initial_capital,
                p50=initial_capital,
                p75=initial_capital,
                p95=initial_capital,
            ),
            profit_confidence_interval=(0.0, 0.0),
            sharpe_confidence_interval=(0.0, 0.0),
            median_terminal_equity=initial_capital,
            profitable_trials_pct=0.0,
        )
