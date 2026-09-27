"""Institutional Forex Position Sizing & Margin Engine (Phase 11).

Provides deterministic position sizing models and portfolio risk controls:
1. Multiple Position Sizing Models:
   - Fixed Risk Percentage: Standard institutional risk-of-equity allocation (e.g. 1.0% or 2.0%).
   - Fixed Monetary Amount: Fixed dollar risk allocation (e.g. $250.00).
   - Volatility-Adjusted / ATR Sizing: Scales sizing inversely to market volatility to normalize dollar risk.
   - Kelly Criterion / Fractional Kelly: Mathematically optimal capital growth based on win-rate and payoff ratio.
   - Account Balance Step Sizing: Dynamic bracketed sizing scaled to account equity capitalization.
2. Broker Execution & Volume Normalization:
   - Normalization to broker volume steps (e.g. 0.01 micro-lots).
   - Downward flooring to strictly avoid exceeding risk thresholds due to IEEE-754 floating point arithmetic.
   - Bounds clamping between broker min_volume (0.01) and max_volume (100.0).
3. Margin, Leverage & Free Margin Verification:
   - Precise required margin calculations for USD-quoted, USD-base, and cross-currency pairs.
   - Pre-trade margin adequacy and projected account margin level checks.
4. Portfolio Currency Exposure & Correlation Safeguards:
   - Single-currency net exposure tracking across open positions (e.g. cumulative USD/EUR risk).
   - Portfolio total open risk aggregation against portfolio risk ceiling.
   - High-correlation pair adjustments (e.g. EURUSD + GBPUSD long co-exposure discount).
"""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from tradingagents.agents.schemas_forex import ForexAction, ForexTraderProposal
from tradingagents.forex.domain import ForexPair, get_forex_pair
from tradingagents.forex.pips import (
    pip_size_for,
    pip_value_in_account_currency,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class PositionSizingMethod(str, Enum):
    """Supported position sizing models."""

    FIXED_RISK_PERCENT = "FIXED_RISK_PERCENT"
    FIXED_MONETARY_AMOUNT = "FIXED_MONETARY_AMOUNT"
    VOLATILITY_ADJUSTED = "VOLATILITY_ADJUSTED"
    KELLY_CRITERION = "KELLY_CRITERION"
    BALANCE_STEP = "BALANCE_STEP"

    @classmethod
    def from_str(cls, val: Any) -> PositionSizingMethod:
        if isinstance(val, PositionSizingMethod):
            return val
        s = str(val).strip().upper().replace(" ", "_").replace("-", "_")
        for member in cls:
            if member.value == s or member.name == s:
                return member
        return cls.FIXED_RISK_PERCENT


# ---------------------------------------------------------------------------
# Canonical FX Correlation Matrix (G8 Majors & Crosses)
# ---------------------------------------------------------------------------

# Baseline institutional 1-year rolling correlation estimates
CANONICAL_FX_CORRELATIONS: dict[tuple[str, str], float] = {
    # EURUSD correlations
    ("EURUSD", "GBPUSD"): 0.82,
    ("EURUSD", "USDCHF"): -0.88,
    ("EURUSD", "AUDUSD"): 0.68,
    ("EURUSD", "NZDUSD"): 0.64,
    ("EURUSD", "USDCAD"): -0.62,
    ("EURUSD", "USDJPY"): -0.25,
    ("EURUSD", "EURJPY"): 0.55,
    ("EURUSD", "GBPJPY"): 0.48,
    ("EURUSD", "EURGBP"): 0.42,
    # GBPUSD correlations
    ("GBPUSD", "USDCHF"): -0.78,
    ("GBPUSD", "AUDUSD"): 0.70,
    ("GBPUSD", "NZDUSD"): 0.66,
    ("GBPUSD", "USDCAD"): -0.58,
    ("GBPUSD", "USDJPY"): -0.15,
    ("GBPUSD", "EURGBP"): -0.72,
    ("GBPUSD", "GBPJPY"): 0.65,
    # Commodity pairs
    ("AUDUSD", "NZDUSD"): 0.86,
    ("AUDUSD", "USDCAD"): -0.68,
    ("AUDUSD", "USDJPY"): 0.35,
    # JPY Crosses
    ("USDJPY", "EURJPY"): 0.74,
    ("USDJPY", "GBPJPY"): 0.72,
    ("EURJPY", "GBPJPY"): 0.88,
}


def get_pair_correlation(pair_a: str, pair_b: str) -> float:
    """Return historical correlation coefficient between two FX pairs.

    Returns a value between -1.0 and 1.0 (default 0.0 for unrelated/exotic pairs).
    """
    sym_a = pair_a.strip().upper()[:6]
    sym_b = pair_b.strip().upper()[:6]

    if sym_a == sym_b:
        return 1.0

    if (sym_a, sym_b) in CANONICAL_FX_CORRELATIONS:
        return CANONICAL_FX_CORRELATIONS[(sym_a, sym_b)]
    if (sym_b, sym_a) in CANONICAL_FX_CORRELATIONS:
        return CANONICAL_FX_CORRELATIONS[(sym_b, sym_a)]

    return 0.0


# ---------------------------------------------------------------------------
# Account & Broker Configuration Models
# ---------------------------------------------------------------------------


class ForexAccountProfile(BaseModel):
    """Trader account parameters, capitalization, and leverage profile."""

    balance: float = Field(default=10000.0, gt=0.0, description="Account cash balance")
    equity: float = Field(default=10000.0, gt=0.0, description="Current account equity")
    free_margin: float = Field(default=10000.0, ge=0.0, description="Available free margin for new trades")
    currency: str = Field(default="USD", description="Account base deposit currency (e.g. USD, EUR, GBP)")
    leverage: float = Field(default=100.0, ge=1.0, description="Account leverage ratio (e.g. 100 for 1:100)")
    max_account_risk_percent: float = Field(
        default=6.0,
        ge=0.5,
        le=20.0,
        description="Maximum cumulative open portfolio risk percentage across all trades",
    )
    max_single_trade_risk_percent: float = Field(
        default=2.0,
        ge=0.1,
        le=10.0,
        description="Maximum authorized risk percentage for any single trade",
    )
    max_currency_exposure_percent: float = Field(
        default=5.0,
        ge=1.0,
        le=25.0,
        description="Maximum net directional exposure risk on any individual currency (e.g. USD)",
    )
    margin_call_level: float = Field(default=100.0, description="Broker margin call level (%)")
    stop_out_level: float = Field(default=50.0, description="Broker liquidation stop-out level (%)")


class BrokerExecutionConstraints(BaseModel):
    """Broker trading environment parameters and volume rules."""

    min_volume: float = Field(default=0.01, ge=0.001, description="Broker minimum volume (0.01 micro lot)")
    max_volume: float = Field(default=100.0, ge=0.1, description="Broker maximum allowed volume in lots")
    volume_step: float = Field(default=0.01, ge=0.001, description="Broker volume increment step")
    contract_size: float = Field(default=100000.0, gt=0.0, description="Standard lot contract size (100,000 units)")


class OpenPosition(BaseModel):
    """Active open position in the trader's portfolio."""

    position_id: str = Field(description="Unique position identifier")
    pair: str = Field(description="Currency pair symbol, e.g. EURUSD")
    action: ForexAction = Field(description="Direction of the open trade (LONG/SHORT)")
    lots: float = Field(ge=0.0, description="Position size in standard lots")
    entry_price: float = Field(gt=0.0, description="Execution price")
    stop_loss: float = Field(gt=0.0, description="Stop-loss level")
    risk_amount: float = Field(ge=0.0, description="Committed dollar risk at stop-loss")


# ---------------------------------------------------------------------------
# Position Sizing Result Model
# ---------------------------------------------------------------------------


class PositionSizingResult(BaseModel):
    """Complete institutional position sizing specification."""

    pair: str = Field(description="Forex pair symbol")
    action: ForexAction = Field(description="Transaction direction")
    sizing_method: PositionSizingMethod = Field(description="Position sizing model used")
    recommended_lot_size: float = Field(ge=0.0, description="Broker-normalized, step-rounded lot size")
    raw_lot_size: float = Field(ge=0.0, description="Theoretical unrounded calculated lot size")
    units: float = Field(ge=0.0, description="Calculated currency units (e.g. 10,000 for 0.10 lots)")
    risk_amount: float = Field(ge=0.0, description="Absolute risk in account currency at stop-loss")
    risk_percent: float = Field(ge=0.0, description="Risk as a percentage of account equity")
    pip_value_per_lot: float = Field(gt=0.0, description="Value of 1 pip per standard lot in account currency")
    total_pip_value: float = Field(ge=0.0, description="Total pip value for recommended position size")
    stop_distance_pips: float = Field(gt=0.0, description="Stop-loss distance in pips")
    margin_required: float = Field(ge=0.0, description="Required margin in account currency")
    free_margin_remaining: float = Field(description="Projected free margin after position entry")
    margin_level_percent: float = Field(ge=0.0, description="Projected account margin level percentage")
    leverage_used: float = Field(ge=0.0, description="Effective leverage of this position (notional / equity)")
    adjustments_applied: list[str] = Field(default_factory=list, description="Audit log of sizing adjustments")
    is_executable: bool = Field(description="True if trade satisfies margin, risk, and volume constraints")
    rejection_reason: str | None = Field(default=None, description="Detailed reason if sizing is not executable")

    @property
    def lot_size(self) -> float:
        """Alias for recommended_lot_size."""
        return self.recommended_lot_size

    @property
    def required_margin(self) -> float:
        """Alias for margin_required."""
        return self.margin_required


# ---------------------------------------------------------------------------
# Margin Calculation Engine
# ---------------------------------------------------------------------------


def calculate_required_margin(
    pair: ForexPair | str,
    lot_size: float,
    entry_price: float,
    leverage: float,
    account_currency: str = "USD",
    current_quote_price: float | None = None,
    contract_size: float = 100000.0,
) -> float:
    """Compute the required margin in account currency for an FX position.

    Formula:
      Notional Value in Base Currency = lot_size * contract_size
      Notional in Account Currency:
        - Base == Account (e.g. USDJPY with USD account): notional_base
        - Quote == Account (e.g. EURUSD with USD account): notional_base * entry_price
        - Cross Pair (e.g. EURGBP with USD account): notional_base * base_to_usd
      Required Margin = Notional in Account Currency / leverage
    """
    if lot_size <= 0 or leverage <= 0 or entry_price <= 0:
        return 0.0

    pair_obj = pair if isinstance(pair, ForexPair) else get_forex_pair(pair)
    base_curr = pair_obj.base_currency if pair_obj else str(pair)[:3].upper()
    quote_curr = pair_obj.quote_currency if pair_obj else str(pair)[3:6].upper()
    acc_curr = account_currency.upper()

    cs = pair_obj.contract_size if pair_obj else contract_size
    notional_base = lot_size * cs

    # Case 1: Base currency is account currency (e.g. USDJPY, USDCAD from USD account)
    if base_curr == acc_curr:
        notional_account = notional_base

    # Case 2: Quote currency is account currency (e.g. EURUSD, GBPUSD, AUDUSD from USD account)
    elif quote_curr == acc_curr:
        notional_account = notional_base * entry_price

    # Case 3: Cross pair (e.g. EURJPY, GBPJPY from USD account)
    else:
        # If current_quote_price is supplied, it is the rate of quote vs account (e.g. USDJPY for EURJPY)
        # notional_account = notional_base * entry_price / current_quote_price
        if current_quote_price and current_quote_price > 0:
            notional_account = (notional_base * entry_price) / current_quote_price
        else:
            # Fallback approximation: use entry_price directly
            notional_account = notional_base * entry_price

    required_margin = notional_account / leverage
    return round(required_margin, 2)


# ---------------------------------------------------------------------------
# Volatility-Adjusted Sizing Helper
# ---------------------------------------------------------------------------


def calculate_volatility_adjusted_risk_percent(
    base_risk_percent: float,
    atr_pips: float,
    baseline_atr_pips: float = 50.0,
    min_scale: float = 0.5,
    max_scale: float = 1.5,
) -> tuple[float, float]:
    """Compute volatility-scaled risk percentage based on current ATR relative to baseline.

    When volatility is high (ATR > baseline), position risk is scaled down.
    When volatility is low (ATR < baseline), position risk is scaled up (within limits).

    Returns:
        (adjusted_risk_percent, volatility_scaling_factor)
    """
    if atr_pips <= 0:
        return base_risk_percent, 1.0

    vol_factor = baseline_atr_pips / atr_pips
    clamped_factor = max(min_scale, min(vol_factor, max_scale))
    adjusted_risk = round(base_risk_percent * clamped_factor, 2)

    return adjusted_risk, round(clamped_factor, 3)


# ---------------------------------------------------------------------------
# Kelly Criterion Sizing Helper
# ---------------------------------------------------------------------------


def calculate_kelly_risk_percent(
    win_rate: float,
    win_loss_ratio: float,
    kelly_fraction: float = 0.25,
    max_risk_percent: float = 2.0,
) -> tuple[float, float]:
    """Compute optimal risk percentage using fractional Kelly Criterion.

    Formula:
        K = [ W * R - (1 - W) ] / R
    where:
        W = historical win rate in (0.0, 1.0)
        R = payoff ratio (average win / average loss)

    To protect against estimation error and drawdown, Kelly is multiplied by
    ``kelly_fraction`` (typically 0.25 for quarter-Kelly or 0.50 for half-Kelly)
    and capped at ``max_risk_percent``.

    Returns:
        (recommended_risk_percent, full_kelly_fraction)
    """
    if not (0.0 < win_rate < 1.0) or win_loss_ratio <= 0:
        return 0.0, 0.0

    full_k = (win_rate * win_loss_ratio - (1.0 - win_rate)) / win_loss_ratio

    if full_k <= 0:
        return 0.0, round(full_k, 4)

    scaled_k = full_k * kelly_fraction
    risk_pct = min(round(scaled_k * 100.0, 2), max_risk_percent)

    return risk_pct, round(full_k, 4)


# ---------------------------------------------------------------------------
# Portfolio Currency Exposure Engine
# ---------------------------------------------------------------------------


def calculate_portfolio_currency_exposure(
    open_positions: Sequence[OpenPosition],
    new_pair: str | None = None,
    new_action: ForexAction | None = None,
    new_lots: float = 0.0,
) -> dict[str, float]:
    """Calculate net currency exposure (in standard lots) across all positions.

    A LONG EURUSD trade adds +lots EUR and -lots USD.
    A SHORT GBPJPY trade adds -lots GBP and +lots JPY.

    Returns:
        dict mapping currency ISO code -> net lot exposure (positive = net long, negative = net short)
    """
    exposures: dict[str, float] = {}

    def _add_trade(pair_sym: str, act: ForexAction, volume: float) -> None:
        if volume <= 0 or act == ForexAction.NO_TRADE:
            return
        pair_obj = get_forex_pair(pair_sym)
        base = pair_obj.base_currency if pair_obj else pair_sym[:3].upper()
        quote = pair_obj.quote_currency if pair_obj else pair_sym[3:6].upper()

        if act == ForexAction.LONG:
            exposures[base] = round(exposures.get(base, 0.0) + volume, 3)
            exposures[quote] = round(exposures.get(quote, 0.0) - volume, 3)
        elif act == ForexAction.SHORT:
            exposures[base] = round(exposures.get(base, 0.0) - volume, 3)
            exposures[quote] = round(exposures.get(quote, 0.0) + volume, 3)

    for pos in open_positions:
        _add_trade(pos.pair, pos.action, pos.lots)

    if new_pair and new_action and new_lots > 0:
        _add_trade(new_pair, new_action, new_lots)

    return exposures


def calculate_correlation_exposure_factor(
    new_pair: str,
    new_action: ForexAction,
    open_positions: Sequence[OpenPosition],
    correlation_threshold: float = 0.70,
) -> tuple[float, list[str]]:
    """Compute position scaling discount based on portfolio pair correlations.

    If the trader is already long EURUSD and opens a long GBPUSD (correlation ~0.82),
    accumulating full size would double risk on the USD weakening macro factor.
    This applies a prudent correlation discount (e.g. 0.70x).

    Returns:
        (scaling_factor, list_of_correlated_positions)
    """
    if not open_positions or new_action == ForexAction.NO_TRADE:
        return 1.0, []

    correlated_pairs: list[str] = []
    max_corr_impact = 0.0

    for pos in open_positions:
        if pos.lots <= 0:
            continue
        corr = get_pair_correlation(new_pair, pos.pair)

        # Same direction with high positive correlation
        if new_action == pos.action and corr >= correlation_threshold:
            correlated_pairs.append(f"{pos.pair} ({pos.action.value}, corr: {corr:+.2f})")
            max_corr_impact = max(max_corr_impact, corr)

        # Opposite direction with strong inverse correlation
        elif new_action != pos.action and corr <= -correlation_threshold:
            correlated_pairs.append(f"{pos.pair} ({pos.action.value}, corr: {corr:+.2f})")
            max_corr_impact = max(max_corr_impact, abs(corr))

    if not correlated_pairs:
        return 1.0, []

    # Correlation scaling formula: scale down by 25% to 40% based on correlation magnitude
    scale = max(0.60, round(1.0 - (max_corr_impact * 0.40), 2))
    return scale, correlated_pairs


# ---------------------------------------------------------------------------
# Forex Position Sizing Engine
# ---------------------------------------------------------------------------


class ForexPositionSizingEngine:
    """Institutional deterministic position sizing and margin management engine."""

    def __init__(
        self,
        default_account: ForexAccountProfile | None = None,
        default_constraints: BrokerExecutionConstraints | None = None,
    ) -> None:
        self.default_account = default_account or ForexAccountProfile()
        self.default_constraints = default_constraints or BrokerExecutionConstraints()

    def compute_size(
        self,
        pair: str,
        action: ForexAction,
        entry_price: float,
        stop_loss: float,
        sizing_method: PositionSizingMethod = PositionSizingMethod.FIXED_RISK_PERCENT,
        account: ForexAccountProfile | None = None,
        constraints: BrokerExecutionConstraints | None = None,
        risk_percent: float | None = None,
        monetary_amount: float | None = None,
        atr_pips: float | None = None,
        baseline_atr_pips: float = 50.0,
        win_rate: float | None = None,
        win_loss_ratio: float | None = None,
        kelly_fraction: float = 0.25,
        open_positions: Sequence[OpenPosition] | None = None,
        current_quote_price: float | None = None,
    ) -> PositionSizingResult:
        """Calculate complete institutional position sizing and margin requirements.

        Performs full mathematical derivation, broker step-rounding, leverage,
        margin adequacy, and portfolio correlation safeguards.
        """
        acc = account or self.default_account
        cons = constraints or self.default_constraints
        positions = open_positions or []

        adjustments: list[str] = []

        # -------------------------------------------------------------------
        # 1. Action Check: NO_TRADE setups receive zero allocation
        # -------------------------------------------------------------------
        if action == ForexAction.NO_TRADE:
            return PositionSizingResult(
                pair=pair,
                action=ForexAction.NO_TRADE,
                sizing_method=sizing_method,
                recommended_lot_size=0.0,
                raw_lot_size=0.0,
                units=0.0,
                risk_amount=0.0,
                risk_percent=0.0,
                pip_value_per_lot=10.0,
                total_pip_value=0.0,
                stop_distance_pips=1.0,
                margin_required=0.0,
                free_margin_remaining=acc.free_margin,
                margin_level_percent=9999.0,
                leverage_used=0.0,
                adjustments_applied=["Setup evaluated as NO_TRADE. 0 lots assigned."],
                is_executable=True,
                rejection_reason=None,
            )

        # -------------------------------------------------------------------
        # 2. Input Sanity Checks
        # -------------------------------------------------------------------
        if entry_price <= 0 or stop_loss <= 0 or entry_price == stop_loss:
            return self._build_unexecutable(
                pair=pair,
                action=action,
                method=sizing_method,
                acc=acc,
                reason=f"Invalid price levels: entry={entry_price}, stop_loss={stop_loss}.",
            )

        pip_sz = pip_size_for(pair)
        stop_dist_price = abs(entry_price - stop_loss)
        stop_dist_pips = round(stop_dist_price / pip_sz, 1)

        if stop_dist_pips <= 0:
            return self._build_unexecutable(
                pair=pair,
                action=action,
                method=sizing_method,
                acc=acc,
                reason="Stop loss distance is zero pips.",
            )

        pip_val_per_lot = pip_value_in_account_currency(
            pair=pair,
            lot_size=1.0,
            account_currency=acc.currency,
            current_quote_price=current_quote_price,
        )

        if pip_val_per_lot <= 0:
            return self._build_unexecutable(
                pair=pair,
                action=action,
                method=sizing_method,
                acc=acc,
                reason=f"Unable to derive pip value for {pair} in {acc.currency}.",
            )

        # -------------------------------------------------------------------
        # 3. Model-Specific Risk Allocation Determination
        # -------------------------------------------------------------------
        eff_risk_percent = acc.max_single_trade_risk_percent

        if sizing_method == PositionSizingMethod.FIXED_RISK_PERCENT:
            target_risk = risk_percent if risk_percent is not None else 1.0
            if target_risk > acc.max_single_trade_risk_percent:
                adjustments.append(
                    f"Requested risk ({target_risk:.1f}%) clamped to account limit ({acc.max_single_trade_risk_percent:.1f}%)."
                )
                eff_risk_percent = acc.max_single_trade_risk_percent
            else:
                eff_risk_percent = target_risk

        elif sizing_method == PositionSizingMethod.FIXED_MONETARY_AMOUNT:
            target_amount = monetary_amount if monetary_amount is not None else (acc.equity * 0.01)
            eff_risk_percent = (target_amount / acc.equity) * 100.0
            if eff_risk_percent > acc.max_single_trade_risk_percent:
                adjustments.append(
                    f"Fixed amount (${target_amount:.2f} = {eff_risk_percent:.1f}%) clamped to single-trade limit ({acc.max_single_trade_risk_percent:.1f}%)."
                )
                eff_risk_percent = acc.max_single_trade_risk_percent

        elif sizing_method == PositionSizingMethod.VOLATILITY_ADJUSTED:
            base_risk = risk_percent if risk_percent is not None else 1.0
            actual_atr = atr_pips if atr_pips is not None and atr_pips > 0 else stop_dist_pips
            vol_risk, vol_factor = calculate_volatility_adjusted_risk_percent(
                base_risk_percent=base_risk,
                atr_pips=actual_atr,
                baseline_atr_pips=baseline_atr_pips,
            )
            eff_risk_percent = min(vol_risk, acc.max_single_trade_risk_percent)
            adjustments.append(
                f"Volatility adjustment: ATR {actual_atr:.1f} vs baseline {baseline_atr_pips:.1f} (scaling {vol_factor:.2f}x) -> risk {eff_risk_percent:.2f}%."
            )

        elif sizing_method == PositionSizingMethod.KELLY_CRITERION:
            w = win_rate if win_rate is not None else 0.55
            r = win_loss_ratio if win_loss_ratio is not None else 1.5
            k_risk, full_k = calculate_kelly_risk_percent(
                win_rate=w,
                win_loss_ratio=r,
                kelly_fraction=kelly_fraction,
                max_risk_percent=acc.max_single_trade_risk_percent,
            )
            if k_risk <= 0:
                return self._build_unexecutable(
                    pair=pair,
                    action=action,
                    method=sizing_method,
                    acc=acc,
                    reason=f"Negative Kelly edge ({full_k:.2f}) for win_rate={w:.2f}, R:R={r:.2f}.",
                )
            eff_risk_percent = k_risk
            adjustments.append(
                f"Fractional Kelly ({kelly_fraction:.2f}x of {full_k:.2f} edge) -> risk {eff_risk_percent:.2f}%."
            )

        elif sizing_method == PositionSizingMethod.BALANCE_STEP:
            # Capital-tier bracketed sizing
            if acc.equity < 5000.0:
                eff_risk_percent = 0.5
            elif acc.equity < 25000.0:
                eff_risk_percent = 1.0
            elif acc.equity < 100000.0:
                eff_risk_percent = 1.5
            else:
                eff_risk_percent = 2.0
            adjustments.append(f"Account capitalization tier bracket applied: {eff_risk_percent:.1f}% risk.")

        # -------------------------------------------------------------------
        # 4. Portfolio Correlation Adjustment
        # -------------------------------------------------------------------
        corr_scale, correlated_positions = calculate_correlation_exposure_factor(
            new_pair=pair,
            new_action=action,
            open_positions=positions,
        )
        if corr_scale < 1.0:
            eff_risk_percent = round(eff_risk_percent * corr_scale, 2)
            adjustments.append(
                f"Correlation adjustment ({corr_scale:.2f}x) applied due to co-exposure with: {', '.join(correlated_positions)}."
            )

        # -------------------------------------------------------------------
        # 5. Position Sizing Arithmetic & Normalization
        # -------------------------------------------------------------------
        target_risk_amount = acc.equity * (eff_risk_percent / 100.0)
        raw_lots = target_risk_amount / (stop_dist_pips * pip_val_per_lot)

        # Broker volume step floor rounding to strictly avoid risk overrun
        step = cons.volume_step if cons.volume_step > 0 else 0.01
        step_factor = 1.0 / step
        clamped_lots = math.floor(round(raw_lots * step_factor, 8)) / step_factor
        clamped_lots = round(clamped_lots, 4)

        if clamped_lots < cons.min_volume:
            return self._build_unexecutable(
                pair=pair,
                action=action,
                method=sizing_method,
                acc=acc,
                reason=(
                    f"Calculated lot size ({raw_lots:.4f}) is below broker minimum volume "
                    f"({cons.min_volume} lots). Requires larger stop or higher capital."
                ),
            )

        if clamped_lots > cons.max_volume:
            adjustments.append(f"Lot size clamped from {clamped_lots:.2f} to broker maximum {cons.max_volume:.2f} lots.")
            clamped_lots = cons.max_volume

        # Re-derive actual monetary risk and percentage at normalized volume
        actual_risk_amount = round(clamped_lots * stop_dist_pips * pip_val_per_lot, 2)
        actual_risk_pct = round((actual_risk_amount / acc.equity) * 100.0, 2)
        units = round(clamped_lots * cons.contract_size, 1)
        total_pip_val = round(clamped_lots * pip_val_per_lot, 2)

        # -------------------------------------------------------------------
        # 6. Margin & Leverage Verification
        # -------------------------------------------------------------------
        margin_needed = calculate_required_margin(
            pair=pair,
            lot_size=clamped_lots,
            entry_price=entry_price,
            leverage=acc.leverage,
            account_currency=acc.currency,
            current_quote_price=current_quote_price,
            contract_size=cons.contract_size,
        )

        remaining_free_margin = round(acc.free_margin - margin_needed, 2)

        if margin_needed > acc.free_margin:
            return self._build_unexecutable(
                pair=pair,
                action=action,
                method=sizing_method,
                acc=acc,
                reason=(
                    f"Insufficient free margin: Required ${margin_needed:.2f} exceeds "
                    f"available free margin ${acc.free_margin:.2f}."
                ),
            )

        # Projected account margin level (%)
        # Margin level = (Equity / Used Margin) * 100
        total_projected_used_margin = (acc.equity - acc.free_margin) + margin_needed
        if total_projected_used_margin > 0:
            proj_margin_level = round((acc.equity / total_projected_used_margin) * 100.0, 1)
        else:
            proj_margin_level = 9999.0

        if proj_margin_level < acc.margin_call_level:
            return self._build_unexecutable(
                pair=pair,
                action=action,
                method=sizing_method,
                acc=acc,
                reason=(
                    f"Trade would push margin level ({proj_margin_level:.1f}%) below "
                    f"margin call threshold ({acc.margin_call_level:.1f}%)."
                ),
            )

        # Effective position leverage (notional value / equity)
        pair_obj = get_forex_pair(pair)
        base_curr = pair_obj.base_currency if pair_obj else pair[:3].upper()
        if base_curr == acc.currency:
            notional = clamped_lots * cons.contract_size
        else:
            notional = clamped_lots * cons.contract_size * entry_price
        leverage_used = round(notional / acc.equity, 2)

        # -------------------------------------------------------------------
        # 7. Portfolio Risk Capacity Check
        # -------------------------------------------------------------------
        current_open_risk = sum(p.risk_amount for p in positions)
        total_open_risk = current_open_risk + actual_risk_amount
        total_open_risk_pct = (total_open_risk / acc.equity) * 100.0

        if total_open_risk_pct > acc.max_account_risk_percent:
            return self._build_unexecutable(
                pair=pair,
                action=action,
                method=sizing_method,
                acc=acc,
                reason=(
                    f"Cumulative portfolio risk ({total_open_risk_pct:.1f}%) would exceed "
                    f"account maximum risk ceiling of {acc.max_account_risk_percent:.1f}%."
                ),
            )

        return PositionSizingResult(
            pair=pair,
            action=action,
            sizing_method=sizing_method,
            recommended_lot_size=clamped_lots,
            raw_lot_size=round(raw_lots, 4),
            units=units,
            risk_amount=actual_risk_amount,
            risk_percent=actual_risk_pct,
            pip_value_per_lot=round(pip_val_per_lot, 2),
            total_pip_value=total_pip_val,
            stop_distance_pips=stop_dist_pips,
            margin_required=margin_needed,
            free_margin_remaining=remaining_free_margin,
            margin_level_percent=proj_margin_level,
            leverage_used=leverage_used,
            adjustments_applied=adjustments,
            is_executable=True,
            rejection_reason=None,
        )

    def size_proposal(
        self,
        proposal: ForexTraderProposal,
        sizing_method: PositionSizingMethod = PositionSizingMethod.FIXED_RISK_PERCENT,
        account: ForexAccountProfile | None = None,
        constraints: BrokerExecutionConstraints | None = None,
        atr_pips: float | None = None,
        win_rate: float | None = None,
        win_loss_ratio: float | None = None,
        open_positions: Sequence[OpenPosition] | None = None,
        current_quote_price: float | None = None,
    ) -> PositionSizingResult:
        """Convenience method to compute sizing directly from a ForexTraderProposal."""
        if proposal.action == ForexAction.NO_TRADE or proposal.entry_price is None or proposal.stop_loss is None:
            return self.compute_size(
                pair=proposal.pair,
                action=proposal.action,
                entry_price=proposal.entry_price or 1.0,
                stop_loss=proposal.stop_loss or 1.0,
                sizing_method=sizing_method,
                account=account,
                constraints=constraints,
                open_positions=open_positions,
            )

        return self.compute_size(
            pair=proposal.pair,
            action=proposal.action,
            entry_price=proposal.entry_price,
            stop_loss=proposal.stop_loss,
            sizing_method=sizing_method,
            account=account,
            constraints=constraints,
            risk_percent=proposal.suggested_risk_percent,
            atr_pips=atr_pips,
            win_rate=win_rate,
            win_loss_ratio=win_loss_ratio or proposal.risk_reward_ratio,
            open_positions=open_positions,
            current_quote_price=current_quote_price,
        )

    def _build_unexecutable(
        self,
        pair: str,
        action: ForexAction,
        method: PositionSizingMethod,
        acc: ForexAccountProfile,
        reason: str,
    ) -> PositionSizingResult:
        """Construct a standardized unexecutable PositionSizingResult."""
        return PositionSizingResult(
            pair=pair,
            action=action,
            sizing_method=method,
            recommended_lot_size=0.0,
            raw_lot_size=0.0,
            units=0.0,
            risk_amount=0.0,
            risk_percent=0.0,
            pip_value_per_lot=10.0,
            total_pip_value=0.0,
            stop_distance_pips=1.0,
            margin_required=0.0,
            free_margin_remaining=acc.free_margin,
            margin_level_percent=9999.0,
            leverage_used=0.0,
            adjustments_applied=[],
            is_executable=False,
            rejection_reason=reason,
        )


# ---------------------------------------------------------------------------
# Module-level Convenience Helper
# ---------------------------------------------------------------------------


def calculate_forex_position_size(
    pair: str,
    action: ForexAction,
    entry_price: float,
    stop_loss: float,
    account_balance: float = 10000.0,
    risk_percent: float = 1.0,
    leverage: float = 100.0,
    account_currency: str = "USD",
    sizing_method: PositionSizingMethod = PositionSizingMethod.FIXED_RISK_PERCENT,
    atr_pips: float | None = None,
    open_positions: Sequence[OpenPosition] | None = None,
) -> PositionSizingResult:
    """Convenience helper to compute position size and margin using default engine."""
    account = ForexAccountProfile(
        balance=account_balance,
        equity=account_balance,
        free_margin=account_balance,
        currency=account_currency,
        leverage=leverage,
    )
    engine = ForexPositionSizingEngine(default_account=account)
    return engine.compute_size(
        pair=pair,
        action=action,
        entry_price=entry_price,
        stop_loss=stop_loss,
        sizing_method=sizing_method,
        risk_percent=risk_percent,
        atr_pips=atr_pips,
        open_positions=open_positions,
    )
