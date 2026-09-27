"""Domain Models for Forex Trade Metrics, MFE/MAE, Outcome Classification & Execution Quality (Phase 16).

Provides strongly-typed models for:
- TradeOutcomeCategory & TradeOutcomeResult: Diagnostic classification of trade efficiency.
- SlippageType & ExecutionQuality: Realized slippage, spread friction, fill delay, and execution quality scoring.
- TradeMfeMae: Maximum Favorable Excursion, Maximum Adverse Excursion, R-multiples, and runup/exit efficiencies.
- MetricsSummary: Aggregated statistical distributions across trades and broker deal executions.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from tradingagents.agents.schemas_forex import ForexAction

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class TradeOutcomeCategory(str, Enum):
    """Diagnostic outcome classification based on excursion efficiency and trade management."""

    PERFECT_EXIT = "PERFECT_EXIT"        # Captured >= 80% of peak MFE, exit near crest
    STANDARD_WIN = "STANDARD_WIN"        # Controlled winning exit capturing solid portion of move
    PREMATURE_EXIT = "PREMATURE_EXIT"    # Closed early; trade ran to large favorable excursion (>=2.0R)
    GREEDY_EXIT = "GREEDY_EXIT"          # Reached >= 1.5R favorable runup, but closed at scratch/loss
    BREAKEVEN = "BREAKEVEN"              # Scratched trade with minor gain/loss within +/- 0.15R
    STANDARD_LOSS = "STANDARD_LOSS"      # Controlled loss strictly contained within intended stop loss
    RUNAWAY_LOSS = "RUNAWAY_LOSS"        # Loss where adverse excursion breached intended stop loss (> 1.05R)
    SCRATCH = "SCRATCH"                  # Immediate flat exit without significant excursion

    @classmethod
    def from_str(cls, val: Any) -> TradeOutcomeCategory:
        if isinstance(val, TradeOutcomeCategory):
            return val
        s = str(val).strip().upper()
        for member in cls:
            if member.value == s or member.name == s:
                return member
        return cls.SCRATCH


class SlippageType(str, Enum):
    """Categorization of order fill slippage relative to requested price."""

    EXACT = "EXACT"                # Exact fill (zero slippage)
    ADVERSE = "ADVERSE"            # Filled at an inferior price (cost to trader)
    IMPROVEMENT = "IMPROVEMENT"    # Filled at a superior price (benefit to trader)


# ---------------------------------------------------------------------------
# MFE / MAE Models
# ---------------------------------------------------------------------------


class TradeMfeMae(BaseModel):
    """Maximum Favorable and Adverse Excursions for an individual trade."""

    trade_id: str
    pair: str
    action: ForexAction
    open_price: float
    close_price: float | None = None
    stop_loss: float
    take_profit: float | None = None
    open_time_utc: str | None = None
    close_time_utc: str | None = None

    # Price extremes reached during the hold period
    mfe_price: float
    mae_price: float

    # Excursion in pips
    mfe_pips: float = Field(default=0.0, ge=0.0)
    mae_pips: float = Field(default=0.0, ge=0.0)

    # Excursion in R-multiples (relative to planned stop loss distance)
    mfe_r: float = Field(default=0.0, ge=0.0)
    mae_r: float = Field(default=0.0, ge=0.0)

    # Timestamps when peak excursions were registered
    mfe_time_utc: str | None = None
    mae_time_utc: str | None = None

    # Realized trade outcomes
    realized_pips: float = 0.0
    realized_r: float = 0.0

    # Planned risk & target distances
    stop_distance_pips: float = Field(default=0.0, ge=0.0)
    target_distance_pips: float | None = None

    # Efficiency metrics (0.0 to 100.0)
    runup_efficiency_pct: float = Field(default=0.0, ge=0.0, le=100.0)
    drawdown_efficiency_pct: float = Field(default=0.0, ge=0.0, le=100.0)
    exit_efficiency_pct: float = Field(default=0.0, ge=0.0, le=100.0)

    candle_count: int = 0


# ---------------------------------------------------------------------------
# Trade Outcome Result Model
# ---------------------------------------------------------------------------


class TradeOutcomeResult(BaseModel):
    """Diagnostic outcome classification and execution feedback for a trade."""

    trade_id: str
    category: TradeOutcomeCategory
    efficiency_score: float = Field(default=0.0, ge=0.0, le=100.0)
    title: str = ""
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)

    mfe_pips: float = 0.0
    mae_pips: float = 0.0
    mfe_r: float = 0.0
    mae_r: float = 0.0
    realized_r: float = 0.0


# ---------------------------------------------------------------------------
# Execution Quality & Slippage Models
# ---------------------------------------------------------------------------


class ExecutionQuality(BaseModel):
    """Detailed slippage, spread, and latency metrics for an individual fill."""

    deal_id: str = Field(default_factory=lambda: f"deal_{uuid.uuid4().hex[:12]}")
    trade_id: str
    proposal_id: str | None = None
    pair: str
    action: ForexAction
    order_type: str = "MARKET"
    volume: float = Field(default=0.1, gt=0.0)
    requested_price: float
    fill_price: float

    # Slippage metrics (signed: positive = adverse, negative = price improvement)
    slippage_pips: float = 0.0
    slippage_type: SlippageType = SlippageType.EXACT
    slippage_cost_usd: float = 0.0

    # Spread metrics
    spread_at_open_pips: float = 0.0
    spread_cost_usd: float = 0.0
    total_execution_friction_usd: float = 0.0

    # Latency & scoring
    execution_delay_ms: float | None = None
    quality_score: float = Field(default=100.0, ge=0.0, le=100.0)

    session: str | None = None
    broker: str = "MetaTrader5"
    timestamp_utc: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


# ---------------------------------------------------------------------------
# Aggregated Metrics Summary Model
# ---------------------------------------------------------------------------


class MetricsSummary(BaseModel):
    """Aggregated portfolio-level MFE/MAE, trade outcomes, and broker execution summary."""

    total_trades_analyzed: int = 0
    total_executions_analyzed: int = 0

    # Excursion averages
    avg_mfe_pips: float = 0.0
    avg_mae_pips: float = 0.0
    avg_mfe_r: float = 0.0
    avg_mae_r: float = 0.0
    avg_realized_r: float = 0.0

    # Efficiency averages
    avg_runup_efficiency_pct: float = 0.0
    avg_drawdown_efficiency_pct: float = 0.0
    avg_exit_efficiency_pct: float = 0.0

    # Execution friction
    avg_slippage_pips: float = 0.0
    max_adverse_slippage_pips: float = 0.0
    best_price_improvement_pips: float = 0.0
    total_slippage_cost_usd: float = 0.0
    total_spread_cost_usd: float = 0.0
    total_execution_friction_usd: float = 0.0
    avg_execution_quality_score: float = 0.0

    # Outcome distribution counts
    outcome_counts: dict[str, int] = Field(default_factory=dict)
    pair_quality_scores: dict[str, float] = Field(default_factory=dict)

    generated_at_utc: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
