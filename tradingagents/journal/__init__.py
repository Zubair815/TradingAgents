"""Forex Trade Journal Package (Phase 15).

Exports the high-level trade lifecycle, chronological event timeline,
proposal matching engine, and post-trade performance analytics:
- ``ForexJournalManager``: Unified coordinator facade for all journal capabilities.
- ``TradeLifecycleManager``: State machine validation, partial closes, breakeven adjustments.
- ``EventTimeline``: Append-only audit logging and Markdown timeline rendering.
- ``ProposalMatcher``: Automatic reconciliation between MT5 broker executions and agent proposals.
- ``PostTradeAnalytics``: Expectancy, R-multiples, drawdown, and categorical breakdowns.
"""

from tradingagents.journal.analytics import PostTradeAnalytics
from tradingagents.journal.lifecycle import (
    ALLOWED_TRANSITIONS,
    LifecycleError,
    LifecycleTransitionError,
    TradeLifecycleManager,
)
from tradingagents.journal.manager import ForexJournalManager
from tradingagents.journal.matching import ProposalMatcher
from tradingagents.journal.models import (
    EventType,
    LifecycleState,
    MatchConfidence,
    MatchResult,
    PairMetrics,
    PerformanceReport,
    ReconciliationStatus,
    SessionMetrics,
    SetupMetrics,
    TradeEvent,
)
from tradingagents.journal.timeline import EventTimeline

__all__ = [
    # Manager Facade
    "ForexJournalManager",
    # Lifecycle
    "TradeLifecycleManager",
    "LifecycleState",
    "LifecycleError",
    "LifecycleTransitionError",
    "ALLOWED_TRANSITIONS",
    # Timeline
    "EventTimeline",
    "TradeEvent",
    "EventType",
    # Matching
    "ProposalMatcher",
    "MatchConfidence",
    "MatchResult",
    "ReconciliationStatus",
    # Analytics
    "PostTradeAnalytics",
    "PerformanceReport",
    "PairMetrics",
    "SetupMetrics",
    "SessionMetrics",
]
