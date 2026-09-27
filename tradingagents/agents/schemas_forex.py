"""Forex-specific structured Pydantic schemas for trading proposals and risk decisions (Phase 9).

Provides:
- ``ForexAction``: LONG, SHORT, NO_TRADE transaction directions.
- ``OrderType``: MARKET, BUY_LIMIT, SELL_LIMIT, BUY_STOP, SELL_STOP.
- ``SetupType``: TREND_CONTINUATION, BREAKOUT, REVERSAL, PULLBACK, RANGE_BOUND, NEWS_MOMENTUM, NEWS_FADE.
- ``ForexRiskDecisionAction``: APPROVE, REJECT, MODIFY.
- ``ForexTraderProposal``: Complete institutional Forex proposal with structured entry, SL, TP1, TP2, pip distances, and R:R ratios.
- ``ForexRiskDecision``: Formal risk management evaluation, validation checks, and volume allocations.
- Render helpers and bidirectional adapters to stock ``TraderProposal`` for seamless backward compatibility.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from tradingagents.agents.schemas import (
    TraderAction,
    TraderProposal,
    _coerce_optional_float,
)
from tradingagents.forex.pips import pip_size_for

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ForexAction(str, Enum):
    """Institutional currency transaction direction."""

    LONG = "LONG"
    SHORT = "SHORT"
    NO_TRADE = "NO_TRADE"

    @classmethod
    def from_str(cls, val: Any) -> ForexAction:
        """Coerce raw text, LLM outputs, or legacy action labels into ForexAction."""
        if isinstance(val, ForexAction):
            return val
        s = str(val).strip().upper()
        if s in {"BUY", "LONG", "BULL", "BULLISH"}:
            return cls.LONG
        if s in {"SELL", "SHORT", "BEAR", "BEARISH"}:
            return cls.SHORT
        if s in {"HOLD", "NO_TRADE", "NO TRADE", "NO-TRADE", "NEUTRAL", "PASS", "NONE"}:
            return cls.NO_TRADE
        return cls.NO_TRADE


class OrderType(str, Enum):
    """Forex order execution type."""

    MARKET = "MARKET"
    BUY_LIMIT = "BUY_LIMIT"
    SELL_LIMIT = "SELL_LIMIT"
    BUY_STOP = "BUY_STOP"
    SELL_STOP = "SELL_STOP"

    @classmethod
    def from_str(cls, val: Any) -> OrderType:
        """Coerce raw text or LLM outputs into OrderType."""
        if isinstance(val, OrderType):
            return val
        s = str(val).strip().upper().replace(" ", "_")
        for member in cls:
            if member.value == s:
                return member
        return cls.MARKET


class SetupType(str, Enum):
    """Forex market structure / price action setup classification."""

    TREND_CONTINUATION = "TREND_CONTINUATION"
    BREAKOUT = "BREAKOUT"
    REVERSAL = "REVERSAL"
    PULLBACK = "PULLBACK"
    RANGE_BOUND = "RANGE_BOUND"
    NEWS_MOMENTUM = "NEWS_MOMENTUM"
    NEWS_FADE = "NEWS_FADE"

    @classmethod
    def from_str(cls, val: Any) -> SetupType:
        if isinstance(val, SetupType):
            return val
        s = str(val).strip().upper().replace(" ", "_").replace("-", "_")
        for member in cls:
            if member.value == s:
                return member
        return cls.TREND_CONTINUATION


class ForexRiskDecisionAction(str, Enum):
    """Risk management review decision on a ForexTraderProposal."""

    APPROVE = "APPROVE"   # Proposal satisfies all risk rules; trade is cleared for execution
    REJECT = "REJECT"     # Proposal violates core risk rules; trade is cancelled
    MODIFY = "MODIFY"     # Proposal requires adjustment (e.g. reduced lot size, tighter/wider SL, limit order)

    @classmethod
    def from_str(cls, val: Any) -> ForexRiskDecisionAction:
        if isinstance(val, ForexRiskDecisionAction):
            return val
        s = str(val).strip().upper()
        if "APPROV" in s or "ACCEPT" in s or s in {"PASS", "YES", "OK", "CLEAR"}:
            return cls.APPROVE
        if "REJECT" in s or s in {"DECLINE", "DENY", "CANCEL", "NO"}:
            return cls.REJECT
        if "MODIF" in s or s in {"ADJUST", "CHANGE"}:
            return cls.MODIFY
        return cls.REJECT


# ---------------------------------------------------------------------------
# Pip & Risk-Reward Arithmetic Helper
# ---------------------------------------------------------------------------


def compute_pips_and_rr(
    pair: str,
    action: ForexAction,
    entry: float | None,
    stop_loss: float | None,
    take_profit: float | None,
) -> tuple[float | None, float | None, float | None]:
    """Deterministically compute sl_pips, tp_pips, and risk_reward_ratio.

    Returns:
        (sl_pips, tp_pips, risk_reward_ratio)
    """
    if entry is None or stop_loss is None:
        return None, None, None

    pip_size = pip_size_for(pair)
    if pip_size <= 0:
        pip_size = 0.0001

    sl_pips: float | None = None
    tp_pips: float | None = None
    rr: float | None = None

    if action == ForexAction.LONG:
        sl_distance = entry - stop_loss
        if sl_distance > 0:
            sl_pips = round(sl_distance / pip_size, 1)

        if take_profit is not None:
            tp_distance = take_profit - entry
            if tp_distance > 0:
                tp_pips = round(tp_distance / pip_size, 1)
                if sl_distance > 0:
                    rr = round(tp_distance / sl_distance, 2)

    elif action == ForexAction.SHORT:
        sl_distance = stop_loss - entry
        if sl_distance > 0:
            sl_pips = round(sl_distance / pip_size, 1)

        if take_profit is not None:
            tp_distance = entry - take_profit
            if tp_distance > 0:
                tp_pips = round(tp_distance / pip_size, 1)
                if sl_distance > 0:
                    rr = round(tp_distance / sl_distance, 2)

    return sl_pips, tp_pips, rr


# ---------------------------------------------------------------------------
# Forex Trader Proposal Model
# ---------------------------------------------------------------------------


class ForexTraderProposal(BaseModel):
    """Structured transaction proposal produced by the Forex Trader.

    Translates technical analysis, currency macro stance, economic calendar risks,
    and market structure into an institutional trade setup with exact price levels,
    pip calculations, and risk-reward ratios.
    """

    pair: str = Field(
        description="Forex pair symbol, e.g. EURUSD, GBPJPY, AUDUSD"
    )
    action: ForexAction = Field(
        description="Transaction direction. Exactly one of: LONG, SHORT, NO_TRADE."
    )
    order_type: OrderType = Field(
        default=OrderType.MARKET,
        description="Execution order type: MARKET, BUY_LIMIT, SELL_LIMIT, BUY_STOP, SELL_STOP",
    )
    setup_type: SetupType = Field(
        default=SetupType.TREND_CONTINUATION,
        description="Technical setup classification: TREND_CONTINUATION, BREAKOUT, REVERSAL, PULLBACK, RANGE_BOUND, NEWS_MOMENTUM, NEWS_FADE",
    )
    timeframe: str = Field(
        default="H1",
        description="Primary setup execution timeframe (e.g. M15, H1, H4, D1)",
    )
    entry_price: float | None = Field(
        default=None,
        description="Target entry price level in quote currency, or None for NO_TRADE",
    )
    entry_zone_low: float | None = Field(
        default=None,
        description="Lower boundary of entry execution zone in quote currency",
    )
    entry_zone_high: float | None = Field(
        default=None,
        description="Upper boundary of entry execution zone in quote currency",
    )
    stop_loss: float | None = Field(
        default=None,
        description="Invalidation stop-loss price level in quote currency",
    )
    take_profit_1: float | None = Field(
        default=None,
        description="Primary profit target in quote currency (1.5R–2.5R or key structural level)",
    )
    take_profit_2: float | None = Field(
        default=None,
        description="Secondary runner profit target in quote currency",
    )
    risk_reward_ratio: float | None = Field(
        default=None,
        description="Risk-to-reward ratio based on TP1 vs SL (e.g. 2.1 for 1:2.1)",
    )
    sl_pips: float | None = Field(
        default=None,
        description="Stop-loss distance in pips",
    )
    tp_pips: float | None = Field(
        default=None,
        description="Primary take-profit distance in pips",
    )
    suggested_risk_percent: float | None = Field(
        default=1.0,
        ge=0.0,
        le=10.0,
        description="Suggested risk allocation as a percentage of account equity (typically 0.5%–2.0%)",
    )
    suggested_lot_size: float | None = Field(
        default=None,
        ge=0.0,
        description="Suggested volume in standard lots based on account equity and stop distance",
    )
    confluence_factors: list[str] = Field(
        default_factory=list,
        description="Key confluence factors supporting the setup (e.g. 'H4 EMA alignment', 'Bullish FVG retest', 'London session overlap')",
    )
    invalidation_condition: str | None = Field(
        default=None,
        description="Specific structural event or candle close invalidating the trade premise",
    )
    reasoning: str = Field(
        description="Comprehensive trade rationale synthesized from technical, macro, and news analysis",
    )
    trade_rationale_summary: str = Field(
        default="",
        description="Concise 1–2 sentence executive summary of the trade plan",
    )
    valid_until: str | None = Field(
        default=None,
        description="ISO timestamp UTC until which the trade proposal is valid before expiring",
    )

    @field_validator(
        "entry_price",
        "entry_zone_low",
        "entry_zone_high",
        "stop_loss",
        "take_profit_1",
        "take_profit_2",
        "risk_reward_ratio",
        "sl_pips",
        "tp_pips",
        "suggested_risk_percent",
        "suggested_lot_size",
        mode="before",
    )
    @classmethod
    def _coerce_numeric_fields(cls, v: Any) -> Any:
        return _coerce_optional_float(v)

    @field_validator("action", mode="before")
    @classmethod
    def _coerce_action(cls, v: Any) -> ForexAction:
        return ForexAction.from_str(v)

    @field_validator("order_type", mode="before")
    @classmethod
    def _coerce_order_type(cls, v: Any) -> OrderType:
        return OrderType.from_str(v)

    @field_validator("setup_type", mode="before")
    @classmethod
    def _coerce_setup_type(cls, v: Any) -> SetupType:
        return SetupType.from_str(v)

    @model_validator(mode="after")
    def _compute_derived_parameters(self) -> ForexTraderProposal:
        """Automatically derive pip distances and R:R ratio if omitted or zero."""
        if self.action in (ForexAction.LONG, ForexAction.SHORT) and self.entry_price is not None:
            calc_sl_pips, calc_tp_pips, calc_rr = compute_pips_and_rr(
                pair=self.pair,
                action=self.action,
                entry=self.entry_price,
                stop_loss=self.stop_loss,
                take_profit=self.take_profit_1,
            )
            if self.sl_pips is None and calc_sl_pips is not None:
                object.__setattr__(self, "sl_pips", calc_sl_pips)
            if self.tp_pips is None and calc_tp_pips is not None:
                object.__setattr__(self, "tp_pips", calc_tp_pips)
            if self.risk_reward_ratio is None and calc_rr is not None:
                object.__setattr__(self, "risk_reward_ratio", calc_rr)

        return self

    def validate_trade_geometry(self) -> tuple[bool, list[str]]:
        """Validate logical geometry of entry, stop-loss, and take-profit levels.

        Returns:
            (is_valid, list_of_violations_or_warnings)
        """
        violations: list[str] = []

        if self.action == ForexAction.NO_TRADE:
            return True, ["Setup evaluated as NO_TRADE (neutral or high-risk conditions)."]

        if self.entry_price is None:
            violations.append("Entry price is required for directional trades.")
        if self.stop_loss is None:
            violations.append("Stop-loss price is mandatory for risk control.")

        if self.entry_price is not None and self.stop_loss is not None:
            if self.action == ForexAction.LONG:
                if self.stop_loss >= self.entry_price:
                    violations.append(
                        f"LONG stop-loss ({self.stop_loss}) must be strictly below entry price ({self.entry_price})."
                    )
                if self.take_profit_1 is not None and self.take_profit_1 <= self.entry_price:
                    violations.append(
                        f"LONG take-profit ({self.take_profit_1}) must be strictly above entry price ({self.entry_price})."
                    )
            elif self.action == ForexAction.SHORT:
                if self.stop_loss <= self.entry_price:
                    violations.append(
                        f"SHORT stop-loss ({self.stop_loss}) must be strictly above entry price ({self.entry_price})."
                    )
                if self.take_profit_1 is not None and self.take_profit_1 >= self.entry_price:
                    violations.append(
                        f"SHORT take-profit ({self.take_profit_1}) must be strictly below entry price ({self.entry_price})."
                    )

        if self.risk_reward_ratio is not None and self.risk_reward_ratio < 1.0:
            violations.append(
                f"Risk:Reward ratio ({self.risk_reward_ratio}:1) is sub-optimal (minimum standard is 1.0:1, preferred >= 1.5:1)."
            )

        return len(violations) == 0, violations

    def to_trader_proposal(self) -> TraderProposal:
        """Convert to legacy/stock-compatible TraderProposal for cross-engine compatibility."""
        action_map = {
            ForexAction.LONG: TraderAction.BUY,
            ForexAction.SHORT: TraderAction.SELL,
            ForexAction.NO_TRADE: TraderAction.HOLD,
        }
        sizing = (
            f"{self.suggested_risk_percent:.1f}% risk"
            f"{f' ({self.suggested_lot_size:.2f} lots)' if self.suggested_lot_size else ''}"
            if self.suggested_risk_percent is not None
            else None
        )
        return TraderProposal(
            action=action_map.get(self.action, TraderAction.HOLD),
            reasoning=self.reasoning,
            entry_price=self.entry_price,
            stop_loss=self.stop_loss,
            position_sizing=sizing,
        )


# ---------------------------------------------------------------------------
# Forex Risk Decision Model
# ---------------------------------------------------------------------------


class ForexRiskDecision(BaseModel):
    """Structured decision output produced by Risk Management / Portfolio Manager.

    Evaluates the ForexTraderProposal against hard risk rules:
    - Minimum Risk:Reward threshold (e.g. >= 1.5R)
    - Maximum pip stop distance
    - Economic calendar blackout restrictions
    - Account equity risk limits and position sizing
    """

    pair: str = Field(description="Forex pair symbol")
    decision: ForexRiskDecisionAction = Field(
        description="Formal risk decision: APPROVE, REJECT, or MODIFY"
    )
    original_action: ForexAction = Field(
        description="Action proposed by the trader (LONG, SHORT, NO_TRADE)"
    )
    approved_action: ForexAction = Field(
        description="Approved action after risk management evaluation"
    )
    max_risk_percent: float = Field(
        default=1.0,
        ge=0.0,
        le=5.0,
        description="Authorized risk allocation as % of account balance",
    )
    approved_lot_size: float | None = Field(
        default=None,
        ge=0.0,
        description="Authorized trade size in standard lots",
    )
    entry_price: float | None = Field(
        default=None,
        description="Authorized entry price level",
    )
    stop_loss: float | None = Field(
        default=None,
        description="Authorized stop-loss level",
    )
    take_profit: float | None = Field(
        default=None,
        description="Authorized take-profit level",
    )
    risk_reward_ratio: float | None = Field(
        default=None,
        description="Verified risk-reward ratio",
    )
    min_rr_threshold: float = Field(
        default=1.5,
        description="Institutional minimum risk:reward threshold required for approval",
    )
    risk_checks_passed: list[str] = Field(
        default_factory=list,
        description="List of risk controls and verification tests that passed",
    )
    risk_violations: list[str] = Field(
        default_factory=list,
        description="List of risk limits or rule violations encountered",
    )
    modifications_required: list[str] = Field(
        default_factory=list,
        description="Execution adjustments required if decision is MODIFY",
    )
    executive_rationale: str = Field(
        description="Comprehensive narrative synthesizing the risk decision and execution safeguards"
    )

    @field_validator(
        "entry_price",
        "stop_loss",
        "take_profit",
        "risk_reward_ratio",
        "approved_lot_size",
        "max_risk_percent",
        mode="before",
    )
    @classmethod
    def _coerce_numeric_fields(cls, v: Any) -> Any:
        return _coerce_optional_float(v)

    @field_validator("decision", mode="before")
    @classmethod
    def _coerce_decision(cls, v: Any) -> ForexRiskDecisionAction:
        return ForexRiskDecisionAction.from_str(v)

    @field_validator("original_action", "approved_action", mode="before")
    @classmethod
    def _coerce_action(cls, v: Any) -> ForexAction:
        return ForexAction.from_str(v)


# ---------------------------------------------------------------------------
# Bidirectional Adapters & Converters
# ---------------------------------------------------------------------------


def from_trader_proposal(
    proposal: TraderProposal,
    pair: str,
    timeframe: str = "H1",
) -> ForexTraderProposal:
    """Construct a ForexTraderProposal from a generic stock/crypto TraderProposal."""
    action_map = {
        TraderAction.BUY: ForexAction.LONG,
        TraderAction.SELL: ForexAction.SHORT,
        TraderAction.HOLD: ForexAction.NO_TRADE,
    }
    return ForexTraderProposal(
        pair=pair,
        action=action_map.get(proposal.action, ForexAction.NO_TRADE),
        timeframe=timeframe,
        entry_price=proposal.entry_price,
        stop_loss=proposal.stop_loss,
        reasoning=proposal.reasoning,
    )


# ---------------------------------------------------------------------------
# Markdown Rendering Helpers
# ---------------------------------------------------------------------------


def render_forex_trader_proposal(proposal: ForexTraderProposal) -> str:
    """Render a ForexTraderProposal to institutional markdown format.

    Preserves structured headers and includes the machine-parseable
    ``FINAL FOREX PROPOSAL: **LONG/SHORT/NO_TRADE**`` termination tag.
    """
    lines = [
        f"# Forex Trade Proposal: {proposal.pair}",
        "",
        f"- **Action**: **{proposal.action.value}**",
        f"- **Order Type**: {proposal.order_type.value}",
        f"- **Setup Classification**: {proposal.setup_type.value}",
        f"- **Timeframe**: {proposal.timeframe}",
    ]

    if proposal.action != ForexAction.NO_TRADE and proposal.entry_price is not None:
        entry_str = f"{proposal.entry_price:.5f}" if proposal.entry_price else "not provided"
        sl_str = f"{proposal.stop_loss:.5f}" if proposal.stop_loss else "not provided"
        tp1_str = f"{proposal.take_profit_1:.5f}" if proposal.take_profit_1 else "not provided"
        tp2_str = f"{proposal.take_profit_2:.5f}" if proposal.take_profit_2 else "not provided"

        sl_pips_str = f" ({proposal.sl_pips:.1f} pips)" if proposal.sl_pips is not None else ""
        tp_pips_str = f" ({proposal.tp_pips:.1f} pips)" if proposal.tp_pips is not None else ""
        rr_str = f"{proposal.risk_reward_ratio:.2f}:1" if proposal.risk_reward_ratio is not None else "not calculated"

        lines.extend([
            f"- **Entry Price**: {entry_str}",
        ])
        if proposal.entry_zone_low is not None and proposal.entry_zone_high is not None:
            lines.append(f"- **Entry Zone**: [{proposal.entry_zone_low:.5f} – {proposal.entry_zone_high:.5f}]")

        lines.extend([
            f"- **Stop Loss**: {sl_str}{sl_pips_str}",
            f"- **Take Profit 1**: {tp1_str}{tp_pips_str}",
            f"- **Take Profit 2**: {tp2_str}",
            f"- **Risk:Reward Ratio**: {rr_str}",
        ])

    risk_str = f"{proposal.suggested_risk_percent:.1f}%" if proposal.suggested_risk_percent else "not specified"
    lot_str = f"{proposal.suggested_lot_size:.2f} lots" if proposal.suggested_lot_size else "to be sized by risk manager"
    lines.extend([
        f"- **Account Risk Allocation**: {risk_str}",
        f"- **Suggested Position Size**: {lot_str}",
    ])

    if proposal.invalidation_condition:
        lines.append(f"- **Setup Invalidation Condition**: {proposal.invalidation_condition}")

    if proposal.confluence_factors:
        lines.extend([
            "",
            "### Confluence Factors",
        ])
        for c in proposal.confluence_factors:
            lines.append(f"- ✅ {c}")

    lines.extend([
        "",
        "### Trade Rationale",
        proposal.reasoning,
    ])

    lines.extend([
        "",
        f"FINAL FOREX PROPOSAL: **{proposal.action.value}**",
    ])

    return "\n".join(lines)


def render_forex_risk_decision(decision: ForexRiskDecision) -> str:
    """Render a ForexRiskDecision to institutional markdown format."""
    status_icon = {
        ForexRiskDecisionAction.APPROVE: "✅ APPROVED",
        ForexRiskDecisionAction.REJECT: "❌ REJECTED",
        ForexRiskDecisionAction.MODIFY: "⚠️ MODIFIED",
    }.get(decision.decision, str(decision.decision))

    lines = [
        f"# Forex Risk Management Decision: {decision.pair}",
        "",
        f"- **Risk Verdict**: **{status_icon}**",
        f"- **Original Proposed Action**: {decision.original_action.value}",
        f"- **Authorized Execution Action**: **{decision.approved_action.value}**",
        f"- **Authorized Risk Allocation**: {decision.max_risk_percent:.1f}% of equity",
    ]

    if decision.approved_lot_size is not None:
        lines.append(f"- **Authorized Position Size**: {decision.approved_lot_size:.2f} standard lots")

    if decision.entry_price is not None:
        lines.append(f"- **Approved Entry Level**: {decision.entry_price:.5f}")
    if decision.stop_loss is not None:
        lines.append(f"- **Approved Stop-Loss**: {decision.stop_loss:.5f}")
    if decision.take_profit is not None:
        lines.append(f"- **Approved Take-Profit Target**: {decision.take_profit:.5f}")
    if decision.risk_reward_ratio is not None:
        lines.append(
            f"- **Verified Risk:Reward Ratio**: {decision.risk_reward_ratio:.2f}:1 "
            f"(Minimum Required: {decision.min_rr_threshold:.1f}:1)"
        )

    if decision.risk_checks_passed:
        lines.extend([
            "",
            "### Risk Controls Verified",
        ])
        for check in decision.risk_checks_passed:
            lines.append(f"- ✅ {check}")

    if decision.risk_violations:
        lines.extend([
            "",
            "### Risk Rule Violations",
        ])
        for viol in decision.risk_violations:
            lines.append(f"- ⛔ {viol}")

    if decision.modifications_required:
        lines.extend([
            "",
            "### Required Adjustments",
        ])
        for mod in decision.modifications_required:
            lines.append(f"- ⚠️ {mod}")

    lines.extend([
        "",
        "### Executive Risk Synthesis",
        decision.executive_rationale,
        "",
        f"FINAL RISK DECISION: **{decision.decision.value}**",
    ])

    return "\n".join(lines)
