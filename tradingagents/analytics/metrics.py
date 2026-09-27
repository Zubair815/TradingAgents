"""Deep Quantitative Risk & Performance Analytics (Phase 19).

Computes institutional metrics:
- Calmar Ratio
- Ulcer Index & Ulcer Performance Index (Martin Ratio)
- Van Tharp System Quality Number (SQN)
- Recovery Factor
- Gain-to-Pain Ratio
- Drawdown duration and underwater recovery episode tracking
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

import numpy as np

from tradingagents.analytics.models import DeepMetrics, DrawdownEpisode, SQNRating


def calculate_deep_metrics(
    equity_curve: Sequence[float],
    trades: Sequence[Any] | None = None,
    initial_capital: float = 100000.0,
    annual_periods: int = 252,
) -> DeepMetrics:
    """Calculate institutional downside, drawdown, and edge metrics.

    Args:
        equity_curve: Sequence of sequential equity points (floats).
        trades: Optional sequence of trade records with .r_multiple, .net_profit, etc.
        initial_capital: Initial portfolio starting balance.
        annual_periods: Trading periods per year for annualization.

    Returns:
        DeepMetrics instance with Calmar, Ulcer, SQN, Gain-to-Pain, and episode data.
    """
    if not equity_curve:
        return DeepMetrics()

    equities = np.array(equity_curve, dtype=float)
    n_points = len(equities)

    # 1. Peak tracking and drawdowns
    running_max = np.maximum.accumulate(equities)
    drawdowns_cash = running_max - equities
    # Percentage drawdowns relative to running peak
    with np.errstate(divide="ignore", invalid="ignore"):
        drawdowns_pct = np.where(running_max > 0, (drawdowns_cash / running_max) * 100.0, 0.0)

    max_dd_cash = float(np.max(drawdowns_cash)) if len(drawdowns_cash) > 0 else 0.0
    max_dd_pct = float(np.max(drawdowns_pct)) if len(drawdowns_pct) > 0 else 0.0

    final_equity = float(equities[-1])
    total_net_profit = final_equity - initial_capital
    total_return_pct = (total_net_profit / max(1.0, initial_capital)) * 100.0

    # 2. Calmar Ratio
    if max_dd_pct > 0.001:
        # Annualized return based on length of sequence
        periods_held = max(1, n_points - 1)
        annualized_return_pct = total_return_pct * (annual_periods / max(1, periods_held))
        calmar = round(annualized_return_pct / max_dd_pct, 2)
    elif total_return_pct > 0:
        calmar = 999.0
    else:
        calmar = 0.0

    # 3. Ulcer Index (UI) & Ulcer Performance Index (UPI)
    # UI is quadratic root mean square of percentage drawdowns
    if n_points > 1:
        squared_pct = np.square(drawdowns_pct)
        ulcer_index = round(float(np.sqrt(np.mean(squared_pct))), 2)
    else:
        ulcer_index = 0.0

    if ulcer_index > 0.001:
        upi = round(total_return_pct / ulcer_index, 2)
    elif total_return_pct > 0:
        upi = 999.0
    else:
        upi = 0.0

    # 4. Recovery Factor
    if max_dd_cash > 0.01:
        recovery_factor = round(total_net_profit / max_dd_cash, 2)
    elif total_net_profit > 0:
        recovery_factor = 999.0
    else:
        recovery_factor = 0.0

    # 5. Underwater Drawdown Episodes & Durations
    episodes: list[DrawdownEpisode] = []
    in_drawdown = False
    peak_idx = 0
    trough_idx = 0
    trough_val = equities[0]
    peak_val = equities[0]
    episode_counter = 0

    for i in range(n_points):
        eq = equities[i]
        if eq >= running_max[i]:
            # At or establishing new peak
            if in_drawdown:
                # Episode recovered!
                dd_cash = peak_val - trough_val
                dd_pct = (dd_cash / max(1.0, peak_val)) * 100.0
                episodes.append(
                    DrawdownEpisode(
                        episode_id=episode_counter,
                        peak_index=peak_idx,
                        trough_index=trough_idx,
                        recovery_index=i,
                        peak_equity=round(peak_val, 2),
                        trough_equity=round(trough_val, 2),
                        drawdown_cash=round(dd_cash, 2),
                        drawdown_pct=round(dd_pct, 2),
                        duration_bars=i - peak_idx,
                        recovery_bars=i - trough_idx,
                        is_recovered=True,
                    )
                )
                in_drawdown = False
            peak_idx = i
            peak_val = eq
            trough_idx = i
            trough_val = eq
        else:
            # Below peak
            if not in_drawdown:
                in_drawdown = True
                episode_counter += 1
            if eq < trough_val:
                trough_val = eq
                trough_idx = i

    # Unrecovered terminal episode
    if in_drawdown:
        dd_cash = peak_val - trough_val
        dd_pct = (dd_cash / max(1.0, peak_val)) * 100.0
        episodes.append(
            DrawdownEpisode(
                episode_id=episode_counter,
                peak_index=peak_idx,
                trough_index=trough_idx,
                recovery_index=None,
                peak_equity=round(peak_val, 2),
                trough_equity=round(trough_val, 2),
                drawdown_cash=round(dd_cash, 2),
                drawdown_pct=round(dd_pct, 2),
                duration_bars=n_points - 1 - peak_idx,
                recovery_bars=None,
                is_recovered=False,
            )
        )

    durations = [ep.duration_bars for ep in episodes]
    longest_dd_bars = max(durations) if durations else 0
    avg_dd_bars = round(float(np.mean(durations)), 1) if durations else 0.0

    # 6. SQN & Gain-to-Pain from Trade History
    sqn = 0.0
    gain_to_pain = 0.0

    if trades:
        # Extract R-multiples or net profits
        r_multiples: list[float] = []
        profits: list[float] = []
        losses: list[float] = []

        for t in trades:
            r = getattr(t, "r_multiple", None)
            if r is not None:
                r_multiples.append(float(r))
            np_val = getattr(t, "net_profit", None)
            if np_val is not None:
                val = float(np_val)
                if val > 0:
                    profits.append(val)
                elif val < 0:
                    losses.append(abs(val))

        # SQN: sqrt(min(N, 100)) * (mean_R / std_R)
        if len(r_multiples) >= 2:
            r_arr = np.array(r_multiples, dtype=float)
            r_mean = float(np.mean(r_arr))
            r_std = float(np.std(r_arr, ddof=1))
            if r_std > 1e-6:
                sqn = round(math.sqrt(min(len(r_multiples), 100)) * (r_mean / r_std), 2)
            else:
                sqn = 0.0
        elif len(r_multiples) == 1:
            sqn = round(r_multiples[0], 2)

        # Gain-to-Pain: Total net return / sum of absolute losses
        sum_profits = sum(profits)
        sum_losses = sum(losses)
        if sum_losses > 0:
            gain_to_pain = round((sum_profits - sum_losses) / sum_losses, 2)
        elif sum_profits > 0:
            gain_to_pain = 999.0
        else:
            gain_to_pain = 0.0

    return DeepMetrics(
        calmar_ratio=calmar,
        ulcer_index=ulcer_index,
        ulcer_performance_index=upi,
        system_quality_number=sqn,
        sqn_rating=SQNRating.from_sqn(sqn),
        recovery_factor=recovery_factor,
        gain_to_pain_ratio=gain_to_pain,
        longest_drawdown_bars=longest_dd_bars,
        average_drawdown_bars=avg_dd_bars,
        underwater_episodes=episodes,
    )
