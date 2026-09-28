"""Persistent SQLite Storage for Forex Trading Lessons & Heuristics (Phase 15)."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sqlite3
import threading
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tradingagents.database.journal import ForexTradeJournal
from tradingagents.database.migrations import apply_pragmas, run_migrations
from tradingagents.learning.models import ForexLesson

logger = logging.getLogger(__name__)

_DEFAULT_DB_PATH = os.path.join(
    os.path.expanduser("~"), ".tradingagents", "forex_journal.db"
)


class ForexLessonStore:
    """Thread-safe SQLite repository for saving, listing, querying, merging, and weakening lessons."""

    def __init__(
        self,
        db_path: str | Path | None = None,
        journal: ForexTradeJournal | None = None,
        auto_migrate: bool = True,
    ) -> None:
        self.journal = journal
        if journal is not None:
            self.db_path = journal.db_path
            self._mem_conn = journal._mem_conn
            self._is_memory = journal._is_memory
            self._lock = journal._lock
        else:
            if db_path is None:
                self.db_path = Path(_DEFAULT_DB_PATH)
            elif str(db_path) == ":memory:" or str(db_path).startswith("file::memory:"):
                self.db_path = str(db_path)
            else:
                self.db_path = Path(db_path)

            self._is_memory = str(self.db_path) == ":memory:" or str(self.db_path).startswith("file::memory:")
            self._lock = threading.RLock()

            if not self._is_memory:
                Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

            if self._is_memory:
                self._mem_conn = sqlite3.connect(
                    str(self.db_path), check_same_thread=False, timeout=30.0
                )
                apply_pragmas(self._mem_conn, is_memory=True)
                if auto_migrate:
                    run_migrations(self._mem_conn)
            else:
                self._mem_conn = None
                if auto_migrate:
                    run_migrations(self.db_path)

        with self._lock:
            conn = self._get_connection()
            try:
                self._ensure_schema(conn)
            finally:
                if not self._is_memory and conn is not self._mem_conn:
                    conn.close()

    def _get_connection(self) -> sqlite3.Connection:
        if self._mem_conn is not None:
            return self._mem_conn
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        apply_pragmas(conn, is_memory=False)
        return conn

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        """Ensure all Phase 15 columns exist in trade_lessons table."""
        try:
            cursor = conn.execute("PRAGMA table_info(trade_lessons);")
            existing_cols = {row[1] for row in cursor.fetchall()}
            if not existing_cols:
                return

            needed = [
                ("source_trade_id", "TEXT"),
                ("timeframe", "TEXT"),
                ("setup", "TEXT"),
                ("direction", "TEXT"),
                ("session", "TEXT"),
                ("market_regime", "TEXT"),
                ("lesson_type", "TEXT DEFAULT 'RISK_MANAGEMENT'"),
                ("evidence_count", "INTEGER DEFAULT 1"),
                ("confidence", "REAL DEFAULT 1.0"),
                ("last_validated_at", "TEXT"),
                ("strategy_version", "TEXT DEFAULT '1.0'"),
                ("active", "INTEGER DEFAULT 1"),
            ]
            with conn:
                for col_name, col_def in needed:
                    if col_name not in existing_cols:
                        with contextlib.suppress(sqlite3.OperationalError):
                            conn.execute(
                                f"ALTER TABLE trade_lessons ADD COLUMN {col_name} {col_def};"
                            )
        except sqlite3.OperationalError:
            pass

    def _row_to_lesson(self, row: sqlite3.Row) -> ForexLesson:
        keys = row.keys()
        tags = (
            json.loads(row["tags_json"])
            if ("tags_json" in keys and row["tags_json"])
            else []
        )
        source_trade_id = (
            row["source_trade_id"]
            if ("source_trade_id" in keys and row["source_trade_id"])
            else (row["trade_id"] if "trade_id" in keys else None)
        )
        setup = (
            row["setup"]
            if ("setup" in keys and row["setup"])
            else (row["setup_type"] if "setup_type" in keys else "TREND_CONTINUATION")
        )
        confidence = (
            row["confidence"]
            if ("confidence" in keys and row["confidence"] is not None)
            else (row["confidence_score"] if "confidence_score" in keys else 1.0)
        )
        created_at = (
            row["created_at_utc"]
            if "created_at_utc" in keys
            else (
                row["created_at"]
                if "created_at" in keys
                else datetime.now(timezone.utc).isoformat()
            )
        )

        return ForexLesson(
            lesson_id=row["lesson_id"],
            source_trade_id=source_trade_id,
            proposal_id=row["proposal_id"] if "proposal_id" in keys else None,
            pair=row["pair"],
            timeframe=row["timeframe"] if "timeframe" in keys else None,
            setup=str(setup),
            direction=row["direction"] if "direction" in keys else None,
            session=row["session"] if "session" in keys else None,
            market_regime=row["market_regime"] if "market_regime" in keys else None,
            lesson_type=(
                row["lesson_type"]
                if ("lesson_type" in keys and row["lesson_type"])
                else "RISK_MANAGEMENT"
            ),
            outcome_category=(
                row["outcome_category"] if "outcome_category" in keys else "STANDARD_WIN"
            ),
            rule_violated=row["rule_violated"] if "rule_violated" in keys else None,
            observation=row["observation"] if "observation" in keys else "",
            root_cause=row["root_cause"] if "root_cause" in keys else "",
            actionable_rule=row["actionable_rule"] if "actionable_rule" in keys else "",
            evidence_count=(
                int(row["evidence_count"])
                if ("evidence_count" in keys and row["evidence_count"] is not None)
                else 1
            ),
            confidence=float(confidence),
            tags=tags,
            created_at=str(created_at),
            last_validated_at=(
                row["last_validated_at"] if "last_validated_at" in keys else None
            ),
            strategy_version=(
                row["strategy_version"]
                if ("strategy_version" in keys and row["strategy_version"])
                else "1.0"
            ),
            active=(
                bool(row["active"])
                if ("active" in keys and row["active"] is not None)
                else True
            ),
        )

    def save_lesson(self, lesson: ForexLesson, merge_similar: bool = False) -> str:
        """Persist a single lesson into SQLite, optionally merging with similar active heuristics."""
        if merge_similar:
            merged = self.merge_or_save_lesson(lesson)
            return merged.lesson_id

        tags_json = json.dumps(lesson.tags)
        with self._lock:
            conn = self._get_connection()
            self._ensure_schema(conn)
            try:
                with conn:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO trade_lessons (
                            lesson_id, trade_id, source_trade_id, proposal_id, pair,
                            setup_type, setup, timeframe, direction, session, market_regime,
                            lesson_type, outcome_category, rule_violated, observation,
                            root_cause, actionable_rule, confidence_score, confidence,
                            evidence_count, tags_json, created_at_utc, last_validated_at,
                            strategy_version, active
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                        """,
                        (
                            lesson.lesson_id,
                            lesson.source_trade_id,
                            lesson.source_trade_id,
                            lesson.proposal_id,
                            lesson.pair.strip().upper(),
                            str(lesson.setup),
                            str(lesson.setup),
                            lesson.timeframe,
                            lesson.direction,
                            lesson.session,
                            lesson.market_regime,
                            str(lesson.lesson_type),
                            str(lesson.outcome_category),
                            lesson.rule_violated,
                            lesson.observation,
                            lesson.root_cause,
                            lesson.actionable_rule,
                            lesson.confidence,
                            lesson.confidence,
                            lesson.evidence_count,
                            tags_json,
                            lesson.created_at,
                            lesson.last_validated_at,
                            lesson.strategy_version,
                            1 if lesson.active else 0,
                        ),
                    )
                return lesson.lesson_id
            finally:
                if not self._is_memory and conn is not self._mem_conn:
                    conn.close()

    def save_lessons(
        self, lessons: Sequence[ForexLesson], merge_similar: bool = False
    ) -> list[str]:
        """Atomically persist multiple lessons."""
        ids: list[str] = []
        for lsn in lessons:
            self.save_lesson(lsn, merge_similar=merge_similar)
            ids.append(lsn.lesson_id)
        return ids

    def merge_or_save_lesson(self, lesson: ForexLesson) -> ForexLesson:
        """Merge empirical lesson into an existing similar heuristic or insert if novel."""
        with self._lock:
            conn = self._get_connection()
            self._ensure_schema(conn)
            conn.row_factory = sqlite3.Row
            try:
                cursor = conn.execute(
                    """
                    SELECT * FROM trade_lessons
                    WHERE pair = ?
                      AND (setup = ? OR setup_type = ?)
                      AND active = 1
                      AND (
                          lesson_type = ?
                          OR rule_violated = ?
                          OR outcome_category = ?
                      )
                    ORDER BY evidence_count DESC, created_at_utc DESC
                    LIMIT 1;
                    """,
                    (
                        lesson.pair.strip().upper(),
                        str(lesson.setup),
                        str(lesson.setup),
                        str(lesson.lesson_type),
                        lesson.rule_violated,
                        str(lesson.outcome_category),
                    ),
                )
                row = cursor.fetchone()
                if row:
                    existing = self._row_to_lesson(row)
                    new_count = existing.evidence_count + lesson.evidence_count
                    new_conf = min(1.0, existing.confidence + 0.05)
                    now_str = lesson.created_at or datetime.now(timezone.utc).isoformat()

                    merged_tags = list(set(existing.tags + lesson.tags))
                    merged_obs = existing.observation
                    trade_ref = lesson.source_trade_id or lesson.trade_id
                    if trade_ref and trade_ref not in merged_obs:
                        merged_obs += f" | Re-observed in trade {trade_ref}"

                    with conn:
                        conn.execute(
                            """
                            UPDATE trade_lessons
                            SET evidence_count = ?,
                                confidence = ?,
                                confidence_score = ?,
                                last_validated_at = ?,
                                observation = ?,
                                tags_json = ?
                            WHERE lesson_id = ?;
                            """,
                            (
                                new_count,
                                new_conf,
                                new_conf,
                                now_str,
                                merged_obs,
                                json.dumps(merged_tags),
                                existing.lesson_id,
                            ),
                        )
                    existing.evidence_count = new_count
                    existing.confidence = new_conf
                    existing.last_validated_at = now_str
                    existing.observation = merged_obs
                    existing.tags = merged_tags
                    return existing
                else:
                    self.save_lesson(lesson, merge_similar=False)
                    return lesson
            finally:
                if not self._is_memory and conn is not self._mem_conn:
                    conn.close()

    def weaken_contradicting_lessons(
        self,
        pair: str,
        setup: str,
        penalty: float = 0.2,
    ) -> list[ForexLesson]:
        """Weaken confidence in failure heuristics when a setup demonstrates contradictory success."""
        weakened: list[ForexLesson] = []
        with self._lock:
            conn = self._get_connection()
            self._ensure_schema(conn)
            conn.row_factory = sqlite3.Row
            try:
                cursor = conn.execute(
                    """
                    SELECT * FROM trade_lessons
                    WHERE pair = ?
                      AND (setup = ? OR setup_type = ?)
                      AND active = 1
                      AND (
                          outcome_category IN ('STANDARD_LOSS', 'RUNAWAY_LOSS', 'GREEDY_EXIT')
                          OR lesson_type IN ('STOP_LOSS_VIOLATION', 'SETUP_FAILURE', 'RISK_MANAGEMENT')
                      );
                    """,
                    (pair.strip().upper(), str(setup), str(setup)),
                )
                rows = cursor.fetchall()
                now_str = datetime.now(timezone.utc).isoformat()
                for row in rows:
                    lsn = self._row_to_lesson(row)
                    new_conf = max(0.0, lsn.confidence - penalty)
                    is_active = 1 if new_conf >= 0.3 else 0
                    with conn:
                        conn.execute(
                            """
                            UPDATE trade_lessons
                            SET confidence = ?,
                                confidence_score = ?,
                                last_validated_at = ?,
                                active = ?
                            WHERE lesson_id = ?;
                            """,
                            (new_conf, new_conf, now_str, is_active, lsn.lesson_id),
                        )
                    lsn.confidence = new_conf
                    lsn.last_validated_at = now_str
                    lsn.active = bool(is_active)
                    weakened.append(lsn)
                return weakened
            finally:
                if not self._is_memory and conn is not self._mem_conn:
                    conn.close()

    def record_contradiction(
        self,
        lesson_id: str,
        penalty: float = 0.25,
    ) -> ForexLesson | None:
        """Directly record empirical contradiction against a specific lesson, weakening its confidence."""
        with self._lock:
            conn = self._get_connection()
            self._ensure_schema(conn)
            conn.row_factory = sqlite3.Row
            try:
                cursor = conn.execute(
                    "SELECT * FROM trade_lessons WHERE lesson_id = ?;", (lesson_id,)
                )
                row = cursor.fetchone()
                if not row:
                    return None
                lsn = self._row_to_lesson(row)
                new_conf = max(0.0, lsn.confidence - penalty)
                is_active = 1 if new_conf >= 0.3 else 0
                now_str = datetime.now(timezone.utc).isoformat()
                with conn:
                    conn.execute(
                        """
                        UPDATE trade_lessons
                        SET confidence = ?,
                            confidence_score = ?,
                            last_validated_at = ?,
                            active = ?
                        WHERE lesson_id = ?;
                        """,
                        (new_conf, new_conf, now_str, is_active, lesson_id),
                    )
                lsn.confidence = new_conf
                lsn.last_validated_at = now_str
                lsn.active = bool(is_active)
                return lsn
            finally:
                if not self._is_memory and conn is not self._mem_conn:
                    conn.close()

    def get_lesson(self, lesson_id: str) -> ForexLesson | None:
        """Retrieve a lesson by ID."""
        with self._lock:
            conn = self._get_connection()
            self._ensure_schema(conn)
            conn.row_factory = sqlite3.Row
            try:
                cursor = conn.execute(
                    "SELECT * FROM trade_lessons WHERE lesson_id = ?;", (lesson_id,)
                )
                row = cursor.fetchone()
                if not row:
                    return None
                return self._row_to_lesson(row)
            finally:
                if not self._is_memory and conn is not self._mem_conn:
                    conn.close()

    def list_lessons(
        self,
        pair: str | None = None,
        setup_type: str | None = None,
        setup: str | None = None,
        timeframe: str | None = None,
        direction: str | None = None,
        session: str | None = None,
        market_regime: str | None = None,
        lesson_type: str | None = None,
        outcome_category: str | None = None,
        trade_id: str | None = None,
        active: bool | None = None,
        min_evidence_count: int = 1,
        min_confidence: float = 0.0,
        tag: str | None = None,
        limit: int = 100,
    ) -> list[ForexLesson]:
        """List lessons matching optional search filters."""
        with self._lock:
            conn = self._get_connection()
            self._ensure_schema(conn)
            conn.row_factory = sqlite3.Row
            try:
                query = "SELECT * FROM trade_lessons WHERE 1=1"
                params: list[Any] = []
                if pair:
                    query += " AND pair = ?"
                    params.append(pair.strip().upper())
                target_setup = setup or setup_type
                if target_setup:
                    query += " AND (setup = ? OR setup_type = ?)"
                    params.extend([str(target_setup), str(target_setup)])
                if timeframe:
                    query += " AND timeframe = ?"
                    params.append(str(timeframe))
                if direction:
                    query += " AND direction = ?"
                    params.append(str(direction))
                if session:
                    query += " AND session = ?"
                    params.append(str(session))
                if market_regime:
                    query += " AND market_regime = ?"
                    params.append(str(market_regime))
                if lesson_type:
                    query += " AND lesson_type = ?"
                    params.append(str(lesson_type))
                if outcome_category:
                    query += " AND outcome_category = ?"
                    params.append(str(outcome_category))
                if trade_id:
                    query += " AND (trade_id = ? OR source_trade_id = ?)"
                    params.extend([trade_id, trade_id])
                if active is not None:
                    query += " AND active = ?"
                    params.append(1 if active else 0)
                if min_evidence_count > 1:
                    query += " AND evidence_count >= ?"
                    params.append(min_evidence_count)
                if min_confidence > 0.0:
                    query += " AND (confidence >= ? OR confidence_score >= ?)"
                    params.extend([min_confidence, min_confidence])

                query += " ORDER BY created_at_utc DESC LIMIT ?"
                params.append(limit)

                cursor = conn.execute(query, params)
                rows = cursor.fetchall()
                results: list[ForexLesson] = []
                for row in rows:
                    lsn = self._row_to_lesson(row)
                    if tag and tag.lower() not in [t.lower() for t in lsn.tags]:
                        continue
                    results.append(lsn)
                return results
            finally:
                if not self._is_memory and conn is not self._mem_conn:
                    conn.close()

    def count_lessons(self, active_only: bool = False) -> int:
        """Count total stored lessons."""
        with self._lock:
            conn = self._get_connection()
            self._ensure_schema(conn)
            try:
                if active_only:
                    cursor = conn.execute("SELECT COUNT(*) FROM trade_lessons WHERE active = 1;")
                else:
                    cursor = conn.execute("SELECT COUNT(*) FROM trade_lessons;")
                row = cursor.fetchone()
                return row[0] if row else 0
            finally:
                if not self._is_memory and conn is not self._mem_conn:
                    conn.close()

    def delete_lesson(self, lesson_id: str) -> bool:
        """Delete a lesson by ID."""
        with self._lock:
            conn = self._get_connection()
            try:
                with conn:
                    cursor = conn.execute(
                        "DELETE FROM trade_lessons WHERE lesson_id = ?;", (lesson_id,)
                    )
                return cursor.rowcount > 0
            finally:
                if not self._is_memory and conn is not self._mem_conn:
                    conn.close()
