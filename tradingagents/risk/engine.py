"""Deterministic institutional risk management engine for Forex trades (Phase 10).

Enforces non-negotiable risk rules preventing LLM hallucination or over-leveraging:
1. Trade Geometry & Structural Integrity:
   - LONG: entry > stop_loss, take_profit > entry
   - SHORT: entry < stop_loss, take_profit < entry
   - Mandatory stop-loss on all directional trades.
2. Pip Stop-Loss Bounds:
   - Distance must be within [min_sl_pips, max_sl_pips] (e.g. 8.0 - 100.0 pips).
3. Risk-to-Reward (R:R) Validation:
   - Sub-1.0:1 R:R is rejected outright (negative expectancy).
   - 1.0:1 <= R:R < min_risk_reward_ratio triggers MODIFY (or REJECT if allow_modification=False).
   - R:R >= min_risk_reward_ratio passes.
4. Economic Calendar News Blackout:
   - High-impact (Tier-1) release within blackout_lookahead_hours (e.g. <= 2.0h) triggers hard REJECT.
   - High-impact release within news_warning_lookahead_hours (e.g. <= 24.0h) triggers MODIFY with 50% risk scale.
5. Market Hours & Weekend Closure:
   - Rejects execution if global Forex market is closed (Friday 22:00 UTC - Sunday 22:00 UTC or holidays).
6. Spread & Volatility Friction:
   - Rejects if live spread > max_spread_pips.
   - Rejects if spread / ATR > max_spread_atr_ratio.
7. Account Equity Risk Clamping & Deterministic Lot Sizing:
   - Clamps proposed risk % to max_risk_percent.
   - Calculates exact lot size using deterministic pip value and balance.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from tradingagents.agents.schemas_forex import (
    ForexAction,
    ForexRiskDecision,
    ForexRiskDecisionAction,
    ForexTraderProposal,
    compute_pips_and_rr,
)
from tradingagents.dataflows.forex_quality import DataInsufficientError
from tradingagents.forex.calendar import evaluate_event_risk_regime
from tradingagents.forex.domain import get_forex_pair
from tradingagents.forex.pips import lot_size_from_risk, pip_size_for
from tradingagents.forex.sessions import is_market_open, is_weekend

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Risk Limits Configuration
# ---------------------------------------------------------------------------


class ForexRiskLimits(BaseModel):
    """Institutional risk parameters and safety thresholds."""

    min_risk_reward_ratio: float = Field(
        default=1.5,
        ge=0.5,
        description="Target minimum Risk:Reward ratio required for outright approval (e.g. 1.5:1)",
    )
    hard_min_risk_reward_ratio: float = Field(
        default=1.0,
        ge=0.1,
        description="Absolute minimum Risk:Reward ratio; below this is an automatic REJECT",
    )
    max_risk_percent: float = Field(
        default=2.0,
        ge=0.1,
        le=10.0,
        description="Maximum account equity risk percentage authorized per trade",
    )
    default_risk_percent: float = Field(
        default=1.0,
        ge=0.1,
        le=5.0,
        description="Baseline equity risk percentage per trade",
    )
    min_sl_pips: float = Field(
        default=8.0,
        ge=1.0,
        description="Minimum stop-loss distance in pips (protects against spread noise & wicks)",
    )
    max_sl_pips: float = Field(
        default=100.0,
        ge=5.0,
        description="Maximum stop-loss distance in pips (prevents runaway tail risk)",
    )
    enforce_news_blackout: bool = Field(
        default=True,
        description="Whether to enforce economic calendar high-impact event blackouts",
    )
    blackout_lookahead_hours: float = Field(
        default=2.0,
        ge=0.0,
        description="Imminent Tier-1 release window triggering hard REJECT (hours)",
    )
    news_warning_lookahead_hours: float = Field(
        default=8.0,
        ge=0.0,
        description="Approaching Tier-1 release window triggering MODIFY with risk reduction (hours)",
    )
    enforce_market_open: bool = Field(
        default=True,
        description="Whether to reject setups when the Forex market is closed (weekend/holiday)",
    )
    max_spread_pips: float = Field(
        default=3.5,
        ge=0.1,
        description="Maximum allowable bid/ask spread in pips for entry execution",
    )
    max_spread_atr_ratio: float = Field(
        default=0.15,
        ge=0.01,
        description="Maximum allowable spread-to-ATR ratio (e.g. spread consuming >15% of ATR)",
    )
    max_open_positions: int = Field(default=5, ge=1, description="Maximum live open positions; pending orders excluded")
    max_daily_loss_percent: float | None = Field(default=None, gt=0.0, le=100.0)
    max_daily_loss_amount: float | None = Field(default=None, gt=0.0)
    require_take_profit: bool = Field(
        default=True,
        description="Whether a defined take-profit level is strictly mandatory for directional setups",
    )
    allow_modification: bool = Field(
        default=True,
        description="If True, borderline setups (e.g. 1.0 <= RR < 1.5, risk > max_risk) produce MODIFY; if False, strict REJECT",
    )


def _resolve_digits(pair: str, broker_digits: int | None = None) -> int:
    """Resolve price decimal digits from broker constraints, pair definition, or convention."""
    if broker_digits is not None and broker_digits >= 0:
        return broker_digits
    pair_obj = get_forex_pair(pair)
    if pair_obj is not None and pair_obj.digits > 0:
        return pair_obj.digits
    return 3 if "JPY" in pair.upper() else 5


# ---------------------------------------------------------------------------
# Forex Risk Engine
# ---------------------------------------------------------------------------


class ForexRiskEngine:
    """Deterministic institutional risk management engine.

    Executes pure-Python mathematical and logical verification on trade proposals.
    LLM reasoning or trader sentiment can never bypass these hard risk controls.
    """

    def __init__(self, default_limits: ForexRiskLimits | None = None) -> None:
        self.default_limits = default_limits or ForexRiskLimits()

    def validate_proposal(
        self,
        proposal: ForexTraderProposal,
        curr_date: str | None = None,
        curr_time_utc: str | None = None,
        current_spread_pips: float | None = None,
        atr_pips: float | None = None,
        account_balance: float | None = None,
        account_currency: str = "USD",
        limits: ForexRiskLimits | None = None,
        open_position_count: int = 0,
        daily_realized_pnl: float | None = None,
        day_start_balance: float | None = None,
        daily_pnl_available: bool = False,
        broker_digits: int | None = None,
    ) -> ForexRiskDecision:
        """Validate a ForexTraderProposal against non-negotiable risk limits.

        Returns a formal ``ForexRiskDecision`` containing the verdict (APPROVE,
        REJECT, MODIFY), authorized parameters, audit logs of passed checks,
        violations, and required adjustments.
        """
        eff_limits = limits or self.default_limits

        # -------------------------------------------------------------------
        # 1. Action Check: NO_TRADE setups are immediately approved
        # -------------------------------------------------------------------
        if proposal.action == ForexAction.NO_TRADE:
            return ForexRiskDecision(
                pair=proposal.pair,
                decision=ForexRiskDecisionAction.APPROVE,
                original_action=ForexAction.NO_TRADE,
                approved_action=ForexAction.NO_TRADE,
                max_risk_percent=0.0,
                approved_lot_size=0.0,
                entry_price=None,
                stop_loss=None,
                take_profit=None,
                risk_reward_ratio=None,
                min_rr_threshold=eff_limits.min_risk_reward_ratio,
                risk_checks_passed=["Proposal evaluated as NO_TRADE. Capital preserved with 0% risk."],
                risk_violations=[],
                modifications_required=[],
                executive_rationale=(
                    f"Proposal for {proposal.pair} is NO_TRADE "
                    f"({proposal.reasoning or 'neutral setup'}). Risk engine approves neutral capital preservation."
                ),
            )

        checks_passed: list[str] = []
        violations: list[str] = []
        modifications: list[str] = []
        digits = _resolve_digits(proposal.pair, broker_digits)

        if open_position_count >= eff_limits.max_open_positions:
            violations.append(
                f"Maximum open positions reached ({open_position_count}/{eff_limits.max_open_positions}); pending orders are excluded."
            )

        daily_limits = [
            value
            for value in (
                eff_limits.max_daily_loss_amount,
                (
                    day_start_balance * eff_limits.max_daily_loss_percent / 100.0
                    if day_start_balance is not None and eff_limits.max_daily_loss_percent is not None
                    else None
                ),
            )
            if value is not None
        ]
        if eff_limits.max_daily_loss_percent is not None or eff_limits.max_daily_loss_amount is not None:
            if not daily_pnl_available or daily_realized_pnl is None or not daily_limits:
                violations.append("Daily realized P&L is unavailable while the daily-loss rule is enabled.")
            else:
                daily_loss = max(0.0, -daily_realized_pnl)
                daily_limit = min(daily_limits)
                if daily_loss >= daily_limit:
                    violations.append(
                        f"Daily realized loss ({daily_loss:.2f}) reached limit ({daily_limit:.2f})."
                    )

        # -------------------------------------------------------------------
        # 2. Market Open / Weekend / Holiday Check
        # -------------------------------------------------------------------
        if eff_limits.enforce_market_open:
            from tradingagents.forex.calendar import _calendar_cutoff
            try:
                eval_dt = _calendar_cutoff(curr_date, curr_time_utc) if curr_date else datetime.now(timezone.utc)
            except ValueError:
                violations.append("DATA_INSUFFICIENT: invalid market evaluation cutoff")
                eval_dt = datetime.now(timezone.utc)

            if not is_market_open(eval_dt):
                reason = "weekend closure" if is_weekend(eval_dt) else "global market holiday"
                violations.append(
                    f"Forex market is closed at {eval_dt.strftime('%Y-%m-%d %H:%M UTC')} "
                    f"({reason}). New trade execution is strictly prohibited."
                )
            else:
                checks_passed.append(
                    f"Market open check passed: 24/5 liquidity active at {eval_dt.strftime('%Y-%m-%d %H:%M UTC')}."
                )

        # -------------------------------------------------------------------
        # 3. Mandatory Parameters & Trade Geometry
        # -------------------------------------------------------------------
        if proposal.entry_price is None or proposal.entry_price <= 0:
            violations.append("Valid positive entry price is mandatory for directional setups.")

        if proposal.stop_loss is None or proposal.stop_loss <= 0:
            violations.append("Mandatory stop-loss level is missing. Unhedged risk is prohibited.")

        if eff_limits.require_take_profit and (proposal.take_profit_1 is None or proposal.take_profit_1 <= 0):
            violations.append("Mandatory take-profit target is missing.")


        if proposal.entry_price is not None and proposal.stop_loss is not None:
            if proposal.action == ForexAction.LONG:
                if proposal.stop_loss >= proposal.entry_price:
                    violations.append(
                        f"LONG stop-loss ({proposal.stop_loss:.{digits}f}) must be strictly below "
                        f"entry price ({proposal.entry_price:.{digits}f})."
                    )
                if proposal.take_profit_1 is not None and proposal.take_profit_1 <= proposal.entry_price:
                    violations.append(
                        f"LONG take-profit ({proposal.take_profit_1:.{digits}f}) must be strictly above "
                        f"entry price ({proposal.entry_price:.{digits}f})."
                    )
            elif proposal.action == ForexAction.SHORT:
                if proposal.stop_loss <= proposal.entry_price:
                    violations.append(
                        f"SHORT stop-loss ({proposal.stop_loss:.{digits}f}) must be strictly above "
                        f"entry price ({proposal.entry_price:.{digits}f})."
                    )
                if proposal.take_profit_1 is not None and proposal.take_profit_1 >= proposal.entry_price:
                    violations.append(
                        f"SHORT take-profit ({proposal.take_profit_1:.{digits}f}) must be strictly below "
                        f"entry price ({proposal.entry_price:.{digits}f})."
                    )

        if not any("entry price" in v or "stop-loss" in v or "take-profit" in v for v in violations):
            checks_passed.append(
                "Trade geometry validated: entry, stop-loss, and take-profit levels are logically consistent."
            )

        # -------------------------------------------------------------------
        # 4. Pip Stop-Loss Bounds
        # -------------------------------------------------------------------
        sl_pips = proposal.sl_pips
        if sl_pips is None and proposal.entry_price is not None and proposal.stop_loss is not None:
            pip_size = pip_size_for(proposal.pair)
            sl_pips = round(abs(proposal.entry_price - proposal.stop_loss) / pip_size, 1)

        if sl_pips is not None:
            if sl_pips < eff_limits.min_sl_pips:
                violations.append(
                    f"Stop-loss distance ({sl_pips:.1f} pips) is below the minimum threshold "
                    f"({eff_limits.min_sl_pips:.1f} pips). Vulnerable to spread expansion and market noise."
                )
            elif sl_pips > eff_limits.max_sl_pips:
                violations.append(
                    f"Stop-loss distance ({sl_pips:.1f} pips) exceeds maximum allowable limit "
                    f"({eff_limits.max_sl_pips:.1f} pips). Capital inefficiency / excessive pip drawdown."
                )
            else:
                checks_passed.append(
                    f"Stop-loss distance ({sl_pips:.1f} pips) conforms to limits "
                    f"[{eff_limits.min_sl_pips:.1f}, {eff_limits.max_sl_pips:.1f}] pips."
                )

        # -------------------------------------------------------------------
        # 5. Risk-to-Reward Ratio (R:R) Validation
        # -------------------------------------------------------------------
        rr = proposal.risk_reward_ratio
        if rr is None and proposal.entry_price is not None and proposal.stop_loss is not None and proposal.take_profit_1 is not None:
            _, _, calc_rr = compute_pips_and_rr(
                pair=proposal.pair,
                action=proposal.action,
                entry=proposal.entry_price,
                stop_loss=proposal.stop_loss,
                take_profit=proposal.take_profit_1,
            )
            rr = calc_rr

        target_tp = proposal.take_profit_1

        if rr is None:
            violations.append("Risk:Reward ratio cannot be verified (missing valid entry, SL, or TP).")
        elif rr < eff_limits.hard_min_risk_reward_ratio:
            violations.append(
                f"Risk:Reward ratio ({rr:.2f}:1) is sub-1.0 (below absolute minimum of "
                f"{eff_limits.hard_min_risk_reward_ratio:.1f}:1). Negative expectancy trade."
            )
        elif eff_limits.hard_min_risk_reward_ratio <= rr < eff_limits.min_risk_reward_ratio:
            if eff_limits.allow_modification and proposal.entry_price is not None and proposal.stop_loss is not None:
                sl_dist = abs(proposal.entry_price - proposal.stop_loss)
                needed_tp_dist = sl_dist * eff_limits.min_risk_reward_ratio
                if proposal.action == ForexAction.LONG:
                    suggested_tp = round(proposal.entry_price + needed_tp_dist, digits)
                else:
                    suggested_tp = round(proposal.entry_price - needed_tp_dist, digits)

                modifications.append(
                    f"Risk:Reward ratio ({rr:.2f}:1) is below target {eff_limits.min_risk_reward_ratio:.1f}:1. "
                    f"Take-Profit target adjusted to {suggested_tp:.{digits}f} to achieve institutional {eff_limits.min_risk_reward_ratio:.1f}R."
                )
                target_tp = suggested_tp
                rr = eff_limits.min_risk_reward_ratio
            else:
                violations.append(
                    f"Risk:Reward ratio ({rr:.2f}:1) is below required institutional threshold of "
                    f"{eff_limits.min_risk_reward_ratio:.1f}:1."
                )
        else:
            checks_passed.append(
                f"Risk:Reward ratio ({rr:.2f}:1) satisfies institutional hurdle "
                f"(>= {eff_limits.min_risk_reward_ratio:.1f}:1)."
            )

        # -------------------------------------------------------------------
        # 6. Economic Calendar High-Impact News Blackout Window
        # -------------------------------------------------------------------
        news_risk_scale = 1.0
        if eff_limits.enforce_news_blackout and not curr_date:
            violations.append("DATA_INSUFFICIENT: calendar cutoff is required")
        if eff_limits.enforce_news_blackout and curr_date:
            try:
                assessment = evaluate_event_risk_regime(
                    symbol=proposal.pair,
                    curr_date=curr_date,
                    curr_time_utc=curr_time_utc,
                    lookahead_hours=max(48.0, eff_limits.news_warning_lookahead_hours),
                )
                if assessment.timing_blackout or (assessment.blackout_active and assessment.hours_to_next_high_impact is None):
                    violations.append("Economic news blackout: recent high-impact release or unknown release time")
                elif assessment.hours_to_next_high_impact is not None:
                    ev = assessment.next_high_impact_event
                    ev_title = ev.title if ev else "Tier-1 Economic Release"
                    ev_curr = ev.currency if ev else "G8"

                    if assessment.hours_to_next_high_impact <= eff_limits.blackout_lookahead_hours:
                        violations.append(
                            f"Economic news blackout active: Tier-1 release '{ev_title}' ({ev_curr}) "
                            f"is scheduled in {assessment.hours_to_next_high_impact:.1f} hours "
                            f"(blackout window: {eff_limits.blackout_lookahead_hours:.1f}h). Extreme slippage & volatility risk."
                        )
                    elif assessment.hours_to_next_high_impact <= eff_limits.news_warning_lookahead_hours:
                        if eff_limits.allow_modification:
                            modifications.append(
                                f"Approaching Tier-1 release '{ev_title}' ({ev_curr}) in "
                                f"{assessment.hours_to_next_high_impact:.1f} hours. Risk allocation reduced by 50%."
                            )
                            news_risk_scale = 0.5
                        else:
                            checks_passed.append(
                                f"Tier-1 release approaching in {assessment.hours_to_next_high_impact:.1f}h "
                                f"(outside {eff_limits.blackout_lookahead_hours:.1f}h blackout window)."
                            )
                    else:
                        checks_passed.append(
                            f"Next Tier-1 release '{ev_title}' is {assessment.hours_to_next_high_impact:.1f}h away (calendar clear)."
                        )
                else:
                    checks_passed.append("Economic calendar clear: No Tier-1 events scheduled within lookahead horizon.")
            except DataInsufficientError as exc:
                violations.append(str(exc))

        # -------------------------------------------------------------------
        # 7. Spread & Spread-to-ATR Friction Checks
        # -------------------------------------------------------------------
        if current_spread_pips is not None:
            if current_spread_pips > eff_limits.max_spread_pips:
                violations.append(
                    f"Current spread ({current_spread_pips:.1f} pips) exceeds maximum allowable "
                    f"threshold ({eff_limits.max_spread_pips:.1f} pips). Unfavorable execution friction."
                )
            else:
                checks_passed.append(
                    f"Current spread ({current_spread_pips:.1f} pips) is within limit "
                    f"(<= {eff_limits.max_spread_pips:.1f} pips)."
                )

        if current_spread_pips is not None and atr_pips is not None and atr_pips > 0:
            spread_atr_ratio = current_spread_pips / atr_pips
            if spread_atr_ratio > eff_limits.max_spread_atr_ratio:
                violations.append(
                    f"Spread-to-ATR ratio ({spread_atr_ratio:.1%}) exceeds maximum limit "
                    f"({eff_limits.max_spread_atr_ratio:.1%}). Spread friction consumes excessive volatility."
                )
            else:
                checks_passed.append(
                    f"Spread-to-ATR ratio ({spread_atr_ratio:.1%}) is favorable "
                    f"(<= {eff_limits.max_spread_atr_ratio:.1%})."
                )

        # -------------------------------------------------------------------
        # 8. Equity Risk Clamping & Deterministic Lot Sizing
        # -------------------------------------------------------------------
        raw_risk = (
            proposal.suggested_risk_percent
            if proposal.suggested_risk_percent is not None
            else eff_limits.default_risk_percent
        )

        if raw_risk > eff_limits.max_risk_percent:
            if eff_limits.allow_modification:
                modifications.append(
                    f"Risk percentage clamped from proposed {raw_risk:.1f}% to policy maximum of "
                    f"{eff_limits.max_risk_percent:.1f}%."
                )
                eff_risk = eff_limits.max_risk_percent
            else:
                violations.append(
                    f"Risk percentage ({raw_risk:.1f}%) exceeds maximum policy ceiling of "
                    f"{eff_limits.max_risk_percent:.1f}%."
                )
                eff_risk = eff_limits.max_risk_percent
        else:
            eff_risk = raw_risk
            checks_passed.append(
                f"Risk allocation ({eff_risk:.1f}%) conforms to risk ceiling (<= {eff_limits.max_risk_percent:.1f}%)."
            )

        if news_risk_scale < 1.0:
            eff_risk = round(eff_risk * news_risk_scale, 2)
            checks_passed.append(f"Risk scaled to {eff_risk:.2f}% due to approaching macroeconomic event.")

        # Compute authorized lot size if account balance is provided
        approved_lot_size: float | None = None
        if (
            account_balance is not None
            and account_balance > 0
            and proposal.entry_price is not None
            and proposal.stop_loss is not None
            and len(violations) == 0
        ):
            try:
                sizing = lot_size_from_risk(
                    account_equity=account_balance,
                    risk_percent=eff_risk,
                    entry_price=proposal.entry_price,
                    stop_loss_price=proposal.stop_loss,
                    pair=proposal.pair,
                    account_currency=account_currency,
                    current_quote_price=proposal.entry_price,
                )
                approved_lot_size = sizing["lot_size"]
                checks_passed.append(
                    f"Authorized position size: {approved_lot_size:.2f} standard lots "
                    f"(${sizing['actual_risk']:.2f} risk on ${account_balance:,.2f} equity)."
                )
            except Exception as e:
                logger.warning("Lot sizing calculation failed: %s", e)
                approved_lot_size = 0.0
                violations.append(f"Position sizing data insufficient: {e}")
        else:
            approved_lot_size = proposal.suggested_lot_size if len(violations) == 0 else 0.0

        # -------------------------------------------------------------------
        # 9. Final Decision Determination & Synthesis
        # -------------------------------------------------------------------
        if len(violations) > 0:
            decision_action = ForexRiskDecisionAction.REJECT
            approved_action = ForexAction.NO_TRADE
            approved_lot_size = 0.0
            max_risk_percent = 0.0
            rationale = (
                f"Risk Engine REJECTED proposed {proposal.action.value} setup on {proposal.pair}. "
                f"Encountered {len(violations)} risk rule violation(s):\n"
                + "\n".join(f"- {v}" for v in violations)
            )
        elif len(modifications) > 0:
            decision_action = ForexRiskDecisionAction.MODIFY
            approved_action = proposal.action
            max_risk_percent = eff_risk
            rationale = (
                f"Risk Engine MODIFIED proposed {proposal.action.value} setup on {proposal.pair}. "
                f"Setup is structurally viable pending {len(modifications)} execution adjustment(s):\n"
                + "\n".join(f"- {m}" for m in modifications)
            )
        else:
            decision_action = ForexRiskDecisionAction.APPROVE
            approved_action = proposal.action
            max_risk_percent = eff_risk
            rationale = (
                f"Risk Engine APPROVED proposed {proposal.action.value} setup on {proposal.pair}. "
                f"All {len(checks_passed)} risk controls passed successfully: R:R {rr:.2f}:1, "
                f"SL {sl_pips:.1f} pips, {max_risk_percent:.1f}% risk."
            )

        return ForexRiskDecision(
            pair=proposal.pair,
            decision=decision_action,
            original_action=proposal.action,
            approved_action=approved_action,
            max_risk_percent=max_risk_percent,
            approved_lot_size=approved_lot_size,
            entry_price=proposal.entry_price,
            stop_loss=proposal.stop_loss,
            take_profit=target_tp,
            risk_reward_ratio=rr,
            min_rr_threshold=eff_limits.min_risk_reward_ratio,
            risk_checks_passed=checks_passed,
            risk_violations=violations,
            modifications_required=modifications,
            executive_rationale=rationale,
        )

    def quick_check(
        self,
        proposal: ForexTraderProposal,
        limits: ForexRiskLimits | None = None,
    ) -> tuple[bool, list[str]]:
        """Perform a quick geometric, pip stop, and R:R check without external market data.

        Returns:
            (is_valid, list_of_violations)
        """
        eff_limits = limits or self.default_limits
        if proposal.action == ForexAction.NO_TRADE:
            return True, []

        violations: list[str] = []
        if proposal.entry_price is None or proposal.entry_price <= 0:
            violations.append("Entry price is required.")
        if proposal.stop_loss is None or proposal.stop_loss <= 0:
            violations.append("Stop-loss is required.")

        if proposal.entry_price is not None and proposal.stop_loss is not None:
            if proposal.action == ForexAction.LONG:
                if proposal.stop_loss >= proposal.entry_price:
                    violations.append(
                        f"LONG stop-loss ({proposal.stop_loss}) must be below entry ({proposal.entry_price})."
                    )
                if proposal.take_profit_1 is not None and proposal.take_profit_1 <= proposal.entry_price:
                    violations.append(
                        f"LONG take-profit ({proposal.take_profit_1}) must be above entry ({proposal.entry_price})."
                    )
            elif proposal.action == ForexAction.SHORT:
                if proposal.stop_loss <= proposal.entry_price:
                    violations.append(
                        f"SHORT stop-loss ({proposal.stop_loss}) must be above entry ({proposal.entry_price})."
                    )
                if proposal.take_profit_1 is not None and proposal.take_profit_1 >= proposal.entry_price:
                    violations.append(
                        f"SHORT take-profit ({proposal.take_profit_1}) must be below entry ({proposal.entry_price})."
                    )

            sl_pips = proposal.sl_pips
            if sl_pips is None:
                ps = pip_size_for(proposal.pair)
                sl_pips = round(abs(proposal.entry_price - proposal.stop_loss) / ps, 1)

            if sl_pips < eff_limits.min_sl_pips:
                violations.append(
                    f"Stop distance ({sl_pips:.1f} pips) < min limit ({eff_limits.min_sl_pips:.1f} pips)."
                )
            elif sl_pips > eff_limits.max_sl_pips:
                violations.append(
                    f"Stop distance ({sl_pips:.1f} pips) > max limit ({eff_limits.max_sl_pips:.1f} pips)."
                )

        if proposal.risk_reward_ratio is not None and proposal.risk_reward_ratio < eff_limits.hard_min_risk_reward_ratio:
            violations.append(
                f"R:R ({proposal.risk_reward_ratio:.2f}:1) < hard min ({eff_limits.hard_min_risk_reward_ratio:.1f}:1)."
            )

        return len(violations) == 0, violations

    def clamp_proposal_to_limits(
        self,
        proposal: ForexTraderProposal,
        limits: ForexRiskLimits | None = None,
        broker_digits: int | None = None,
    ) -> ForexTraderProposal:
        """Return a copy of the proposal adjusted to strictly satisfy limits.

        - Clamps risk percent to max_risk_percent.
        - Clamps stop-loss to satisfy [min_sl_pips, max_sl_pips].
        - Adjusts take-profit to satisfy min_risk_reward_ratio.
        """
        eff_limits = limits or self.default_limits
        if proposal.action == ForexAction.NO_TRADE or proposal.entry_price is None or proposal.stop_loss is None:
            return proposal

        digits = _resolve_digits(proposal.pair, broker_digits)
        pip_size = pip_size_for(proposal.pair)
        entry = proposal.entry_price
        sl = proposal.stop_loss
        tp = proposal.take_profit_1

        # Adjust SL if out of bounds
        current_sl_pips = round(abs(entry - sl) / pip_size, 1)
        clamped_sl_pips = max(eff_limits.min_sl_pips, min(current_sl_pips, eff_limits.max_sl_pips))

        if clamped_sl_pips != current_sl_pips:
            sl_dist = clamped_sl_pips * pip_size
            sl = round(entry - sl_dist if proposal.action == ForexAction.LONG else entry + sl_dist, digits)

        # Adjust TP if R:R < min_risk_reward_ratio
        actual_sl_dist = abs(entry - sl)
        target_tp_dist = actual_sl_dist * eff_limits.min_risk_reward_ratio
        if tp is None:
            tp = round(entry + target_tp_dist if proposal.action == ForexAction.LONG else entry - target_tp_dist, digits)
        else:
            current_tp_dist = abs(tp - entry)
            if current_tp_dist < target_tp_dist:
                tp = round(entry + target_tp_dist if proposal.action == ForexAction.LONG else entry - target_tp_dist, digits)

        # Clamp risk percent
        risk_pct = min(
            proposal.suggested_risk_percent or eff_limits.default_risk_percent,
            eff_limits.max_risk_percent,
        )

        return ForexTraderProposal(
            pair=proposal.pair,
            action=proposal.action,
            order_type=proposal.order_type,
            setup_type=proposal.setup_type,
            timeframe=proposal.timeframe,
            entry_price=entry,
            entry_zone_low=proposal.entry_zone_low,
            entry_zone_high=proposal.entry_zone_high,
            stop_loss=sl,
            take_profit_1=tp,
            take_profit_2=proposal.take_profit_2,
            suggested_risk_percent=risk_pct,
            suggested_lot_size=proposal.suggested_lot_size,
            confluence_factors=proposal.confluence_factors,
            invalidation_condition=proposal.invalidation_condition,
            reasoning=proposal.reasoning,
            trade_rationale_summary=proposal.trade_rationale_summary,
        )


# ---------------------------------------------------------------------------
# Module-level Convenience Helper
# ---------------------------------------------------------------------------


def validate_forex_proposal(
    proposal: ForexTraderProposal,
    curr_date: str | None = None,
    curr_time_utc: str | None = None,
    current_spread_pips: float | None = None,
    atr_pips: float | None = None,
    account_balance: float | None = None,
    account_currency: str = "USD",
    limits: ForexRiskLimits | None = None,
    broker_digits: int | None = None,
) -> ForexRiskDecision:
    """Validate a ForexTraderProposal using the default ForexRiskEngine."""
    engine = ForexRiskEngine(default_limits=limits)
    return engine.validate_proposal(
        proposal=proposal,
        curr_date=curr_date,
        curr_time_utc=curr_time_utc,
        current_spread_pips=current_spread_pips,
        atr_pips=atr_pips,
        account_balance=account_balance,
        account_currency=account_currency,
        limits=limits,
        broker_digits=broker_digits,
    )
