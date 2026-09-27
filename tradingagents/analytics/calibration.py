"""Empirical Risk & Stop Calibration Engine (Phase 19).

Calibrates optimal Stop Loss and Take Profit levels using:
- Maximum Adverse Excursion (MAE) winner survival analysis.
- Maximum Favorable Excursion (MFE) target crest expectancy curves.
- ATR multiplier optimization for dynamic volatility regimes.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

import numpy as np

from tradingagents.analytics.models import StopTargetCalibration

logger = logging.getLogger(__name__)


class RiskStopCalibrator:
    """Calibrates optimal SL and TP price geometry from historical trade excursions."""

    def __init__(self, default_atr_pips: float = 20.0) -> None:
        self.default_atr_pips = default_atr_pips

    def calibrate(
        self,
        trades: Sequence[Any],
        pair: str = "GLOBAL",
        atr_pips: float | None = None,
    ) -> StopTargetCalibration:
        """Analyze trade excursion distributions and determine optimal stop/target levels.

        Args:
            trades: Sequence of trades with excursion metrics (.mae_pips, .mfe_pips, .mfe_r, .mae_r, etc.).
            pair: Target currency pair or "GLOBAL".
            atr_pips: Average true range in pips for dynamic multiplier mapping.

        Returns:
            StopTargetCalibration with optimal SL/TP pips, R multiples, and actionable recommendations.
        """
        if not trades:
            return self._build_empty_calibration(pair)

        effective_atr = atr_pips or self.default_atr_pips

        # Extract excursion arrays
        mae_pips_list: list[float] = []
        mfe_pips_list: list[float] = []
        mfe_r_list: list[float] = []
        mae_r_list: list[float] = []
        winning_mae_pips: list[float] = []
        winning_pips_gained: list[float] = []
        losing_pips_lost: list[float] = []

        for t in trades:
            # Excursion values
            mae_p = getattr(t, "mae_pips", None)
            mfe_p = getattr(t, "mfe_pips", None)
            mfe_r = getattr(t, "mfe_r", None)
            mae_r = getattr(t, "mae_r", None)
            pips = getattr(t, "pips_gained", None)
            net_prof = getattr(t, "net_profit", None)

            # Fallbacks if dictionary
            if isinstance(t, dict):
                mae_p = t.get("mae_pips", mae_p)
                mfe_p = t.get("mfe_pips", mfe_p)
                mfe_r = t.get("mfe_r", mfe_r)
                mae_r = t.get("mae_r", mae_r)
                pips = t.get("pips_gained", pips)
                net_prof = t.get("net_profit", net_prof)

            is_win = (pips is not None and pips > 0) or (net_prof is not None and net_prof > 0)

            if mae_p is not None:
                mae_pips_list.append(float(mae_p))
                if is_win:
                    winning_mae_pips.append(float(mae_p))
            if mfe_p is not None:
                mfe_pips_list.append(float(mfe_p))
            if mfe_r is not None:
                mfe_r_list.append(float(mfe_r))
            if mae_r is not None:
                mae_r_list.append(float(mae_r))

            if pips is not None:
                if pips > 0:
                    winning_pips_gained.append(float(pips))
                elif pips < 0:
                    losing_pips_lost.append(abs(float(pips)))

        if not mfe_r_list and not mae_pips_list:
            return self._build_empty_calibration(pair)

        # -------------------------------------------------------------------
        # 1. MAE Winner Survival Curve & Optimal Stop Loss
        # -------------------------------------------------------------------
        sl_candidates = [5.0, 10.0, 15.0, 20.0, 25.0, 30.0, 40.0, 50.0, 60.0]
        survival_rates: dict[float, float] = {}
        total_winners = len(winning_mae_pips)

        best_sl_pips = 20.0
        best_sl_score = -1e9

        avg_win_pips = float(np.mean(winning_pips_gained)) if winning_pips_gained else 30.0

        for sl_cand in sl_candidates:
            if total_winners > 0:
                survived = sum(1 for m in winning_mae_pips if m <= sl_cand)
                rate = round(survived / total_winners, 4)
            else:
                rate = 1.0
            survival_rates[sl_cand] = rate

            # Evaluate net simulated pip score
            # Preserved win pips - premature stopout cost - loss pips truncated at sl_cand
            premature_stops = total_winners - (survived if total_winners > 0 else 0)
            score = (
                (survived if total_winners > 0 else 0) * avg_win_pips
                - premature_stops * sl_cand
                - len(losing_pips_lost) * sl_cand
            )
            if score > best_sl_score and rate >= 0.75:  # Preserve at least 75% of winners
                best_sl_score = score
                best_sl_pips = sl_cand

        # -------------------------------------------------------------------
        # 2. MFE Target Crest Curve & Optimal Take Profit (R)
        # -------------------------------------------------------------------
        tp_candidates = [0.8, 1.0, 1.2, 1.5, 1.8, 2.0, 2.5, 3.0, 4.0]
        crest_probs: dict[float, float] = {}
        expectancy_map: dict[float, float] = {}

        total_trades = len(mfe_r_list)
        best_tp_r = 2.0
        max_expectancy = -1e9

        for tp_cand in tp_candidates:
            if total_trades > 0:
                hits = sum(1 for mfe in mfe_r_list if mfe >= tp_cand)
                prob = round(hits / total_trades, 4)
            else:
                prob = 0.5
            crest_probs[tp_cand] = prob

            # Expectancy: P(hit) * TP_R - (1 - P(hit)) * 1.0R
            exp_val = round((prob * tp_cand) - ((1.0 - prob) * 1.0), 3)
            expectancy_map[tp_cand] = exp_val

            if exp_val > max_expectancy:
                max_expectancy = exp_val
                best_tp_r = tp_cand

        best_tp_pips = round(best_tp_r * best_sl_pips, 1)
        optimal_sl_r = 1.0

        # Suggested ATR multiples
        recommended_sl_atr = round(best_sl_pips / max(1.0, effective_atr), 1)
        recommended_tp_atr = round(best_tp_pips / max(1.0, effective_atr), 1)

        # Baseline expectancy vs calibrated expectancy improvement
        baseline_exp = expectancy_map.get(2.0, 0.0)
        lift_pct = (
            round(((max_expectancy - baseline_exp) / max(0.1, abs(baseline_exp))) * 100.0, 1)
            if max_expectancy > baseline_exp
            else 0.0
        )

        # Actionable recommendations
        recs: list[str] = [
            f"Set primary stop loss distance to {best_sl_pips:.1f} pips ({recommended_sl_atr:.1f}x ATR) to preserve {survival_rates.get(best_sl_pips, 1.0) * 100:.1f}% of winning trade setups.",
            f"Target {best_tp_r:.1f}R ({best_tp_pips:.1f} pips, {recommended_tp_atr:.1f}x ATR) to harvest maximum mathematical expectancy of +{max_expectancy:.2f}R per trade.",
        ]

        # Check for premature stopout warning
        tight_survival = survival_rates.get(10.0, 1.0)
        if tight_survival < 0.60:
            recs.append(
                f"Avoid ultra-tight stops (<= 10 pips): historically, {(1.0 - tight_survival) * 100:.1f}% of winning trades had adverse noise exceeding 10 pips before reaching profit."
            )

        if best_tp_r < 2.0:
            recs.append(
                f"Target crest falls off sharply above {best_tp_r:.1f}R: adopt trailing stops or scale out 70% at {best_tp_r:.1f}R rather than holding runners to 3.0R+."
            )

        return StopTargetCalibration(
            pair=pair,
            optimal_sl_pips=best_sl_pips,
            optimal_sl_r=optimal_sl_r,
            optimal_tp_pips=best_tp_pips,
            optimal_tp_r=best_tp_r,
            mae_survival_rates=survival_rates,
            mfe_crest_probabilities=crest_probs,
            expectancy_by_tp_r=expectancy_map,
            recommended_atr_sl_multiple=recommended_sl_atr,
            recommended_atr_tp_multiple=recommended_tp_atr,
            expected_gain_improvement_pct=lift_pct,
            recommendations=recs,
        )

    def _build_empty_calibration(self, pair: str) -> StopTargetCalibration:
        return StopTargetCalibration(
            pair=pair,
            optimal_sl_pips=20.0,
            optimal_sl_r=1.0,
            optimal_tp_pips=40.0,
            optimal_tp_r=2.0,
            mae_survival_rates={20.0: 1.0},
            mfe_crest_probabilities={2.0: 0.5},
            expectancy_by_tp_r={2.0: 0.5},
            recommended_atr_sl_multiple=1.5,
            recommended_atr_tp_multiple=2.5,
            expected_gain_improvement_pct=0.0,
            recommendations=["Insufficient trade history for empirical calibration. Using baseline 1:2 R:R guidelines."],
        )
