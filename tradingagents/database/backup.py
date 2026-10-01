"""Consistency-safe SQLite backup and guarded restore for the Forex journal."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sqlite3
import tempfile
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from tradingagents.database.migrations import (
    MIGRATIONS,
    get_current_schema_version,
    run_migrations,
)

EXPECTED_TABLES = frozenset(
    {"schema_migrations", "proposals", "trades", "executions", "trade_events"}
)


class JournalMaintenanceError(RuntimeError):
    """A backup or restore could not be completed safely."""


@dataclass(frozen=True)
class BackupMetadata:
    created_at_utc: str
    application_version: str
    schema_version: int
    sha256: str
    source_name: str


@dataclass(frozen=True)
class BackupResult:
    backup_path: Path
    metadata_path: Path
    metadata: BackupMetadata


@dataclass(frozen=True)
class RestoreResult:
    restored_path: Path
    safety_backup: BackupResult
    source_schema_version: int
    final_schema_version: int


def _application_version() -> str:
    try:
        return version("tradingagents")
    except PackageNotFoundError:
        return "development"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _metadata_path(backup_path: Path) -> Path:
    return backup_path.with_suffix(backup_path.suffix + ".metadata.json")


def _integrity_check(conn: sqlite3.Connection) -> None:
    rows = conn.execute("PRAGMA integrity_check;").fetchall()
    messages = [str(row[0]) for row in rows]
    if messages != ["ok"]:
        raise JournalMaintenanceError(
            "SQLite integrity check failed: " + "; ".join(messages[:5])
        )


def validate_journal_database(path: str | Path) -> int:
    """Validate a readable, compatible TradingAgents journal and return its schema."""

    candidate = Path(path).expanduser().resolve()
    if not candidate.is_file():
        raise JournalMaintenanceError(f"Journal database does not exist: {candidate}")
    try:
        conn = sqlite3.connect(f"file:{candidate.as_posix()}?mode=ro", uri=True, timeout=5.0)
    except sqlite3.Error as exc:
        raise JournalMaintenanceError("Source is not a readable SQLite database") from exc
    try:
        _integrity_check(conn)
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table';"
            ).fetchall()
        }
        missing = sorted(EXPECTED_TABLES - tables)
        if missing:
            raise JournalMaintenanceError(
                "Source is not a compatible TradingAgents journal; missing tables: "
                + ", ".join(missing)
            )
        schema_version = get_current_schema_version(conn)
    except sqlite3.Error as exc:
        raise JournalMaintenanceError("Source journal validation failed") from exc
    finally:
        conn.close()

    latest = max(int(item["version"]) for item in MIGRATIONS)
    if schema_version < 1:
        raise JournalMaintenanceError("Source journal has no supported schema version")
    if schema_version > latest:
        raise JournalMaintenanceError(
            f"Source journal schema {schema_version} is newer than supported schema {latest}"
        )

    metadata_path = _metadata_path(candidate)
    if metadata_path.is_file():
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise JournalMaintenanceError("Backup metadata is unreadable") from exc
        expected_hash = metadata.get("sha256")
        if expected_hash and expected_hash != _sha256(candidate):
            raise JournalMaintenanceError("Backup hash does not match its metadata")
    return schema_version


def _sqlite_backup(source_path: Path, destination_path: Path) -> None:
    source = sqlite3.connect(str(source_path), timeout=30.0)
    destination = sqlite3.connect(str(destination_path), timeout=30.0)
    try:
        source.backup(destination)
        destination.commit()
    except sqlite3.Error as exc:
        raise JournalMaintenanceError("SQLite backup operation failed") from exc
    finally:
        destination.close()
        source.close()


def _resolve_backup_path(destination: str | Path, *, prefix: str) -> Path:
    requested = Path(destination).expanduser()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    if requested.exists() and requested.is_dir():
        return (requested / f"{prefix}-{stamp}.db").resolve()
    if not requested.suffix:
        requested.mkdir(parents=True, exist_ok=True)
        return (requested / f"{prefix}-{stamp}.db").resolve()
    requested.parent.mkdir(parents=True, exist_ok=True)
    return requested.resolve()


def _write_metadata(path: Path, metadata: BackupMetadata) -> Path:
    metadata_path = _metadata_path(path)
    handle, temp_name = tempfile.mkstemp(
        prefix=f".{metadata_path.name}.", suffix=".tmp", dir=metadata_path.parent
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(asdict(metadata), stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, metadata_path)
    except Exception:
        with contextlib.suppress(OSError):
            os.close(handle)
        Path(temp_name).unlink(missing_ok=True)
        raise
    return metadata_path


def _backup_unlocked(source_path: Path, destination: str | Path, *, prefix: str) -> BackupResult:
    backup_path = _resolve_backup_path(destination, prefix=prefix)
    if backup_path == source_path.resolve():
        raise JournalMaintenanceError("Backup destination must differ from the active journal")
    if backup_path.exists() or _metadata_path(backup_path).exists():
        raise JournalMaintenanceError(f"Backup destination already exists: {backup_path}")
    handle, temp_name = tempfile.mkstemp(
        prefix=f".{backup_path.name}.", suffix=".tmp", dir=backup_path.parent
    )
    os.close(handle)
    temp_path = Path(temp_name)
    temp_path.unlink(missing_ok=True)
    try:
        _sqlite_backup(source_path, temp_path)
        schema_version = validate_journal_database(temp_path)
        os.replace(temp_path, backup_path)
        metadata = BackupMetadata(
            created_at_utc=datetime.now(timezone.utc).isoformat(),
            application_version=_application_version(),
            schema_version=schema_version,
            sha256=_sha256(backup_path),
            source_name=source_path.name,
        )
        metadata_path = _write_metadata(backup_path, metadata)
        return BackupResult(backup_path, metadata_path, metadata)
    except Exception:
        temp_path.unlink(missing_ok=True)
        backup_path.unlink(missing_ok=True)
        _metadata_path(backup_path).unlink(missing_ok=True)
        raise


def create_journal_backup(journal, destination: str | Path) -> BackupResult:
    """Create and validate a consistent snapshot of a file-backed journal."""

    if getattr(journal, "_is_memory", False):
        raise JournalMaintenanceError("In-memory journals cannot be backed up to a file")
    source_path = Path(journal.db_path).expanduser().resolve()
    with journal._lock:
        return _backup_unlocked(source_path, destination, prefix="forex-journal-backup")


_STALE_MARKER_GRACE_SECONDS = 5 * 60


def _process_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def recover_stale_maintenance_marker(
    active_path: str | Path, *, grace_seconds: float = _STALE_MARKER_GRACE_SECONDS
) -> bool:
    """Remove an abandoned marker only when its recorded owner is provably dead."""
    marker = Path(f"{Path(active_path).expanduser().resolve()}.maintenance.lock")
    try:
        age = max(0.0, time.time() - marker.stat().st_mtime)
        fields = dict(
            line.split("=", 1) for line in marker.read_text(encoding="utf-8").splitlines()
            if "=" in line
        )
        pid = int(fields["pid"])
    except FileNotFoundError:
        return False
    except (OSError, KeyError, ValueError):
        return False
    if age < max(0.0, grace_seconds) or _process_is_alive(pid):
        return False
    try:
        marker.unlink()
    except FileNotFoundError:
        return False
    return True


def _acquire_maintenance_marker(active_path: Path) -> tuple[int, Path]:
    marker = Path(f"{active_path}.maintenance.lock")
    recover_stale_maintenance_marker(active_path)
    try:
        descriptor = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise JournalMaintenanceError("Journal maintenance is already in progress") from exc
    os.write(
        descriptor,
        f"pid={os.getpid()}\ncreated_at={datetime.now(timezone.utc).isoformat()}\n".encode(),
    )
    os.fsync(descriptor)
    return descriptor, marker


def restore_journal_backup(
    journal,
    source: str | Path,
    *,
    safety_backup_directory: str | Path | None = None,
    reinitialize: Callable[[], None] | None = None,
) -> RestoreResult:
    """Validate, safety-backup, atomically replace, migrate, and verify a journal."""

    if getattr(journal, "_is_memory", False):
        raise JournalMaintenanceError("Restore requires a file-backed journal")
    source_path = Path(source).expanduser().resolve()
    source_schema = validate_journal_database(source_path)
    active_path = Path(journal.db_path).expanduser().resolve()
    if source_path == active_path:
        raise JournalMaintenanceError("Restore source must differ from the active journal")
    safety_dir = (
        Path(safety_backup_directory).expanduser().resolve()
        if safety_backup_directory is not None
        else active_path.parent / "backups"
    )

    with journal._lock:
        descriptor, marker = _acquire_maintenance_marker(active_path)
        safety_backup = None
        staged_path = active_path.with_name(f".{active_path.name}.restore-{os.getpid()}.tmp")
        replaced = False
        try:
            active_conn = sqlite3.connect(str(active_path), timeout=5.0)
            try:
                active_conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
                active_conn.execute("BEGIN EXCLUSIVE;")
                active_conn.rollback()
            except sqlite3.Error as exc:
                raise JournalMaintenanceError(
                    "Active journal writers could not be stopped for restore"
                ) from exc
            finally:
                active_conn.close()

            safety_backup = _backup_unlocked(
                active_path, safety_dir, prefix="forex-journal-pre-restore"
            )
            staged_path.unlink(missing_ok=True)
            _sqlite_backup(source_path, staged_path)
            validate_journal_database(staged_path)
            os.replace(staged_path, active_path)
            replaced = True
            for suffix in ("-wal", "-shm"):
                Path(f"{active_path}{suffix}").unlink(missing_ok=True)

            run_migrations(active_path)
            final_schema = validate_journal_database(active_path)
            if reinitialize is not None:
                reinitialize()
            return RestoreResult(
                restored_path=active_path,
                safety_backup=safety_backup,
                source_schema_version=source_schema,
                final_schema_version=final_schema,
            )
        except Exception as exc:
            staged_path.unlink(missing_ok=True)
            if replaced and safety_backup is not None:
                rollback_path = active_path.with_name(
                    f".{active_path.name}.rollback-{os.getpid()}.tmp"
                )
                try:
                    rollback_path.unlink(missing_ok=True)
                    _sqlite_backup(safety_backup.backup_path, rollback_path)
                    validate_journal_database(rollback_path)
                    os.replace(rollback_path, active_path)
                    run_migrations(active_path)
                except Exception as rollback_exc:
                    raise JournalMaintenanceError(
                        "Restore failed and the safety backup could not be reapplied; "
                        f"safety backup: {safety_backup.backup_path}"
                    ) from rollback_exc
                finally:
                    rollback_path.unlink(missing_ok=True)
            if isinstance(exc, JournalMaintenanceError):
                raise
            raise JournalMaintenanceError("Journal restore failed") from exc
        finally:
            os.close(descriptor)
            marker.unlink(missing_ok=True)
