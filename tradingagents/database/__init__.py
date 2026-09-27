"""Database package for trading agents.

Provides SQLite schema migration engine, immutable proposal storage,
and institutional Forex trade journal (Phase 12).
"""

from tradingagents.database.journal import ForexTradeJournal
from tradingagents.database.migrations import (
    apply_pragmas,
    get_current_schema_version,
    run_migrations,
)
from tradingagents.database.models import (
    OrderExecutionRecord,
    ProposalRecord,
    ProposalStatus,
    StrategyVersionRecord,
    TradeExitReason,
    TradeJournalRecord,
    TradeStatus,
)

__all__ = [
    # Journal Manager
    "ForexTradeJournal",
    # Migrations
    "run_migrations",
    "get_current_schema_version",
    "apply_pragmas",
    # Models & Enums
    "ProposalStatus",
    "TradeStatus",
    "TradeExitReason",
    "ProposalRecord",
    "TradeJournalRecord",
    "OrderExecutionRecord",
    "StrategyVersionRecord",
]
