"""Dedicated SQLite persistence for observer evidence and alerts."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from tradingagents.pair_observer.models import (
    MarketLevel,
    ObserverAlert,
    ObserverSnapshotEnvelope,
    WeeklyReview,
)
from tradingagents.pair_observer.settings import observer_home

SCHEMA_VERSION = 3


class ObserverRepositoryError(RuntimeError):
    pass


class SQLiteObserverRepository:
    def __init__(self, path: Path | None = None):
        self.path = path or observer_home() / "observer.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _migrate(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS observer_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS observer_snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    setup_id TEXT NOT NULL,
                    alert_id TEXT NOT NULL UNIQUE,
                    as_of_utc TEXT NOT NULL,
                    integrity_sha256 TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS observer_alerts (
                    alert_id TEXT PRIMARY KEY,
                    setup_id TEXT NOT NULL,
                    fingerprint TEXT NOT NULL UNIQUE,
                    created_at_utc TEXT NOT NULL,
                    acknowledged_at_utc TEXT,
                    notification_status TEXT NOT NULL DEFAULT 'PENDING',
                    notification_attempts INTEGER NOT NULL DEFAULT 0,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_observer_alerts_created
                    ON observer_alerts(created_at_utc DESC);
                CREATE TABLE IF NOT EXISTS observer_watermarks (
                    pair TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    last_close_utc TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL,
                    PRIMARY KEY(pair, timeframe)
                );
                CREATE TABLE IF NOT EXISTS observer_structures (
                    pair TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    as_of_utc TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY(pair, timeframe)
                );
                CREATE TABLE IF NOT EXISTS observer_levels (
                    level_id TEXT PRIMARY KEY,
                    pair TEXT NOT NULL,
                    state TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_observer_levels_pair_state
                    ON observer_levels(pair, state);
                CREATE TABLE IF NOT EXISTS observer_setups (
                    setup_id TEXT PRIMARY KEY,
                    pair TEXT NOT NULL,
                    state TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS observer_setup_events (
                    event_id TEXT PRIMARY KEY,
                    setup_id TEXT NOT NULL,
                    from_state TEXT,
                    to_state TEXT NOT NULL,
                    occurred_at_utc TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    FOREIGN KEY(setup_id) REFERENCES observer_setups(setup_id)
                );
                CREATE TABLE IF NOT EXISTS observer_weekly_reviews (
                    review_id TEXT PRIMARY KEY,
                    pair TEXT NOT NULL,
                    week_start_utc TEXT NOT NULL,
                    week_end_utc TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL,
                    integrity_sha256 TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    UNIQUE(pair, week_start_utc)
                );
                CREATE INDEX IF NOT EXISTS idx_observer_weekly_reviews_pair_end
                    ON observer_weekly_reviews(pair, week_end_utc DESC);
                """
            )
            existing = connection.execute(
                "SELECT value FROM observer_meta WHERE key='schema_version'"
            ).fetchone()
            if existing and int(existing["value"]) > SCHEMA_VERSION:
                raise ObserverRepositoryError("observer database schema is newer than this application")
            connection.execute(
                "INSERT OR REPLACE INTO observer_meta(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )

    def save_alert_with_snapshot(self, alert: ObserverAlert, snapshot: ObserverSnapshotEnvelope) -> bool:
        if snapshot.alert_id != alert.alert_id or snapshot.setup_id != alert.setup_id:
            raise ValueError("alert and snapshot identities do not match")
        if not snapshot.verify_integrity():
            raise ValueError("snapshot integrity hash is absent or invalid")
        try:
            with self._connect() as connection:
                previous = connection.execute(
                    "SELECT state FROM observer_setups WHERE setup_id=?", (alert.setup_id,)
                ).fetchone()
                setup_payload = {
                    "setup_id": alert.setup_id,
                    "pair": alert.pair,
                    "direction": alert.direction.value,
                    "setup_type": alert.setup_type.value,
                    "state": alert.state.value,
                    "updated_at_utc": alert.created_at_utc.isoformat(),
                }
                connection.execute(
                    "INSERT INTO observer_setups(setup_id,pair,state,updated_at_utc,payload_json) VALUES(?,?,?,?,?) "
                    "ON CONFLICT(setup_id) DO UPDATE SET state=excluded.state,updated_at_utc=excluded.updated_at_utc,payload_json=excluded.payload_json",
                    (
                        alert.setup_id,
                        alert.pair,
                        alert.state.value,
                        alert.created_at_utc.isoformat(),
                        __import__("json").dumps(setup_payload, sort_keys=True),
                    ),
                )
                from_state = previous["state"] if previous else None
                event_id = f"{alert.setup_id}:{from_state or 'NONE'}:{alert.state.value}"
                connection.execute(
                    "INSERT OR IGNORE INTO observer_setup_events(event_id,setup_id,from_state,to_state,occurred_at_utc,payload_json) VALUES(?,?,?,?,?,?)",
                    (
                        event_id,
                        alert.setup_id,
                        from_state,
                        alert.state.value,
                        alert.created_at_utc.isoformat(),
                        alert.model_dump_json(),
                    ),
                )
                connection.execute(
                    "INSERT INTO observer_alerts(alert_id, setup_id, fingerprint, created_at_utc, payload_json) VALUES(?,?,?,?,?)",
                    (
                        alert.alert_id,
                        alert.setup_id,
                        alert.fingerprint,
                        alert.created_at_utc.isoformat(),
                        alert.model_dump_json(),
                    ),
                )
                connection.execute(
                    "INSERT INTO observer_snapshots(snapshot_id, setup_id, alert_id, as_of_utc, integrity_sha256, payload_json) VALUES(?,?,?,?,?,?)",
                    (
                        snapshot.snapshot_id,
                        snapshot.setup_id,
                        snapshot.alert_id,
                        snapshot.as_of_utc.isoformat(),
                        snapshot.integrity_sha256,
                        snapshot.model_dump_json(),
                    ),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def record_setup_state(self, payload: dict) -> None:
        import json

        setup_id = str(payload["setup_id"])
        state = str(payload["state"])
        occurred = str(payload["updated_at_utc"])
        with self._connect() as connection:
            previous = connection.execute(
                "SELECT state FROM observer_setups WHERE setup_id=?", (setup_id,)
            ).fetchone()
            from_state = previous["state"] if previous else None
            state_rank = {"CANDIDATE": 0, "DEVELOPING": 1, "TECHNICALLY_READY": 2}
            if from_state in state_rank and state in state_rank and state_rank[state] < state_rank[from_state]:
                return
            connection.execute(
                "INSERT INTO observer_setups(setup_id,pair,state,updated_at_utc,payload_json) VALUES(?,?,?,?,?) "
                "ON CONFLICT(setup_id) DO UPDATE SET state=excluded.state,updated_at_utc=excluded.updated_at_utc,payload_json=excluded.payload_json",
                (setup_id, payload["pair"], state, occurred, json.dumps(payload, sort_keys=True)),
            )
            if from_state != state:
                event_id = f"{setup_id}:{from_state or 'NONE'}:{state}"
                connection.execute(
                    "INSERT OR IGNORE INTO observer_setup_events(event_id,setup_id,from_state,to_state,occurred_at_utc,payload_json) VALUES(?,?,?,?,?,?)",
                    (event_id, setup_id, from_state, state, occurred, json.dumps(payload, sort_keys=True)),
                )

    def get_snapshot(self, snapshot_id: str) -> ObserverSnapshotEnvelope:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM observer_snapshots WHERE snapshot_id=?", (snapshot_id,)
            ).fetchone()
        if row is None:
            raise KeyError(snapshot_id)
        snapshot = ObserverSnapshotEnvelope.model_validate_json(row["payload_json"])
        if not snapshot.verify_integrity():
            raise ObserverRepositoryError("stored snapshot integrity check failed")
        return snapshot

    def list_alerts(self, *, limit: int = 50, offset: int = 0) -> list[dict]:
        if not 1 <= limit <= 200 or offset < 0:
            raise ValueError("invalid pagination")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json, acknowledged_at_utc, notification_status FROM observer_alerts ORDER BY created_at_utc DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [
            {
                **ObserverAlert.model_validate_json(row["payload_json"]).model_dump(mode="json"),
                "acknowledged_at_utc": row["acknowledged_at_utc"],
                "notification_status": row["notification_status"],
            }
            for row in rows
        ]

    def get_setup(self, setup_id: str) -> dict:
        with self._connect() as connection:
            setup = connection.execute(
                "SELECT payload_json FROM observer_setups WHERE setup_id=?", (setup_id,)
            ).fetchone()
            rows = connection.execute(
                "SELECT payload_json, acknowledged_at_utc, notification_status FROM observer_alerts WHERE setup_id=? ORDER BY created_at_utc",
                (setup_id,),
            ).fetchall()
            events = connection.execute(
                "SELECT payload_json FROM observer_setup_events WHERE setup_id=? ORDER BY occurred_at_utc",
                (setup_id,),
            ).fetchall()
        if setup is None:
            raise KeyError(setup_id)
        alerts = [ObserverAlert.model_validate_json(row["payload_json"]).model_dump(mode="json") for row in rows]
        import json

        payload = json.loads(setup["payload_json"])
        payload["alerts"] = alerts
        payload["events"] = [json.loads(row["payload_json"]) for row in events]
        return payload

    def list_setups(self, *, limit: int = 50, offset: int = 0) -> list[dict]:
        import json

        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM observer_setups ORDER BY updated_at_utc DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def save_cycle_state(
        self,
        *,
        pair: str,
        as_of_utc: str,
        structures: dict[str, dict],
        levels: tuple[MarketLevel, ...],
        watermarks: dict[str, str],
    ) -> None:
        with self._connect() as connection:
            import json

            cycle_key = f"cycles:{pair}"
            previous_cycles = connection.execute(
                "SELECT value FROM observer_meta WHERE key=?", (cycle_key,)
            ).fetchone()
            cycle_count = int(previous_cycles["value"]) + 1 if previous_cycles else 1
            connection.execute(
                "INSERT OR REPLACE INTO observer_meta(key,value) VALUES(?,?)",
                (cycle_key, str(cycle_count)),
            )
            connection.execute(
                "INSERT OR REPLACE INTO observer_meta(key,value) VALUES(?,?)",
                (f"last_success:{pair}", as_of_utc),
            )

            for timeframe, payload in structures.items():
                connection.execute(
                    "INSERT INTO observer_structures(pair,timeframe,as_of_utc,payload_json) VALUES(?,?,?,?) "
                    "ON CONFLICT(pair,timeframe) DO UPDATE SET as_of_utc=excluded.as_of_utc,payload_json=excluded.payload_json",
                    (pair, timeframe, as_of_utc, json.dumps(payload, sort_keys=True)),
                )
            for level in levels:
                connection.execute(
                    "INSERT INTO observer_levels(level_id,pair,state,updated_at_utc,payload_json) VALUES(?,?,?,?,?) "
                    "ON CONFLICT(level_id) DO UPDATE SET state=excluded.state,updated_at_utc=excluded.updated_at_utc,payload_json=excluded.payload_json",
                    (level.level_id, pair, level.state.value, as_of_utc, level.model_dump_json()),
                )
            for timeframe, last_close in watermarks.items():
                connection.execute(
                    "INSERT INTO observer_watermarks(pair,timeframe,last_close_utc,updated_at_utc) VALUES(?,?,?,?) "
                    "ON CONFLICT(pair,timeframe) DO UPDATE SET last_close_utc=excluded.last_close_utc,updated_at_utc=excluded.updated_at_utc",
                    (pair, timeframe, last_close, as_of_utc),
                )
            connection.execute(
                "DELETE FROM observer_levels WHERE pair=? AND level_id NOT IN "
                "(SELECT level_id FROM observer_levels WHERE pair=? ORDER BY updated_at_utc DESC LIMIT 500)",
                (pair, pair),
            )

    def load_levels(self, pair: str) -> tuple[MarketLevel, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM observer_levels WHERE pair=?", (pair,)
            ).fetchall()
        return tuple(MarketLevel.model_validate_json(row["payload_json"]) for row in rows)

    def load_state(self, pair: str) -> tuple[dict[str, dict], list[dict]]:
        import json

        with self._connect() as connection:
            structures = connection.execute(
                "SELECT timeframe,payload_json FROM observer_structures WHERE pair=?", (pair,)
            ).fetchall()
            levels = connection.execute(
                "SELECT payload_json FROM observer_levels WHERE pair=?", (pair,)
            ).fetchall()
        return (
            {row["timeframe"]: json.loads(row["payload_json"]) for row in structures},
            [json.loads(row["payload_json"]) for row in levels],
        )

    def load_runtime_meta(self, pair: str) -> tuple[int, str | None]:
        with self._connect() as connection:
            cycles = connection.execute(
                "SELECT value FROM observer_meta WHERE key=?", (f"cycles:{pair}",)
            ).fetchone()
            last_success = connection.execute(
                "SELECT value FROM observer_meta WHERE key=?", (f"last_success:{pair}",)
            ).fetchone()
        return (
            int(cycles["value"]) if cycles else 0,
            str(last_success["value"]) if last_success else None,
        )

    def save_weekly_review(self, review: WeeklyReview) -> WeeklyReview:
        if not review.verify_integrity():
            raise ValueError("weekly review integrity hash is absent or invalid")
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO observer_weekly_reviews(review_id,pair,week_start_utc,week_end_utc,created_at_utc,integrity_sha256,payload_json) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(pair,week_start_utc) DO UPDATE SET "
                "review_id=excluded.review_id,week_end_utc=excluded.week_end_utc,created_at_utc=excluded.created_at_utc,"
                "integrity_sha256=excluded.integrity_sha256,payload_json=excluded.payload_json",
                (
                    review.review_id,
                    review.pair,
                    review.week_start_utc.isoformat(),
                    review.week_end_utc.isoformat(),
                    review.created_at_utc.isoformat(),
                    review.integrity_sha256,
                    review.model_dump_json(),
                ),
            )
        return review

    def latest_weekly_review(
        self,
        pair: str,
        *,
        before_utc: datetime | None = None,
    ) -> WeeklyReview | None:
        query = "SELECT payload_json FROM observer_weekly_reviews WHERE pair=?"
        params: list[str] = [pair]
        if before_utc is not None:
            query += " AND week_end_utc<=?"
            params.append(before_utc.isoformat())
        query += " ORDER BY week_end_utc DESC LIMIT 1"
        with self._connect() as connection:
            row = connection.execute(query, params).fetchone()
        if row is None:
            return None
        review = WeeklyReview.model_validate_json(row["payload_json"])
        if not review.verify_integrity():
            raise ObserverRepositoryError("stored weekly review integrity check failed")
        return review

    def acknowledge(self, alert_id: str, at_utc: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE observer_alerts SET acknowledged_at_utc=COALESCE(acknowledged_at_utc, ?) WHERE alert_id=?",
                (at_utc, alert_id),
            )
            return cursor.rowcount == 1

    def mark_notification(self, alert_id: str, status: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE observer_alerts SET notification_status=?, notification_attempts=notification_attempts+1 WHERE alert_id=?",
                (status, alert_id),
            )

    def pending_notifications(self, *, limit: int = 100) -> list[ObserverAlert]:
        """Return committed alerts never attempted, for one startup retry."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM observer_alerts WHERE notification_status='PENDING' AND notification_attempts=0 ORDER BY created_at_utc LIMIT ?",
                (limit,),
            ).fetchall()
        return [ObserverAlert.model_validate_json(row["payload_json"]) for row in rows]

    def integrity_check(self) -> str:
        with self._connect() as connection:
            return str(connection.execute("PRAGMA integrity_check").fetchone()[0])
