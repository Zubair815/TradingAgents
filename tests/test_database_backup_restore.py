"""Safe SQLite journal backup and restore acceptance tests."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

import cli.main as cli_main
from tradingagents.agents.schemas_forex import ForexAction
from tradingagents.database.backup import (
    JournalMaintenanceError,
    create_journal_backup,
    restore_journal_backup,
    validate_journal_database,
)
from tradingagents.database.journal import ForexTradeJournal
from tradingagents.database.migrations import MIGRATIONS


def _journal(path: Path) -> ForexTradeJournal:
    return ForexTradeJournal(db_path=path)


def _add_trade(journal: ForexTradeJournal, trade_id: str, pair: str = "EURUSD") -> None:
    journal.record_trade_open(
        pair=pair,
        action=ForexAction.LONG,
        open_price=1.1,
        stop_loss=1.09,
        take_profit=1.12,
        lots=0.1,
        trade_id=trade_id,
    )


def test_backup_uses_consistent_snapshot_metadata_hash_and_integrity(tmp_path: Path):
    journal = _journal(tmp_path / "active.db")
    _add_trade(journal, "before-backup")

    result = create_journal_backup(journal, tmp_path / "backups")

    assert result.backup_path.is_file()
    assert result.metadata_path.is_file()
    assert result.metadata.schema_version == max(item["version"] for item in MIGRATIONS)
    metadata = json.loads(result.metadata_path.read_text(encoding="utf-8"))
    assert metadata["sha256"] == result.metadata.sha256
    assert "password" not in json.dumps(metadata).lower()
    assert validate_journal_database(result.backup_path) == result.metadata.schema_version
    restored_view = _journal(result.backup_path)
    assert restored_view.get_trade("before-backup") is not None


def test_backup_never_overwrites_an_existing_destination(tmp_path: Path):
    journal = _journal(tmp_path / "active.db")
    destination = tmp_path / "existing.db"
    destination.write_bytes(b"keep me")

    with pytest.raises(JournalMaintenanceError, match="already exists"):
        create_journal_backup(journal, destination)

    assert destination.read_bytes() == b"keep me"


def test_restore_replaces_records_creates_safety_backup_and_remains_writable(tmp_path: Path):
    active = _journal(tmp_path / "active.db")
    _add_trade(active, "wanted")
    backup = create_journal_backup(active, tmp_path / "source.db")
    _add_trade(active, "remove-on-restore", pair="GBPUSD")

    result = restore_journal_backup(
        active,
        backup.backup_path,
        safety_backup_directory=tmp_path / "safety",
    )

    assert result.safety_backup.backup_path.is_file()
    assert result.final_schema_version == max(item["version"] for item in MIGRATIONS)
    assert active.get_trade("wanted") is not None
    assert active.get_trade("remove-on-restore") is None
    _add_trade(active, "after-restore")
    assert active.get_trade("after-restore") is not None

    safety_view = _journal(result.safety_backup.backup_path)
    assert safety_view.get_trade("remove-on-restore") is not None


def test_restore_rejects_corrupt_source_without_changing_active_data(tmp_path: Path):
    active = _journal(tmp_path / "active.db")
    _add_trade(active, "preserved")
    corrupt = tmp_path / "corrupt.db"
    corrupt.write_bytes(b"not sqlite")

    with pytest.raises(JournalMaintenanceError):
        restore_journal_backup(active, corrupt)

    assert active.get_trade("preserved") is not None
    assert not Path(f"{active.db_path}.maintenance.lock").exists()


def test_restore_rejects_future_schema_without_replacing_active(tmp_path: Path):
    active = _journal(tmp_path / "active.db")
    _add_trade(active, "preserved")
    source = create_journal_backup(active, tmp_path / "future.db")
    source.metadata_path.unlink()
    with sqlite3.connect(source.backup_path) as conn:
        conn.execute(
            "INSERT INTO schema_migrations(version, applied_at_utc, description) VALUES (?, ?, ?)",
            (999, "2026-10-01T00:00:00+00:00", "future"),
        )

    with pytest.raises(JournalMaintenanceError, match="newer than supported"):
        restore_journal_backup(active, source.backup_path)

    assert active.get_trade("preserved") is not None


def test_failed_reinitialization_rolls_back_to_safety_backup(tmp_path: Path):
    active = _journal(tmp_path / "active.db")
    _add_trade(active, "old-state")
    source_journal = _journal(tmp_path / "source-active.db")
    _add_trade(source_journal, "new-state")
    source = create_journal_backup(source_journal, tmp_path / "source-backup.db")

    def fail_reinitialize() -> None:
        raise RuntimeError("simulated restart failure")

    with pytest.raises(JournalMaintenanceError, match="restore failed"):
        restore_journal_backup(active, source.backup_path, reinitialize=fail_reinitialize)

    assert active.get_trade("old-state") is not None
    assert active.get_trade("new-state") is None
    assert not Path(f"{active.db_path}.maintenance.lock").exists()


def test_maintenance_marker_blocks_new_journal_connections(tmp_path: Path):
    journal = _journal(tmp_path / "active.db")
    marker = Path(f"{journal.db_path}.maintenance.lock")
    marker.write_text("test", encoding="utf-8")
    try:
        with pytest.raises(RuntimeError, match="maintenance is in progress"):
            journal.list_trades()
    finally:
        marker.unlink()


def test_journal_recovers_provably_abandoned_maintenance_marker(tmp_path, monkeypatch):
    journal = _journal(tmp_path / "active.db")
    marker = Path(f"{journal.db_path}.maintenance.lock")
    marker.write_text("pid=424242\ncreated_at=old\n", encoding="utf-8")
    monkeypatch.setattr("tradingagents.database.backup._process_is_alive", lambda pid: False)
    monkeypatch.setattr("tradingagents.database.backup._STALE_MARKER_GRACE_SECONDS", 0)
    # The public helper receives its default at definition time, so make the
    # marker old enough for the normal five-minute safety window.
    old = marker.stat().st_mtime - 301
    marker.touch()
    os.utime(marker, (old, old))

    assert journal.list_trades() == []
    assert not marker.exists()


def test_cli_backup_and_confirmed_restore(tmp_path: Path):
    database = tmp_path / "active.db"
    journal = _journal(database)
    _add_trade(journal, "original")
    runner = CliRunner()

    backup_result = runner.invoke(
        cli_main.app,
        ["journal", "backup", str(tmp_path / "backups"), "--database", str(database)],
    )
    assert backup_result.exit_code == 0, backup_result.output
    backup_line = next(
        line for line in backup_result.output.splitlines() if line.startswith("Backup: ")
    )
    backup_path = Path(backup_line.removeprefix("Backup: ").strip())
    _add_trade(journal, "later")

    refused = runner.invoke(
        cli_main.app,
        ["journal", "restore", str(backup_path), "--database", str(database)],
    )
    assert refused.exit_code != 0
    assert "--confirm" in refused.output

    restored = runner.invoke(
        cli_main.app,
        [
            "journal", "restore", str(backup_path),
            "--database", str(database),
            "--safety-backup-directory", str(tmp_path / "safety"),
            "--confirm",
        ],
    )
    assert restored.exit_code == 0, restored.output
    assert "Integrity check: ok" in restored.output
    assert journal.get_trade("original") is not None
    assert journal.get_trade("later") is None
