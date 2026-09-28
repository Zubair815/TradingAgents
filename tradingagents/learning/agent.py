"""Forex Post-Trade Reflection Agent (Phase 17).

Evaluates settled trades by cross-referencing intended proposals, excursion dynamics
(MFE/MAE), execution quality, and timeline events to derive institutional heuristics
and behavioral correction rules.
"""

from __future__ import annotations

import logging
from typing import Any

from tradingagents.learning.models import (
    ForexLesson,
    ReflectionContext,
    ReflectionRating,
    TradeReflection,
)
from tradingagents.metrics.models import (
    TradeOutcomeCategory,
)

logger = logging.getLogger(__name__)


class ForexReflectionAgent:
    """Institutional reflection engine extracting actionable learning signals from trades."""

    def __init__(self, store: Any | None = None) -> None:
        self.store = store

    def reflect(self, context: ReflectionContext) -> TradeReflection:
        """Analyze trade context and generate structured reflection and lessons."""
        trade = context.trade
        mfe_mae = context.mfe_mae
        outcome = context.outcome
        executions = context.executions
        proposal = context.proposal

        pair = trade.pair
        setup_type = "TREND_CONTINUATION"
        if proposal is not None:
            setup_type = getattr(proposal, "setup_type", "TREND_CONTINUATION")
            if hasattr(setup_type, "value"):
                setup_type = setup_type.value

        realized_r = trade.r_multiple if trade.r_multiple is not None else (mfe_mae.realized_r if mfe_mae else 0.0)
        realized_pips = trade.pips_gained if trade.pips_gained is not None else (mfe_mae.realized_pips if mfe_mae else 0.0)
        mfe_r = mfe_mae.mfe_r if mfe_mae else (realized_r if realized_r > 0 else 0.0)
        mae_r = mfe_mae.mae_r if mfe_mae else (abs(realized_r) if realized_r < 0 else 0.0)
        mfe_pips = mfe_mae.mfe_pips if mfe_mae else max(0.0, realized_pips)
        mae_pips = mfe_mae.mae_pips if mfe_mae else max(0.0, -realized_pips)
        runup_eff = mfe_mae.runup_efficiency_pct if mfe_mae else 0.0
        exit_eff = mfe_mae.exit_efficiency_pct if mfe_mae else 100.0

        outcome_cat = outcome.category if outcome else TradeOutcomeCategory.STANDARD_WIN

        what_went_well: list[str] = []
        what_went_wrong: list[str] = []
        lessons: list[ForexLesson] = []
        tags: list[str] = []
        rating: ReflectionRating = ReflectionRating.NEUTRAL

        # -------------------------------------------------------------------
        # 1. Evaluate Outcome Category & Risk Discipline
        # -------------------------------------------------------------------
        if outcome_cat == TradeOutcomeCategory.RUNAWAY_LOSS or mae_r > 1.05 or realized_r < -1.10:
            rating = ReflectionRating.CRITICAL_ERROR
            what_went_wrong.append(
                f"Adverse excursion reached {mae_r:.2f}R ({mae_pips:.1f} pips), breaching intended 1.0R risk limit."
            )
            tags.extend(["runaway_loss", "stop_breach", "risk_failure"])

            lessons.append(
                ForexLesson(
                    trade_id=trade.trade_id,
                    proposal_id=trade.proposal_id,
                    pair=pair,
                    setup_type=setup_type,
                    outcome_category=TradeOutcomeCategory.RUNAWAY_LOSS.value,
                    rule_violated="Hard Stop Loss Non-Negotiable Limit",
                    observation=f"Trade on {pair} sustained {mae_r:.2f}R adverse excursion, exceeding planned 1.0R stop.",
                    root_cause="Stop order execution slippage, delayed manual exit, or holding through unhedged macroeconomic embargo.",
                    actionable_rule=(
                        f"For {pair} {setup_type}, always place native broker-side stop loss immediately upon fill. "
                        "Never widen a stop loss after entry. Liquidate immediately if excursion exceeds 1.0R."
                    ),
                    confidence_score=1.0,
                    tags=["stop_breach", "risk_discipline", "broker_stop"],
                )
            )

        elif outcome_cat == TradeOutcomeCategory.GREEDY_EXIT or (outcome is None and mfe_r >= 1.5 and realized_r <= 0.15):
            rating = ReflectionRating.POOR
            what_went_wrong.append(
                f"Trade attained peak open profit of +{mfe_r:.2f}R ({mfe_pips:.1f} pips) but closed flat or in loss ({realized_r:+.2f}R)."
            )
            tags.extend(["greedy_exit", "unharvested_profit", "trade_management_leak"])

            lessons.append(
                ForexLesson(
                    trade_id=trade.trade_id,
                    proposal_id=trade.proposal_id,
                    pair=pair,
                    setup_type=setup_type,
                    outcome_category=TradeOutcomeCategory.GREEDY_EXIT.value,
                    rule_violated="Unrealized Profit Protection (Breakeven Rule)",
                    observation=f"Position reached +{mfe_r:.2f}R favorable excursion but completely reversed into {realized_r:+.2f}R.",
                    root_cause="Failure to move stop loss to breakeven or lock in partial profits at primary target levels.",
                    actionable_rule=(
                        f"Mandatory rule for {pair} {setup_type}: once price reaches +1.0R to +1.5R favorable excursion, "
                        "move stop loss to entry price + spread and scale out 30-50% position volume."
                    ),
                    confidence_score=0.95,
                    tags=["greedy_exit", "breakeven_rule", "partial_close"],
                )
            )

        elif outcome_cat == TradeOutcomeCategory.PREMATURE_EXIT or (outcome is None and mfe_r >= 2.0 and realized_r < 0.5 * mfe_r and realized_r > 0.0):
            rating = ReflectionRating.POOR
            what_went_well.append(f"Trade was profitable, banking +{realized_r:.2f}R.")
            what_went_wrong.append(
                f"Exited prematurely capturing only {runup_eff:.1f}% of available +{mfe_r:.2f}R ({mfe_pips:.1f} pips) move."
            )
            tags.extend(["premature_exit", "left_money_on_table", "asymmetry_leak"])

            lessons.append(
                ForexLesson(
                    trade_id=trade.trade_id,
                    proposal_id=trade.proposal_id,
                    pair=pair,
                    setup_type=setup_type,
                    outcome_category=TradeOutcomeCategory.PREMATURE_EXIT.value,
                    rule_violated="Runner Execution Discipline",
                    observation=f"Closed early with +{realized_r:.2f}R; price subsequently extended to +{mfe_r:.2f}R.",
                    root_cause="Discretionary early manual liquidation or impatience during normal consolidation.",
                    actionable_rule=(
                        f"For {pair} {setup_type}, do not liquidate winning positions before TP1 unless higher-timeframe "
                        "market structure exhibits an opposing structural break (CHoCH/BOS). Use structural trailing stops."
                    ),
                    confidence_score=0.90,
                    tags=["premature_exit", "runner_discipline", "trailing_stop"],
                )
            )

        elif outcome_cat == TradeOutcomeCategory.PERFECT_EXIT or (outcome is None and realized_r > 0.25 and (runup_eff >= 80.0 or exit_eff >= 85.0)):
            rating = ReflectionRating.EXCELLENT
            what_went_well.append(
                f"Exemplary capture of {runup_eff:.1f}% of peak move (+{realized_r:.2f}R, {realized_pips:.1f} pips) with {exit_eff:.1f}% exit efficiency."
            )
            tags.extend(["perfect_exit", "optimal_capture", "benchmark_trade"])

            lessons.append(
                ForexLesson(
                    trade_id=trade.trade_id,
                    proposal_id=trade.proposal_id,
                    pair=pair,
                    setup_type=setup_type,
                    outcome_category=TradeOutcomeCategory.PERFECT_EXIT.value,
                    rule_violated=None,
                    observation=f"Captured {realized_r:.2f}R out of {mfe_r:.2f}R peak move on {pair}.",
                    root_cause="Optimal alignment of technical setup, session liquidity, and disciplined target taking.",
                    actionable_rule=(
                        f"Replicate this setup pattern on {pair} {setup_type}: entry trigger and target placement "
                        "accurately matched liquidity pool exhaustion."
                    ),
                    confidence_score=1.0,
                    tags=["perfect_exit", "optimal_benchmark", "strategy_edge"],
                )
            )

        elif outcome_cat == TradeOutcomeCategory.STANDARD_WIN or realized_r > 0.15:
            rating = ReflectionRating.GOOD
            what_went_well.append(f"Systematic profit booked (+{realized_r:.2f}R, {realized_pips:.1f} pips).")
            tags.extend(["standard_win", "systematic_profit"])

            lessons.append(
                ForexLesson(
                    trade_id=trade.trade_id,
                    proposal_id=trade.proposal_id,
                    pair=pair,
                    setup_type=setup_type,
                    outcome_category=TradeOutcomeCategory.STANDARD_WIN.value,
                    rule_violated=None,
                    observation=f"Planned take-profit achieved on {pair} {setup_type}.",
                    root_cause="Systematic confluence of market structure and risk containment.",
                    actionable_rule=f"Maintain standard execution protocol for {pair} {setup_type}.",
                    confidence_score=0.85,
                    tags=["standard_win", "systematic_profit"],
                )
            )

        elif outcome_cat == TradeOutcomeCategory.STANDARD_LOSS or (realized_r < -0.15 and mae_r <= 1.05):
            rating = ReflectionRating.GOOD
            what_went_well.append(
                f"Disciplined loss contained strictly within intended stop loss ({mae_r:.2f}R MAE, {realized_r:.2f}R realized)."
            )
            what_went_wrong.append("Market price action invalidated setup thesis.")
            tags.extend(["standard_loss", "controlled_risk", "disciplined_stop"])

            lessons.append(
                ForexLesson(
                    trade_id=trade.trade_id,
                    proposal_id=trade.proposal_id,
                    pair=pair,
                    setup_type=setup_type,
                    outcome_category=TradeOutcomeCategory.STANDARD_LOSS.value,
                    rule_violated=None,
                    observation=f"Controlled loss on {pair} {setup_type} contained at {realized_r:.2f}R.",
                    root_cause="Normal statistical market variance; invalidation level reached.",
                    actionable_rule=(
                        f"Controlled loss on {pair} is acceptable variance. Do not revenge trade; maintain "
                        "consistent position sizing and wait for fresh A+ setup confirmation."
                    ),
                    confidence_score=0.80,
                    tags=["standard_loss", "risk_control", "variance"],
                )
            )

        else:
            rating = ReflectionRating.NEUTRAL
            what_went_well.append(f"Capital preserved at scratch ({realized_r:+.2f}R).")
            tags.extend(["breakeven", "scratch"])

        # -------------------------------------------------------------------
        # 2. Evaluate Broker Execution & Slippage Friction
        # -------------------------------------------------------------------
        for ex in executions:
            slip_pips = getattr(ex, "slippage_pips", 0.0)
            if slip_pips > 0.8:
                what_went_wrong.append(
                    f"Execution suffered {slip_pips:.1f} pips adverse slippage during order fill."
                )
                tags.append("execution_slippage_drag")
                lessons.append(
                    ForexLesson(
                        trade_id=trade.trade_id,
                        proposal_id=trade.proposal_id,
                        pair=pair,
                        setup_type=setup_type,
                        outcome_category="EXECUTION_SLIPPAGE",
                        rule_violated="Execution Timing & Liquidity Safeguard",
                        observation=f"Adverse fill slippage of {slip_pips:.1f} pips registered on {pair}.",
                        root_cause="Order dispatch coincided with high volatility spread expansion or off-session thin liquidity.",
                        actionable_rule=(
                            f"For {pair}, favor limit orders around key liquidity levels or restrict market orders to "
                            "prime London/New York session overlap to minimize slippage friction."
                        ),
                        confidence_score=0.90,
                        tags=["slippage_drag", "liquidity_timing", "execution_quality"],
                    )
                )

        # -------------------------------------------------------------------
        # 3. Evaluate Structured Output Dimensions (Phase 14)
        # -------------------------------------------------------------------
        # Direction quality
        if mfe_r >= 1.0 or realized_r > 0.5:
            direction_quality = "EXCELLENT"
        elif mfe_r >= 0.5:
            direction_quality = "GOOD"
        elif mfe_r <= 0.1 and mae_r >= 0.8:
            direction_quality = "WRONG_DIRECTION"
        else:
            direction_quality = "FAIR"

        # Thesis quality
        if (
            outcome_cat in (TradeOutcomeCategory.PERFECT_EXIT, TradeOutcomeCategory.STANDARD_WIN)
            or outcome_cat == TradeOutcomeCategory.PREMATURE_EXIT
        ):
            thesis_quality = "EXCELLENT"
        elif outcome_cat == TradeOutcomeCategory.STANDARD_LOSS:
            thesis_quality = "FAIR"
        elif outcome_cat == TradeOutcomeCategory.RUNAWAY_LOSS:
            thesis_quality = "CRITICAL_ERROR"
        else:
            thesis_quality = "GOOD"

        # Entry quality
        slip = context.slippage_pips
        if slip <= 0.0 and mae_r <= 0.3:
            entry_quality = "EXCELLENT"
        elif slip <= 0.5 and mae_r <= 0.6:
            entry_quality = "GOOD"
        elif slip > 1.0 or mae_r > 0.9:
            entry_quality = "POOR"
        else:
            entry_quality = "FAIR"

        # Stop quality
        if mae_r <= 0.5:
            stop_quality = "EXCELLENT"
        elif mae_r <= 1.0:
            stop_quality = "GOOD"
        else:
            stop_quality = "CRITICAL_ERROR"

        # Target quality
        if exit_eff >= 80.0 or outcome_cat == TradeOutcomeCategory.PERFECT_EXIT:
            target_quality = "EXCELLENT"
        elif outcome_cat == TradeOutcomeCategory.GREEDY_EXIT:
            target_quality = "POOR"
        elif realized_r > 0:
            target_quality = "GOOD"
        else:
            target_quality = "FAIR"

        # Execution quality
        if slip <= 0.0:
            execution_quality = "EXCELLENT"
        elif slip <= 0.5:
            execution_quality = "GOOD"
        elif slip <= 1.0:
            execution_quality = "FAIR"
        else:
            execution_quality = "POOR"

        # Management quality
        if outcome_cat == TradeOutcomeCategory.GREEDY_EXIT:
            management_quality = "POOR"
        elif outcome_cat == TradeOutcomeCategory.PREMATURE_EXIT:
            management_quality = "FAIR"
        elif (len(context.sl_changes) > 0 and realized_r >= 0) or outcome_cat in (
            TradeOutcomeCategory.PERFECT_EXIT,
            TradeOutcomeCategory.STANDARD_WIN,
        ):
            management_quality = "EXCELLENT"
        else:
            management_quality = "GOOD"

        main_success = (
            what_went_well[0]
            if what_went_well
            else "Trade executed and closed according to planned risk boundaries."
        )
        main_failure = (
            what_went_wrong[0]
            if what_went_wrong
            else "None — execution remained strictly within planned risk parameters."
        )

        # -------------------------------------------------------------------
        # 4. Construct Summary & Save Lessons
        # -------------------------------------------------------------------
        summary = (
            f"Trade {trade.trade_id} ({pair} {setup_type}) settled with {realized_r:+.2f}R ({realized_pips:+.1f} pips). "
            f"Rating: {rating.value}. Peak excursions: +{mfe_r:.2f}R MFE / {mae_r:.2f}R MAE. "
            f"Generated {len(lessons)} institutional lessons."
        )

        reflection = TradeReflection(
            trade_id=trade.trade_id,
            rating=rating,
            thesis_quality=thesis_quality,
            direction_quality=direction_quality,
            entry_quality=entry_quality,
            stop_quality=stop_quality,
            target_quality=target_quality,
            execution_quality=execution_quality,
            management_quality=management_quality,
            main_success=main_success,
            main_failure=main_failure,
            summary=summary,
            what_went_well=what_went_well,
            what_went_wrong=what_went_wrong,
            lessons=lessons,
            tags=list(set(tags)),
        )

        # If store is attached, persist lessons
        if self.store is not None and lessons:
            try:
                self.store.save_lessons(lessons)
            except Exception as e:
                logger.warning("Failed to automatically persist lessons to store: %s", e)

        return reflection
