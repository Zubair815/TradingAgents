"""Persistent SQLite Storage for Forex Trading Lessons & Heuristics (Phase 17)."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from collections.abc import Sequence
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
    """Thread-safe SQLite repository for saving, listing, and querying trading lessons."""

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

    def _get_connection(self) -> sqlite3.Connection:
        if self._mem_conn is not None:
            return self._mem_conn
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        apply_pragmas(conn, is_memory=False)
        return conn

    def save_lesson(self, lesson: ForexLesson) -> str:
        """Persist a single lesson into SQLite."""
        tags_json = json.dumps(lesson.tags)
        with self._lock:
            conn = self._get_connection()
            try:
                with conn:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO trade_lessons (
                            lesson_id, trade_id, proposal_id, pair, setup_type,
                            outcome_category, rule_violated, observation, root_cause,
                            actionable_rule, confidence_score, tags_json, created_at_utc
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                        """,
                        (
                            lesson.lesson_id,
                            lesson.trade_id,
                            lesson.proposal_id,
                            lesson.pair.strip().upper(),
                            str(lesson.setup_type),
                            str(lesson.outcome_category),
                            lesson.rule_violated,
                            lesson.observation,
                            lesson.root_cause,
                            lesson.actionable_rule,
                            lesson.confidence_score,
                            tags_json,
                            lesson.created_at_utc,
                        ),
                    )
                return lesson.lesson_id
            finally:
                if not self._is_memory and conn is not self._mem_conn:
                    conn.close()

    def save_lessons(self, lessons: Sequence[ForexLesson]) -> list[str]:
        """Atomically persist multiple lessons."""
        ids: list[str] = []
        for lsn in lessons:
            self.save_lesson(lsn)
            ids.append(lsn.lesson_id)
        return ids

    def get_lesson(self, lesson_id: str) -> ForexLesson | None:
        """Retrieve a lesson by ID."""
        with self._lock:
            conn = self._get_connection()
            conn.row_factory = sqlite3.Row
            try:
                cursor = conn.execute(
                    "SELECT * FROM trade_lessons WHERE lesson_id = ?;", (lesson_id,)
                )
                row = cursor.fetchone()
                if not row:
                    return None
                return ForexLesson(
                    lesson_id=row["lesson_id"],
                    trade_id=row["trade_id"],
                    proposal_id=row["proposal_id"],
                    pair=row["pair"],
                    setup_type=row["setup_type"],
                    outcome_category=row["outcome_category"],
                    rule_violated=row["rule_violated"],
                    observation=row["observation"],
                    root_cause=row["root_cause"],
                    actionable_rule=row["actionable_rule"],
                    confidence_score=row["confidence_score"],
                    tags=json.loads(row["tags_json"]) if row["tags_json"] else [],
                    created_at_utc=row["created_at_utc"],
                )
            finally:
                if not self._is_memory and conn is not self._mem_conn:
                    conn.close()

    def list_lessons(
        self,
        pair: str | None = None,
        setup_type: str | None = None,
        outcome_category: str | None = None,
        trade_id: str | None = None,
        tag: str | None = None,
        limit: int = 100,
    ) -> list[ForexLesson]:
        """List lessons matching optional search filters."""
        with self._lock:
            conn = self._get_connection()
            conn.row_factory = sqlite3.Row
            try:
                query = "SELECT * FROM trade_lessons WHERE 1=1"
                params: list[Any] = []
                if pair:
                    query += " AND pair = ?"
                    params.append(pair.strip().upper())
                if setup_type:
                    query += " AND setup_type = ?"
                    params.append(str(setup_type))
                if outcome_category:
                    query += " AND outcome_category = ?"
                    params.append(str(outcome_category))
                if trade_id:
                    query += " AND trade_id = ?"
                    params.append(trade_id)
                query += " ORDER BY created_at_utc DESC LIMIT ?"
                params.append(limit)

                cursor = conn.execute(query, params)
                rows = cursor.fetchall()
                results: list[ForexLesson] = []
                for row in rows:
                    tags = json.loads(row["tags_json"]) if row["tags_json"] else []
                    if tag and tag.lower() not in [t.lower() for t in tags]:
                        continue
                    results.append(
                        ForexLesson(
                            lesson_id=row["lesson_id"],
                            trade_id=row["trade_id"],
                            proposal_id=row["proposal_id"],
                            pair=row["pair"],
                            setup_type=row["setup_type"],
                            outcome_category=row["outcome_category"],
                            rule_violated=row["rule_violated"],
                            observation=row["observation"],
                            root_cause=row["root_cause"],
                            actionable_rule=row["actionable_rule"],
                            confidence_score=row["confidence_score"],
                            tags=tags,
                            created_at_utc=row["created_at_utc"],
                        )
                    )
                return results
            finally:
                if not self._is_memory and conn is not self._mem_conn:
                    conn.close()

    def count_lessons(self) -> int:
        """Count total stored lessons."""
        with self._lock:
            conn = self._get_connection()
            try:
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
