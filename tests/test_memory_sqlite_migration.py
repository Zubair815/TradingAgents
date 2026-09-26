"""Tests for TradingMemoryLog SQLite migration."""

import sqlite3

from tradingagents.agents.utils.memory import TradingMemoryLog


def test_migrate_to_sqlite(tmp_path):
    md_file = tmp_path / "trading_memory.md"
    sqlite_file = tmp_path / "trading_memory.sqlite3"

    log = TradingMemoryLog({"memory_log_path": str(md_file)})

    # Store a pending decision
    log.store_decision("AAPL", "2026-01-05", "Rating: Buy\nEnter at $150.")

    # Store and resolve another decision
    log.store_decision("MSFT", "2026-01-05", "Rating: Hold\nWait for catalyst.")
    log.update_with_outcome("MSFT", "2026-01-05", 0.05, 0.02, 5, "Good call.", "2026-01-12")

    # Migrate to SQLite
    migrated_count = log.migrate_to_sqlite(sqlite_file)
    assert migrated_count == 2
    assert sqlite_file.exists()

    # Query SQLite database to verify data integrity
    conn = sqlite3.connect(str(sqlite_file))
    try:
        cur = conn.cursor()
        cur.execute("SELECT ticker, trade_date, rating, status, raw_return, alpha_return, holding_days, resolution_date FROM memory_decisions ORDER BY ticker")
        rows = cur.fetchall()
        assert len(rows) == 2

        # AAPL: pending
        assert rows[0][0] == "AAPL"
        assert rows[0][1] == "2026-01-05"
        assert rows[0][2] == "Buy"
        assert rows[0][3] == "pending"

        # MSFT: resolved
        assert rows[1][0] == "MSFT"
        assert rows[1][1] == "2026-01-05"
        assert rows[1][2] == "Hold"
        assert rows[1][3] == "resolved"
        assert rows[1][4] == "+5.0%"
        assert rows[1][5] == "+2.0%"
        assert rows[1][6] == 5
        assert rows[1][7] == "2026-01-12"
    finally:
        conn.close()
