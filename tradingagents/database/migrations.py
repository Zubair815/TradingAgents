"""Deterministic SQLite migration engine for Forex trading database (Phase 12).

Manages forward-compatible schema evolution, WAL mode activation, foreign key
integrity, busy timeouts, and indexed queries.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Migration Definitions
# ---------------------------------------------------------------------------

MIGRATIONS: list[dict[str, Any]] = [
    {
        "version": 1,
        "description": "Initial schema: proposals, trades, executions, and strategy_versions",
        "sql": """
        -- 1. Proposals table: persistent storage of ForexTraderProposal + ForexRiskDecision
        CREATE TABLE IF NOT EXISTS proposals (
            proposal_id TEXT PRIMARY KEY,
            created_at_utc TEXT NOT NULL,
            pair TEXT NOT NULL,
            action TEXT NOT NULL,
            order_type TEXT NOT NULL,
            setup_type TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            entry_price REAL,
            entry_zone_low REAL,
            entry_zone_high REAL,
            stop_loss REAL,
            take_profit_1 REAL,
            take_profit_2 REAL,
            risk_reward_ratio REAL,
            sl_pips REAL,
            tp_pips REAL,
            suggested_risk_percent REAL,
            suggested_lot_size REAL,
            confluence_factors_json TEXT NOT NULL DEFAULT '[]',
            invalidation_condition TEXT,
            reasoning TEXT NOT NULL DEFAULT '',
            trade_rationale_summary TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL,
            risk_decision_json TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );

        CREATE INDEX IF NOT EXISTS idx_proposals_pair_created
            ON proposals(pair, created_at_utc);
        CREATE INDEX IF NOT EXISTS idx_proposals_status
            ON proposals(status);

        -- 2. Trades table: lifecycle tracking of open, closed, and cancelled positions
        CREATE TABLE IF NOT EXISTS trades (
            trade_id TEXT PRIMARY KEY,
            proposal_id TEXT,
            pair TEXT NOT NULL,
            action TEXT NOT NULL,
            status TEXT NOT NULL,
            open_time_utc TEXT NOT NULL,
            close_time_utc TEXT,
            open_price REAL NOT NULL,
            close_price REAL,
            stop_loss REAL NOT NULL,
            take_profit REAL,
            lots REAL NOT NULL,
            commission REAL NOT NULL DEFAULT 0.0,
            swap REAL NOT NULL DEFAULT 0.0,
            gross_profit REAL,
            net_profit REAL,
            pips_gained REAL,
            r_multiple REAL,
            exit_reason TEXT,
            notes TEXT NOT NULL DEFAULT '',
            reflection TEXT NOT NULL DEFAULT '',
            tags_json TEXT NOT NULL DEFAULT '[]',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            FOREIGN KEY (proposal_id) REFERENCES proposals(proposal_id) ON DELETE SET NULL
        );

        CREATE INDEX IF NOT EXISTS idx_trades_pair_open
            ON trades(pair, open_time_utc);
        CREATE INDEX IF NOT EXISTS idx_trades_status
            ON trades(status);
        CREATE INDEX IF NOT EXISTS idx_trades_proposal
            ON trades(proposal_id);

        -- 3. Executions table: broker fill details, deals, slippage, and spread
        CREATE TABLE IF NOT EXISTS executions (
            deal_id TEXT PRIMARY KEY,
            trade_id TEXT NOT NULL,
            proposal_id TEXT,
            pair TEXT NOT NULL,
            order_type TEXT NOT NULL,
            volume REAL NOT NULL,
            price REAL NOT NULL,
            slippage_pips REAL NOT NULL DEFAULT 0.0,
            spread_at_open_pips REAL NOT NULL DEFAULT 0.0,
            timestamp_utc TEXT NOT NULL,
            FOREIGN KEY (trade_id) REFERENCES trades(trade_id) ON DELETE CASCADE,
            FOREIGN KEY (proposal_id) REFERENCES proposals(proposal_id) ON DELETE SET NULL
        );

        CREATE INDEX IF NOT EXISTS idx_executions_trade
            ON executions(trade_id);

        -- 4. Strategy versions table: tracks prompt hash, model name, and strategy versions
        CREATE TABLE IF NOT EXISTS strategy_versions (
            version_id TEXT PRIMARY KEY,
            strategy_name TEXT NOT NULL,
            prompt_hash TEXT NOT NULL,
            model_name TEXT NOT NULL,
            parameters_json TEXT NOT NULL DEFAULT '{}',
            created_at_utc TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_strategy_name
            ON strategy_versions(strategy_name);

        -- 5. Trade events table: immutable chronological audit timeline (Phase 15)
        CREATE TABLE IF NOT EXISTS trade_events (
            event_id TEXT PRIMARY KEY,
            trade_id TEXT,
            proposal_id TEXT,
            event_type TEXT NOT NULL,
            timestamp_utc TEXT NOT NULL,
            actor TEXT NOT NULL,
            description TEXT NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}'
        );

        CREATE INDEX IF NOT EXISTS idx_trade_events_trade
            ON trade_events(trade_id, timestamp_utc);
        CREATE INDEX IF NOT EXISTS idx_trade_events_proposal
            ON trade_events(proposal_id, timestamp_utc);
        CREATE INDEX IF NOT EXISTS idx_trade_events_type
            ON trade_events(event_type);

        -- 6. Trade lessons table: persistent repository of heuristics, pitfalls, and rules (Phase 17)
        CREATE TABLE IF NOT EXISTS trade_lessons (
            lesson_id TEXT PRIMARY KEY,
            trade_id TEXT,
            proposal_id TEXT,
            pair TEXT NOT NULL,
            setup_type TEXT NOT NULL,
            outcome_category TEXT NOT NULL,
            rule_violated TEXT,
            observation TEXT NOT NULL,
            root_cause TEXT NOT NULL,
            actionable_rule TEXT NOT NULL,
            confidence_score REAL NOT NULL DEFAULT 1.0,
            tags_json TEXT NOT NULL DEFAULT '[]',
            created_at_utc TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_trade_lessons_pair
            ON trade_lessons(pair);
        CREATE INDEX IF NOT EXISTS idx_trade_lessons_setup
            ON trade_lessons(setup_type);
        CREATE INDEX IF NOT EXISTS idx_trade_lessons_outcome
            ON trade_lessons(outcome_category);
        CREATE INDEX IF NOT EXISTS idx_trade_lessons_trade
            ON trade_lessons(trade_id);
        """,
    }
]


# ---------------------------------------------------------------------------
# Migration Engine
# ---------------------------------------------------------------------------


def apply_pragmas(conn: sqlite3.Connection, is_memory: bool = False) -> None:
    """Configure SQLite pragmas for high-throughput concurrency and integrity."""
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA busy_timeout = 30000;")
    if not is_memory:
        try:
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA synchronous = NORMAL;")
        except sqlite3.OperationalError:
            pass


def ensure_migration_table(conn: sqlite3.Connection) -> None:
    """Ensure the schema_migrations tracking table exists."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at_utc TEXT NOT NULL,
            description TEXT NOT NULL
        );
        """
    )


def get_applied_migrations(conn: sqlite3.Connection) -> set[int]:
    """Retrieve set of migration versions already applied."""
    ensure_migration_table(conn)
    cursor = conn.execute("SELECT version FROM schema_migrations;")
    return {row[0] for row in cursor.fetchall()}


def run_migrations(conn_or_path: sqlite3.Connection | str | Path) -> int:
    """Execute all pending schema migrations atomically.

    Returns:
        int: Number of migrations applied during this run.
    """
    should_close = False
    if isinstance(conn_or_path, (str, Path)):
        p_str = str(conn_or_path)
        is_mem = p_str == ":memory:" or p_str.startswith("file::memory:")
        if not is_mem:
            Path(p_str).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(p_str, timeout=30.0)
        should_close = True
    else:
        conn = conn_or_path
        # Check if in-memory
        is_mem = False

    try:
        apply_pragmas(conn, is_memory=is_mem)
        ensure_migration_table(conn)
        applied = get_applied_migrations(conn)

        count = 0
        for m in sorted(MIGRATIONS, key=lambda x: x["version"]):
            ver = m["version"]
            if ver not in applied:
                logger.info("Applying database migration %d: %s", ver, m["description"])
                with conn:
                    conn.executescript(m["sql"])
                    conn.execute(
                        """
                        INSERT INTO schema_migrations (version, applied_at_utc, description)
                        VALUES (?, ?, ?);
                        """,
                        (
                            ver,
                            datetime.now(timezone.utc).isoformat(),
                            m["description"],
                        ),
                    )
                count += 1

        return count
    finally:
        if should_close:
            conn.close()


def get_current_schema_version(conn_or_path: sqlite3.Connection | str | Path) -> int:
    """Return the highest applied migration version (0 if none)."""
    should_close = False
    if isinstance(conn_or_path, (str, Path)):
        conn = sqlite3.connect(str(conn_or_path), timeout=30.0)
        should_close = True
    else:
        conn = conn_or_path

    try:
        ensure_migration_table(conn)
        cursor = conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations;")
        row = cursor.fetchone()
        return row[0] if row else 0
    finally:
        if should_close:
            conn.close()
