"""Database package for trading agents.

Provides SQLite schema migration engine, immutable proposal storage,
and institutional Forex trade journal (Phase 12).
"""

from tradingagents.database.backup import (
    BackupMetadata,
    BackupResult,
    JournalMaintenanceError,
    RestoreResult,
    create_journal_backup,
    recover_stale_maintenance_marker,
    restore_journal_backup,
    validate_journal_database,
)
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
    "BackupMetadata",
    "BackupResult",
    "RestoreResult",
    "JournalMaintenanceError",
    "create_journal_backup",
    "restore_journal_backup",
    "recover_stale_maintenance_marker",
    "validate_journal_database",
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
