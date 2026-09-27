"""Domain Models for Forex Trade Journal, Lifecycle & Analytics (Phase 15).

Provides typed representations for:
- EventType & TradeEvent: Chronological audit events for trade timeline.
- LifecycleState: State machine states for proposal-to-trade lifecycle.
- MatchConfidence & MatchResult: Reconciliation matching broker executions with proposals.
- PerformanceReport & breakdown metrics: Institutional analytics across closed trades.
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


class EventType(str, Enum):
    """Types of chronological audit events in the trade lifecycle."""

    PROPOSAL_CREATED = "PROPOSAL_CREATED"
    RISK_EVALUATION = "RISK_EVALUATION"
    PROPOSAL_APPROVED = "PROPOSAL_APPROVED"
    PROPOSAL_REJECTED = "PROPOSAL_REJECTED"
    PROPOSAL_MODIFIED = "PROPOSAL_MODIFIED"
    PROPOSAL_EXPIRED = "PROPOSAL_EXPIRED"
    PROPOSAL_EXECUTED = "PROPOSAL_EXECUTED"
    PROPOSAL_SKIPPED = "PROPOSAL_SKIPPED"
    PROPOSAL_WAITING_USER = "PROPOSAL_WAITING_USER"
    PROPOSAL_INVALIDATED = "PROPOSAL_INVALIDATED"
    PROPOSAL_SUPERSEDED = "PROPOSAL_SUPERSEDED"
    ORDER_SUBMITTED = "ORDER_SUBMITTED"
    ORDER_FILLED = "ORDER_FILLED"
    POSITION_OPENED = "POSITION_OPENED"
    STOP_LOSS_MODIFIED = "STOP_LOSS_MODIFIED"
    TAKE_PROFIT_MODIFIED = "TAKE_PROFIT_MODIFIED"
    BREAKEVEN_APPLIED = "BREAKEVEN_APPLIED"
    PARTIAL_CLOSE = "PARTIAL_CLOSE"
    POSITION_CLOSED = "POSITION_CLOSED"
    NEWS_EVENT_ALERT = "NEWS_EVENT_ALERT"
    NOTE_ADDED = "NOTE_ADDED"
    RECONCILIATION_MATCH = "RECONCILIATION_MATCH"

    @classmethod
    def from_str(cls, val: Any) -> EventType:
        if isinstance(val, EventType):
            return val
        s = str(val).strip().upper()
        for member in cls:
            if member.value == s or member.name == s:
                return member
        return cls.NOTE_ADDED


class LifecycleState(str, Enum):
    """Valid states in the deterministic trade lifecycle state machine."""

    CREATED = "CREATED"
    PROPOSED = "PROPOSED"
    APPROVED = "APPROVED"
    MODIFIED = "MODIFIED"
    REJECTED = "REJECTED"
    RISK_REJECTED = "RISK_REJECTED"
    WAITING_USER = "WAITING_USER"
    EXECUTED = "EXECUTED"
    SKIPPED = "SKIPPED"
    EXPIRED = "EXPIRED"
    INVALIDATED = "INVALIDATED"
    SUPERSEDED = "SUPERSEDED"
    PENDING_FILL = "PENDING_FILL"
    ACTIVE_POSITION = "ACTIVE_POSITION"
    PARTIALLY_CLOSED = "PARTIALLY_CLOSED"
    SETTLED = "SETTLED"
    CANCELLED = "CANCELLED"

    @property
    def is_terminal(self) -> bool:
        return self in (
            LifecycleState.SETTLED,
            LifecycleState.REJECTED,
            LifecycleState.RISK_REJECTED,
            LifecycleState.CANCELLED,
            LifecycleState.EXPIRED,
            LifecycleState.SKIPPED,
            LifecycleState.INVALIDATED,
            LifecycleState.SUPERSEDED,
        )


class MatchConfidence(str, Enum):
    """Confidence tier when reconciling broker executions with proposals."""

    EXACT = "EXACT"        # Explicit proposal_id match or magic number match (score: 1.0)
    HIGH = "HIGH"          # High confidence (score >= 0.85)
    MEDIUM = "MEDIUM"      # Medium confidence (score 0.65 - 0.84)
    LOW = "LOW"            # Low confidence (score 0.40 - 0.64)
    NONE = "NONE"          # No plausible match (score < 0.40)


# ---------------------------------------------------------------------------
# Timeline Models
# ---------------------------------------------------------------------------


class TradeEvent(BaseModel):
    """Immutable chronological audit event in the trade timeline."""

    event_id: str = Field(default_factory=lambda: f"evt_{uuid.uuid4().hex[:12]}")
    trade_id: str | None = None
    proposal_id: str | None = None
    event_type: EventType
    timestamp_utc: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    actor: str = Field(default="System", description="Agent, engine, or system emitting event")
    description: str = Field(default="", description="Human-readable event summary")
    payload: dict[str, Any] = Field(default_factory=dict, description="Structured event metadata")


# ---------------------------------------------------------------------------
# Proposal Matching Models
# ---------------------------------------------------------------------------


class MatchResult(BaseModel):
    """Result of reconciling a broker position/deal with a pending proposal."""

    proposal_id: str | None = None
    broker_ticket: int | str
    symbol: str
    action: ForexAction
    confidence: MatchConfidence = MatchConfidence.NONE
    score: float = Field(default=0.0, ge=0.0, le=1.0)
    reasons: list[str] = Field(default_factory=list)
    matched_at_utc: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    is_matched: bool = False
    discrepancy_pips: float = 0.0


# ---------------------------------------------------------------------------
# Performance Analytics Breakdown Models
# ---------------------------------------------------------------------------


class PairMetrics(BaseModel):
    """Aggregated trading performance for a specific Forex pair."""

    pair: str
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    net_profit: float = 0.0
    profit_factor: float = 0.0
    total_pips: float = 0.0
    avg_pips: float = 0.0
    expectancy_r: float = 0.0


class SetupMetrics(BaseModel):
    """Aggregated trading performance for a specific setup strategy type."""

    setup_type: str
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    net_profit: float = 0.0
    profit_factor: float = 0.0


class SessionMetrics(BaseModel):
    """Aggregated trading performance for a trading session."""

    session: str
    total_trades: int = 0
    winning_trades: int = 0
    win_rate: float = 0.0
    net_profit: float = 0.0


class PerformanceReport(BaseModel):
    """Comprehensive institutional performance analytics across settled trades."""

    total_trades: int = 0
    open_trades: int = 0
    closed_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    breakeven_trades: int = 0
    win_rate: float = 0.0
    loss_rate: float = 0.0
    total_net_profit: float = 0.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    profit_factor: float = 0.0
    expectancy_cash: float = 0.0
    expectancy_r: float = 0.0
    avg_r_multiple: float = 0.0
    max_win_r: float = 0.0
    max_loss_r: float = 0.0
    total_pips_gained: float = 0.0
    avg_pips_per_trade: float = 0.0
    win_avg_pips: float = 0.0
    loss_avg_pips: float = 0.0
    max_drawdown_cash: float = 0.0
    max_drawdown_percent: float = 0.0
    long_win_rate: float = 0.0
    short_win_rate: float = 0.0
    by_pair: dict[str, PairMetrics] = Field(default_factory=dict)
    by_setup: dict[str, SetupMetrics] = Field(default_factory=dict)
    by_session: dict[str, SessionMetrics] = Field(default_factory=dict)
    by_exit_reason: dict[str, int] = Field(default_factory=dict)
    generated_at_utc: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
