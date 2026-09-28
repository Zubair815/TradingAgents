"""Domain Models for Forex Learning, Post-Trade Reflection & Lesson Retrieval (Phase 17).

Provides strongly-typed models for:
- ForexLesson: Granular, prescriptive heuristic derived from post-trade analysis.
- ReflectionRating & TradeReflection: Structured evaluation of trade execution discipline.
- RetrievedLesson: Lesson ranked by relevance score for dynamic prompt augmentation.
- ReflectionContext: Comprehensive context bundle supplied to the reflection agent.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator

from tradingagents.agents.schemas_forex import ForexTraderProposal
from tradingagents.database.models import (
    OrderExecutionRecord,
    ProposalRecord,
    TradeJournalRecord,
)
from tradingagents.journal.models import TradeEvent
from tradingagents.metrics.models import (
    ExecutionQuality,
    TradeMfeMae,
    TradeOutcomeResult,
)


class ReflectionRating(str, Enum):
    """Overall execution quality and behavioral grade for a trade."""

    EXCELLENT = "EXCELLENT"            # Exemplary adherence to strategy, optimal exit
    GOOD = "GOOD"                      # Controlled execution with solid risk containment
    NEUTRAL = "NEUTRAL"                # Scratch or routine trade with minimal learning signal
    POOR = "POOR"                      # Execution errors, greedy reversal, or premature exit
    CRITICAL_ERROR = "CRITICAL_ERROR"  # Hard rule breach, stop loss violation, or runaway loss


class EvidenceClass(str, Enum):
    """Categorization of empirical validation strength for a trading heuristic (Phase 15)."""

    ANECDOTAL = "ANECDOTAL"  # Single observation (evidence_count <= 1)
    EARLY = "EARLY"          # 2 observations
    MODERATE = "MODERATE"    # 3 - 5 observations
    STRONG = "STRONG"        # > 5 observations


def classify_evidence(evidence_count: int) -> EvidenceClass:
    """Map raw evidence counts to empirical validation classes."""
    if evidence_count <= 1:
        return EvidenceClass.ANECDOTAL
    elif evidence_count == 2:
        return EvidenceClass.EARLY
    elif 3 <= evidence_count <= 5:
        return EvidenceClass.MODERATE
    else:
        return EvidenceClass.STRONG


class ForexLesson(BaseModel):
    """Institutional heuristic or rule extracted from post-trade reflection (Phase 15)."""

    lesson_id: str = Field(default_factory=lambda: f"lsn_{uuid.uuid4().hex[:12]}")
    source_trade_id: str | None = None
    proposal_id: str | None = None
    pair: str
    timeframe: str | None = None
    setup: str = "TREND_CONTINUATION"
    direction: str | None = None
    session: str | None = None
    market_regime: str | None = None
    lesson_type: str = "RISK_MANAGEMENT"
    observation: str = ""
    root_cause: str = ""
    actionable_rule: str = ""
    evidence_count: int = 1
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    last_validated_at: str | None = None
    strategy_version: str = "1.0"
    active: bool = True

    # Legacy & auxiliary fields preserved for backward compatibility
    outcome_category: str = "STANDARD_WIN"
    rule_violated: str | None = None
    tags: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _remap_legacy_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "trade_id" in data and "source_trade_id" not in data:
                data["source_trade_id"] = data["trade_id"]
            elif "source_trade_id" in data and "trade_id" not in data:
                data["trade_id"] = data["source_trade_id"]

            if "setup_type" in data and "setup" not in data:
                data["setup"] = data["setup_type"]
            elif "setup" in data and "setup_type" not in data:
                data["setup_type"] = data["setup"]

            if "confidence_score" in data and "confidence" not in data:
                data["confidence"] = data["confidence_score"]
            elif "confidence" in data and "confidence_score" not in data:
                data["confidence_score"] = data["confidence"]

            if "created_at_utc" in data and "created_at" not in data:
                data["created_at"] = data["created_at_utc"]
            elif "created_at" in data and "created_at_utc" not in data:
                data["created_at_utc"] = data["created_at"]
        return data

    @model_validator(mode="after")
    def validate_overfitting_ban(self) -> ForexLesson:
        """Prevent anecdotal evidence from establishing categorical trade prohibitions."""
        if self.evidence_count <= 1 or self.evidence_class == EvidenceClass.ANECDOTAL:
            forbidden = [
                "never trade",
                "stop trading",
                "avoid trading forever",
                "permanently avoid",
            ]
            rule_lower = (self.actionable_rule or "").lower()
            if any(term in rule_lower for term in forbidden):
                raise ValueError(
                    f"Categorical ban 'NEVER trade...' is not permitted from a single trade "
                    f"(anecdotal evidence for {self.pair} {self.setup}). "
                    f"Systemic bans require accumulated evidence."
                )
        return self

    @property
    def trade_id(self) -> str | None:
        """Backward-compatible alias for source_trade_id."""
        return self.source_trade_id

    @property
    def setup_type(self) -> str:
        """Backward-compatible alias for setup."""
        return self.setup

    @property
    def confidence_score(self) -> float:
        """Backward-compatible alias for confidence."""
        return self.confidence

    @property
    def created_at_utc(self) -> str:
        """Backward-compatible alias for created_at."""
        return self.created_at

    @property
    def evidence_class(self) -> EvidenceClass:
        """Classify empirical validation strength based on evidence_count."""
        return classify_evidence(self.evidence_count)


class RetrievedLesson(BaseModel):
    """A lesson retrieved from memory with an attached relevance score and diagnostic match reasons."""

    lesson: ForexLesson
    relevance_score: float = Field(default=1.0, ge=0.0, le=1.0)
    match_reasons: list[str] = Field(default_factory=list)


class TradeReflection(BaseModel):
    """Structured post-trade reflection report generated by ForexReflectionAgent (Phase 14)."""

    trade_id: str
    rating: ReflectionRating = ReflectionRating.NEUTRAL

    # Structured evaluation dimensions (Phase 14)
    thesis_quality: str = "GOOD"
    direction_quality: str = "GOOD"
    entry_quality: str = "GOOD"
    stop_quality: str = "GOOD"
    target_quality: str = "GOOD"
    execution_quality: str = "GOOD"
    management_quality: str = "GOOD"
    main_success: str = ""
    main_failure: str = ""

    summary: str = ""
    what_went_well: list[str] = Field(default_factory=list)
    what_went_wrong: list[str] = Field(default_factory=list)
    lessons: list[ForexLesson] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    generated_at_utc: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class ReflectionContext(BaseModel):
    """Full operational context bundle supplied to the reflection engine (Phase 14)."""

    trade: TradeJournalRecord
    proposal: ProposalRecord | ForexTraderProposal | None = None
    mfe_mae: TradeMfeMae | None = None
    outcome: TradeOutcomeResult | None = None
    executions: list[OrderExecutionRecord | ExecutionQuality] = Field(default_factory=list)
    events: list[TradeEvent] = Field(default_factory=list)

    # Detailed operational dimensions (Phase 14)
    risk_decision: dict[str, Any] | None = None
    execution_quality: Any | None = None
    session: str | None = None
    setup: str | None = None
    market_news_context: dict[str, Any] = Field(default_factory=dict)

    @property
    def original_proposal(self) -> ProposalRecord | ForexTraderProposal | None:
        """Alias for proposal record."""
        return self.proposal

    @property
    def actual_fills(self) -> list[OrderExecutionRecord | ExecutionQuality]:
        """Alias for executions."""
        return self.executions

    @property
    def event_timeline(self) -> list[TradeEvent]:
        """Alias for events."""
        return self.events

    @property
    def sl_changes(self) -> list[TradeEvent]:
        """Events recording Stop Loss modifications."""
        target_types = {"SL_CHANGED", "STOP_LOSS_MODIFIED"}
        return [
            e for e in self.events
            if getattr(e.event_type, "value", str(e.event_type)) in target_types
        ]

    @property
    def tp_changes(self) -> list[TradeEvent]:
        """Events recording Take Profit modifications."""
        target_types = {"TP_CHANGED", "TAKE_PROFIT_MODIFIED"}
        return [
            e for e in self.events
            if getattr(e.event_type, "value", str(e.event_type)) in target_types
        ]

    @property
    def partial_closes(self) -> list[TradeEvent]:
        """Events recording partial scale-outs."""
        return [
            e for e in self.events
            if getattr(e.event_type, "value", str(e.event_type)) == "PARTIAL_CLOSE"
        ]

    @property
    def commission(self) -> float:
        return float(getattr(self.trade, "commission", 0.0) or 0.0)

    @property
    def swap(self) -> float:
        return float(getattr(self.trade, "swap", 0.0) or 0.0)

    @property
    def slippage_pips(self) -> float:
        if self.execution_quality is not None and hasattr(self.execution_quality, "slippage_pips"):
            return float(self.execution_quality.slippage_pips)
        if self.executions:
            return float(getattr(self.executions[0], "slippage_pips", 0.0) or 0.0)
        return 0.0

    @property
    def mfe(self) -> TradeMfeMae | None:
        return self.mfe_mae

    @property
    def mae(self) -> float:
        return float(self.mfe_mae.mae_pips if self.mfe_mae else 0.0)

    @property
    def realized_r(self) -> float:
        if self.trade.r_multiple is not None:
            return float(self.trade.r_multiple)
        if self.mfe_mae is not None:
            return float(self.mfe_mae.realized_r)
        return 0.0
