"""Trade Outcome Engine for Diagnostic Classification & Feedback (Phase 16).

Classifies trade lifecycle outcomes based on excursion dynamics, R-multiples,
and exit efficiency to detect behavioral leaks (e.g. leaving money on the table,
failing to protect unrealized profits, or letting losses runaway).
"""

from __future__ import annotations

import logging
from typing import Any

from tradingagents.database.models import TradeJournalRecord
from tradingagents.metrics.models import (
    TradeMfeMae,
    TradeOutcomeCategory,
    TradeOutcomeResult,
)

logger = logging.getLogger(__name__)


class TradeOutcomeEngine:
    """Deterministic trade outcome classification and institutional feedback engine."""

    @classmethod
    def classify_outcome(
        cls,
        mfe_mae: TradeMfeMae,
        trade: TradeJournalRecord | dict[str, Any] | None = None,
    ) -> TradeOutcomeResult:
        """Classify a trade into a diagnostic outcome category and compute efficiency score.

        Parameters
        ----------
        mfe_mae:
            Pre-computed Maximum Favorable / Adverse Excursion model.
        trade:
            Optional TradeJournalRecord for exit reason or notes context.

        Returns
        -------
        TradeOutcomeResult
            Typed classification with actionable tags and recommendations.
        """
        realized_r = mfe_mae.realized_r
        mfe_r = mfe_mae.mfe_r
        mae_r = mfe_mae.mae_r
        runup_eff = mfe_mae.runup_efficiency_pct
        exit_eff = mfe_mae.exit_efficiency_pct

        # -------------------------------------------------------------------
        # 1. RUNAWAY LOSS (Loss exceeded intended stop loss)
        # -------------------------------------------------------------------
        if mae_r > 1.05 or realized_r < -1.10:
            breach_r = max(mae_r - 1.0, abs(realized_r) - 1.0)
            score = max(0.0, round(50.0 - (breach_r * 80.0), 1))
            return TradeOutcomeResult(
                trade_id=mfe_mae.trade_id,
                category=TradeOutcomeCategory.RUNAWAY_LOSS,
                efficiency_score=score,
                title="Runaway Loss — Stop Breach",
                description=(
                    f"Adverse excursion reached {mae_r:.2f}R with realized loss of {realized_r:.2f}R, "
                    f"breaching the planned 1.0R risk threshold by {breach_r:.2f}R."
                ),
                tags=["runaway_loss", "stop_breach", "excessive_slippage", "risk_leak"],
                recommendations=[
                    "Investigate broker stop order fill latency and negative slippage.",
                    "Ensure stop loss orders are sent natively to the broker rather than resting client-side.",
                    "Verify if position was held across weekend market close or high-impact macroeconomic embargo.",
                ],
                mfe_pips=mfe_mae.mfe_pips,
                mae_pips=mfe_mae.mae_pips,
                mfe_r=mfe_r,
                mae_r=mae_r,
                realized_r=realized_r,
            )

        # -------------------------------------------------------------------
        # 2. GREEDY EXIT (Substantial profit given back into scratch/loss)
        # -------------------------------------------------------------------
        if mfe_r >= 1.5 and realized_r <= 0.15:
            score = 30.0
            return TradeOutcomeResult(
                trade_id=mfe_mae.trade_id,
                category=TradeOutcomeCategory.GREEDY_EXIT,
                efficiency_score=score,
                title="Greedy Exit — Unharvested Gains Given Back",
                description=(
                    f"Position reached peak favorable excursion of +{mfe_r:.2f}R ({mfe_mae.mfe_pips:.1f} pips) "
                    f"but failed to protect open profit, settling at {realized_r:+.2f}R."
                ),
                tags=["unharvested_profit", "no_breakeven_lock", "profit_given_back", "management_leak"],
                recommendations=[
                    "Enforce deterministic breakeven stop rule once trade reaches +1.0R or +1.5R excursion.",
                    "Scale out 30-50% position volume at initial target level to lock in banked gain.",
                    "Use a structured trailing stop following structural swing points on H1/M15.",
                ],
                mfe_pips=mfe_mae.mfe_pips,
                mae_pips=mfe_mae.mae_pips,
                mfe_r=mfe_r,
                mae_r=mae_r,
                realized_r=realized_r,
            )

        # -------------------------------------------------------------------
        # 3. PREMATURE EXIT (Winning runner cut short prematurely)
        # -------------------------------------------------------------------
        if mfe_r >= 2.0 and (realized_r < 0.5 * mfe_r or runup_eff < 45.0) and realized_r > 0.0:
            score = 55.0
            return TradeOutcomeResult(
                trade_id=mfe_mae.trade_id,
                category=TradeOutcomeCategory.PREMATURE_EXIT,
                efficiency_score=score,
                title="Premature Exit — Money Left on the Table",
                description=(
                    f"Trade closed with modest profit of +{realized_r:.2f}R, capturing only {runup_eff:.1f}% "
                    f"of the available +{mfe_r:.2f}R ({mfe_mae.mfe_pips:.1f} pips) favorable move."
                ),
                tags=["premature_exit", "left_money_on_table", "fear_of_giving_back", "asymmetry_leak"],
                recommendations=[
                    "Allow winning trades to run toward planned higher-timeframe liquidity targets.",
                    "Avoid discretionary manual exits when market structure has not signaled a reversal.",
                    "Consider partial closes rather than full liquidations to maintain runner exposure.",
                ],
                mfe_pips=mfe_mae.mfe_pips,
                mae_pips=mfe_mae.mae_pips,
                mfe_r=mfe_r,
                mae_r=mae_r,
                realized_r=realized_r,
            )

        # -------------------------------------------------------------------
        # 4. PERFECT EXIT (Optimal capture near crest of move)
        # -------------------------------------------------------------------
        if realized_r > 0.25 and (runup_eff >= 80.0 or exit_eff >= 85.0):
            score = min(100.0, round(92.0 + min(8.0, realized_r), 1))
            return TradeOutcomeResult(
                trade_id=mfe_mae.trade_id,
                category=TradeOutcomeCategory.PERFECT_EXIT,
                efficiency_score=score,
                title="Perfect Exit — Optimal Move Capture",
                description=(
                    f"Exemplary capture of {runup_eff:.1f}% of favorable runup (+{realized_r:.2f}R) "
                    f"with {exit_eff:.1f}% exit efficiency."
                ),
                tags=["perfect_exit", "optimal_capture", "disciplined_execution", "high_efficiency"],
                recommendations=[
                    "Exit timing and target placement were optimal; log as a gold-standard execution benchmark."
                ],
                mfe_pips=mfe_mae.mfe_pips,
                mae_pips=mfe_mae.mae_pips,
                mfe_r=mfe_r,
                mae_r=mae_r,
                realized_r=realized_r,
            )

        # -------------------------------------------------------------------
        # 5. BREAKEVEN (Scratch near entry with minimal excursion)
        # -------------------------------------------------------------------
        if -0.15 <= realized_r <= 0.15 and mfe_r < 1.5:
            score = 75.0
            return TradeOutcomeResult(
                trade_id=mfe_mae.trade_id,
                category=TradeOutcomeCategory.BREAKEVEN,
                efficiency_score=score,
                title="Breakeven — Capital Preserved",
                description=(
                    f"Trade scratched near cost basis ({realized_r:+.2f}R) with contained adverse excursion "
                    f"of {mae_r:.2f}R."
                ),
                tags=["breakeven", "capital_preservation", "neutral_trade"],
                recommendations=[
                    "Clean risk mitigation; trade settled with minimal impact on portfolio equity."
                ],
                mfe_pips=mfe_mae.mfe_pips,
                mae_pips=mfe_mae.mae_pips,
                mfe_r=mfe_r,
                mae_r=mae_r,
                realized_r=realized_r,
            )

        # -------------------------------------------------------------------
        # 6. STANDARD WIN (Solid profitable trade)
        # -------------------------------------------------------------------
        if realized_r > 0.15:
            score = round(min(90.0, 70.0 + runup_eff * 0.2), 1)
            return TradeOutcomeResult(
                trade_id=mfe_mae.trade_id,
                category=TradeOutcomeCategory.STANDARD_WIN,
                efficiency_score=score,
                title="Standard Win — Planned Profit Reached",
                description=(
                    f"Profitable trade realizing +{realized_r:.2f}R ({mfe_mae.realized_pips:.1f} pips) "
                    f"with {runup_eff:.1f}% runup capture."
                ),
                tags=["standard_win", "systematic_profit", "positive_expectancy"],
                recommendations=[
                    "Trade completed cleanly according to systematic criteria."
                ],
                mfe_pips=mfe_mae.mfe_pips,
                mae_pips=mfe_mae.mae_pips,
                mfe_r=mfe_r,
                mae_r=mae_r,
                realized_r=realized_r,
            )

        # -------------------------------------------------------------------
        # 7. STANDARD LOSS (Controlled loss strictly within planned stop)
        # -------------------------------------------------------------------
        if realized_r < -0.15 and mae_r <= 1.05:
            score = round(max(50.0, 75.0 - (mae_r * 20.0)), 1)
            return TradeOutcomeResult(
                trade_id=mfe_mae.trade_id,
                category=TradeOutcomeCategory.STANDARD_LOSS,
                efficiency_score=score,
                title="Standard Loss — Controlled Risk",
                description=(
                    f"Controlled loss strictly contained within planned risk ({mae_r:.2f}R MAE, {realized_r:.2f}R realized)."
                ),
                tags=["standard_loss", "controlled_risk", "stop_respected", "disciplined_loss"],
                recommendations=[
                    "Normal operational loss within expected statistical variance; risk parameters respected."
                ],
                mfe_pips=mfe_mae.mfe_pips,
                mae_pips=mfe_mae.mae_pips,
                mfe_r=mfe_r,
                mae_r=mae_r,
                realized_r=realized_r,
            )

        # -------------------------------------------------------------------
        # 8. SCRATCH (Default fallback)
        # -------------------------------------------------------------------
        return TradeOutcomeResult(
            trade_id=mfe_mae.trade_id,
            category=TradeOutcomeCategory.SCRATCH,
            efficiency_score=60.0,
            title="Scratch Trade",
            description="Minimal excursion and flat settlement.",
            tags=["scratch"],
            recommendations=["Review entry timing and execution catalysts."],
            mfe_pips=mfe_mae.mfe_pips,
            mae_pips=mfe_mae.mae_pips,
            mfe_r=mfe_r,
            mae_r=mae_r,
            realized_r=realized_r,
        )
