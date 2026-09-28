"""High-level institutional SQLite trade journal and proposal storage (Phase 12).

Provides transactional persistence, proposal-to-trade lifecycle linking,
execution fills, strategy versioning, and post-trade performance analytics.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tradingagents.agents.schemas_forex import (
    ForexAction,
    ForexRiskDecision,
    ForexTraderProposal,
    OrderType,
    SetupType,
)
from tradingagents.database.migrations import (
    apply_pragmas,
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
from tradingagents.forex.pips import (
    pip_size_for,
    pip_value_in_account_currency,
)
from tradingagents.research.contracts import canonical_json

logger = logging.getLogger(__name__)

_DEFAULT_DB_PATH = os.path.join(
    os.path.expanduser("~"), ".tradingagents", "forex_journal.db"
)


# ---------------------------------------------------------------------------
# Forex Trade Journal Class
# ---------------------------------------------------------------------------


class ForexTradeJournal:
    """Thread-safe SQLite persistent trade journal and proposal storage."""

    def __init__(
        self,
        db_path: str | Path | None = None,
        auto_migrate: bool = True,
        account_currency: str = "USD",
    ) -> None:
        self.account_currency = account_currency.upper()
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

        # In-memory connections must be kept open; file connections can reconnect
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
        """Return an active database connection with pragmas configured."""
        if self._mem_conn is not None:
            return self._mem_conn
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        apply_pragmas(conn, is_memory=False)
        return conn

    def close(self) -> None:
        """Close connection if in-memory."""
        if self._mem_conn is not None:
            with contextlib.suppress(Exception):
                self._mem_conn.close()
            self._mem_conn = None


    def __enter__(self) -> ForexTradeJournal:
        return self

    @property
    def research(self):
        """Use the same database, connection lifecycle and write lock for research."""
        from tradingagents.research.store import ResearchStore
        return ResearchStore(self)

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    # -----------------------------------------------------------------------
    # Proposal Storage & Lifecycle
    # -----------------------------------------------------------------------

    def save_proposal(
        self,
        proposal: ForexTraderProposal | ProposalRecord,
        risk_decision: ForexRiskDecision | dict[str, Any] | None = None,
        status: ProposalStatus = ProposalStatus.PROPOSED,
        metadata: dict[str, Any] | None = None,
        run_id: str | None = None,
        snapshot_id: str | None = None,
        version_id: str | None = None,
    ) -> str:
        """Persist a Forex trade proposal and optional risk management verdict.

        Returns:
            str: proposal_id
        """
        if isinstance(proposal, ForexTraderProposal):
            rec = ProposalRecord.from_forex_trader_proposal(
                proposal=proposal,
                status=status,
                risk_decision=risk_decision,
                metadata=metadata,
            )
        else:
            rec = proposal

        payload = rec.proposal_payload or rec.to_forex_trader_proposal().model_dump(mode="json")
        evidence = canonical_json({"proposal": payload, "risk": rec.risk_decision})

        with self._lock:
            conn = self._get_connection()
            should_close = conn != self._mem_conn
            try:
                with conn:
                    conn.execute(
                        """
                        INSERT INTO proposals (
                            proposal_id, created_at_utc, pair, action, order_type,
                            setup_type, timeframe, entry_price, entry_zone_low,
                            entry_zone_high, stop_loss, take_profit_1, take_profit_2,
                            risk_reward_ratio, sl_pips, tp_pips, confidence, suggested_risk_percent,
                            suggested_lot_size, confluence_factors_json,
                            invalidation_condition, reasoning, trade_rationale_summary,
                            status, risk_decision_json, metadata_json
                        ) VALUES (
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                        );
                        """,
                        (
                            rec.proposal_id,
                            rec.created_at_utc,
                            rec.pair,
                            rec.action.value if hasattr(rec.action, "value") else str(rec.action),
                            rec.order_type.value if hasattr(rec.order_type, "value") else str(rec.order_type),
                            rec.setup_type.value if hasattr(rec.setup_type, "value") else str(rec.setup_type),
                            rec.timeframe,
                            rec.entry_price,
                            rec.entry_zone_low,
                            rec.entry_zone_high,
                            rec.stop_loss,
                            rec.take_profit_1,
                            rec.take_profit_2,
                            rec.risk_reward_ratio,
                            rec.sl_pips,
                            rec.tp_pips,
                            rec.confidence,
                            rec.suggested_risk_percent,
                            rec.suggested_lot_size,
                            json.dumps(rec.confluence_factors),
                            rec.invalidation_condition,
                            rec.reasoning,
                            rec.trade_rationale_summary,
                            rec.status.value if hasattr(rec.status, "value") else str(rec.status),
                            json.dumps(rec.risk_decision) if rec.risk_decision else None,
                            json.dumps(rec.metadata),
                        ),
                    )
                    conn.execute(
                        "INSERT INTO proposal_evidence VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (rec.proposal_id, rec.schema_version, run_id or rec.run_id,
                         snapshot_id or rec.snapshot_id, version_id or rec.version_id,
                         canonical_json(payload), canonical_json(rec.risk_decision) if rec.risk_decision is not None else None,
                         hashlib.sha256(evidence.encode()).hexdigest()),
                    )
                return rec.proposal_id
            finally:
                if should_close:
                    conn.close()

    def get_proposal(self, proposal_id: str) -> ProposalRecord | None:
        """Retrieve a proposal by its unique ID."""
        with self._lock:
            conn = self._get_connection()
            should_close = conn != self._mem_conn
            try:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    "SELECT proposals.*, proposal_evidence.payload_json, proposal_evidence.run_id, "
                    "proposal_evidence.snapshot_id, proposal_evidence.version_id "
                    "FROM proposals LEFT JOIN proposal_evidence USING(proposal_id) WHERE proposal_id = ?;", (proposal_id,)
                )
                row = cursor.fetchone()
                if not row:
                    return None
                return self._row_to_proposal_record(row)
            finally:
                if should_close:
                    conn.close()

    def update_proposal_status(
        self,
        proposal_id: str,
        status: ProposalStatus,
        risk_decision: ForexRiskDecision | dict[str, Any] | None = None,
    ) -> bool:
        """Update the lifecycle status and risk verdict of a proposal."""
        status_val = status.value if hasattr(status, "value") else str(status)
        rd_json: str | None = None
        if isinstance(risk_decision, ForexRiskDecision):
            rd_json = risk_decision.model_dump_json()
        elif isinstance(risk_decision, dict):
            rd_json = json.dumps(risk_decision)

        with self._lock:
            conn = self._get_connection()
            should_close = conn != self._mem_conn
            try:
                with conn:
                    if rd_json:
                        cursor = conn.execute(
                            """
                            UPDATE proposals
                            SET status = ?, risk_decision_json = ?
                            WHERE proposal_id = ?;
                            """,
                            (status_val, rd_json, proposal_id),
                        )
                    else:
                        cursor = conn.execute(
                            "UPDATE proposals SET status = ? WHERE proposal_id = ?;",
                            (status_val, proposal_id),
                        )
                return cursor.rowcount > 0
            finally:
                if should_close:
                    conn.close()

    def supersede_proposals(
        self,
        pair: str,
        exclude_proposal_id: str | None = None,
    ) -> list[str]:
        """Mark unfulfilled active proposals for a pair as SUPERSEDED.

        Original proposal evidence and records remain strictly immutable.
        """
        norm_pair = pair.replace("/", "").strip().upper()
        active_statuses = (
            ProposalStatus.PROPOSED.value,
            ProposalStatus.APPROVED.value,
            ProposalStatus.MODIFIED.value,
            ProposalStatus.WAITING_USER.value,
        )
        with self._lock:
            conn = self._get_connection()
            should_close = conn != self._mem_conn
            try:
                with conn:
                    query = f"""
                        SELECT proposal_id FROM proposals
                        WHERE pair = ? AND status IN ({','.join('?' for _ in active_statuses)})
                    """
                    params: list[Any] = [norm_pair, *active_statuses]
                    if exclude_proposal_id:
                        query += " AND proposal_id != ?"
                        params.append(exclude_proposal_id)

                    cursor = conn.execute(query, tuple(params))
                    rows = cursor.fetchall()
                    superseded_ids = [
                        r[0] if isinstance(r, (tuple, list)) else r["proposal_id"]
                        for r in rows
                    ]
                    if superseded_ids:
                        update_query = f"""
                            UPDATE proposals SET status = ?
                            WHERE proposal_id IN ({','.join('?' for _ in superseded_ids)})
                        """
                        conn.execute(
                            update_query,
                            (ProposalStatus.SUPERSEDED.value, *superseded_ids),
                        )
                return superseded_ids
            finally:
                if should_close:
                    conn.close()

    def list_proposals(
        self,
        pair: str | None = None,
        status: ProposalStatus | str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 100,
    ) -> list[ProposalRecord]:
        """Query proposals with optional filtering."""
        query = ("SELECT proposals.*, proposal_evidence.payload_json, proposal_evidence.run_id, "
                 "proposal_evidence.snapshot_id, proposal_evidence.version_id "
                 "FROM proposals LEFT JOIN proposal_evidence USING(proposal_id) WHERE 1=1")
        params: list[Any] = []

        if pair:
            query += " AND pair = ?"
            params.append(pair.strip().upper())
        if status:
            s_val = status.value if hasattr(status, "value") else str(status)
            query += " AND status = ?"
            params.append(s_val)
        if start_date:
            query += " AND created_at_utc >= ?"
            params.append(start_date)
        if end_date:
            query += " AND created_at_utc <= ?"
            params.append(end_date)

        query += " ORDER BY created_at_utc DESC LIMIT ?;"
        params.append(limit)

        with self._lock:
            conn = self._get_connection()
            should_close = conn != self._mem_conn
            try:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(query, params)
                return [self._row_to_proposal_record(row) for row in cursor.fetchall()]
            finally:
                if should_close:
                    conn.close()

    # -----------------------------------------------------------------------
    # Trade Journal Lifecycle
    # -----------------------------------------------------------------------

    def record_trade_open(
        self,
        pair: str,
        action: ForexAction,
        open_price: float,
        stop_loss: float,
        lots: float,
        trade_id: str | None = None,
        proposal_id: str | None = None,
        take_profit: float | None = None,
        open_time_utc: str | None = None,
        commission: float = 0.0,
        tags: list[str] | None = None,
        notes: str = "",
        metadata: dict[str, Any] | None = None,
        confidence: float | None = None,
    ) -> TradeJournalRecord:
        """Record the execution and opening of a new Forex trade."""
        tid = trade_id or f"trd_{uuid.uuid4().hex[:12]}"
        now_str = open_time_utc or datetime.now(timezone.utc).isoformat()

        eff_confidence = confidence
        if eff_confidence is None and proposal_id:
            try:
                prop = self.get_proposal(proposal_id)
                if prop and getattr(prop, "confidence", None) is not None:
                    eff_confidence = prop.confidence
            except Exception:
                pass

        rec = TradeJournalRecord(
            trade_id=tid,
            proposal_id=proposal_id,
            pair=pair.strip().upper(),
            action=action,
            status=TradeStatus.OPEN,
            open_time_utc=now_str,
            open_price=open_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            lots=lots,
            commission=commission,
            confidence=eff_confidence,
            tags=tags or [],
            notes=notes,
            metadata=metadata or {},
        )

        with self._lock:
            conn = self._get_connection()
            should_close = conn != self._mem_conn
            try:
                with conn:
                    conn.execute(
                        """
                        INSERT INTO trades (
                            trade_id, proposal_id, pair, action, status,
                            open_time_utc, open_price, stop_loss, take_profit,
                            lots, commission, swap, confidence, tags_json, notes, metadata_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                        """,
                        (
                            rec.trade_id,
                            rec.proposal_id,
                            rec.pair,
                            rec.action.value,
                            rec.status.value,
                            rec.open_time_utc,
                            rec.open_price,
                            rec.stop_loss,
                            rec.take_profit,
                            rec.lots,
                            rec.commission,
                            rec.swap,
                            rec.confidence,
                            json.dumps(rec.tags),
                            rec.notes,
                            json.dumps(rec.metadata),
                        ),
                    )
                    # Automatically mark proposal as EXECUTED if linked
                    if proposal_id:
                        conn.execute(
                            "UPDATE proposals SET status = 'EXECUTED' WHERE proposal_id = ?;",
                            (proposal_id,),
                        )
                return rec
            finally:
                if should_close:
                    conn.close()

    def record_trade_close(
        self,
        trade_id: str,
        close_price: float,
        close_time_utc: str | None = None,
        exit_reason: TradeExitReason | str | None = None,
        swap: float = 0.0,
        notes: str = "",
        reflection: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> TradeJournalRecord:
        """Close an active position, calculating pips, R-multiple, and PnL."""
        trade = self.get_trade(trade_id)
        if not trade:
            raise ValueError(f"Trade ID {trade_id!r} not found in journal.")

        now_str = close_time_utc or datetime.now(timezone.utc).isoformat()
        exit_r = (
            exit_reason
            if isinstance(exit_reason, TradeExitReason)
            else TradeExitReason.from_str(exit_reason)
        )

        # Pip calculations
        ps = pip_size_for(trade.pair)
        if trade.action == ForexAction.LONG:
            pips = round((close_price - trade.open_price) / ps, 1)
            risk_pips = abs(trade.open_price - trade.stop_loss) / ps
        else:
            pips = round((trade.open_price - close_price) / ps, 1)
            risk_pips = abs(trade.stop_loss - trade.open_price) / ps

        # Realized R-Multiple
        r_multiple = round(pips / risk_pips, 2) if risk_pips > 0 else 0.0

        # Profit arithmetic in account currency
        pip_val_per_lot = pip_value_in_account_currency(
            pair=trade.pair,
            lot_size=1.0,
            account_currency=self.account_currency,
        )
        gross_profit = round(pips * pip_val_per_lot * trade.lots, 2)
        net_profit = round(gross_profit - trade.commission + swap, 2)

        merged_notes = f"{trade.notes}\n{notes}".strip() if notes else trade.notes
        merged_meta = {**trade.metadata, **(metadata or {})}

        with self._lock:
            conn = self._get_connection()
            should_close = conn != self._mem_conn
            try:
                with conn:
                    conn.execute(
                        """
                        UPDATE trades
                        SET status = 'CLOSED', close_time_utc = ?, close_price = ?,
                            swap = ?, gross_profit = ?, net_profit = ?,
                            pips_gained = ?, r_multiple = ?, exit_reason = ?,
                            notes = ?, reflection = ?, metadata_json = ?
                        WHERE trade_id = ?;
                        """,
                        (
                            now_str,
                            close_price,
                            swap,
                            gross_profit,
                            net_profit,
                            pips,
                            r_multiple,
                            exit_r.value if exit_r else None,
                            merged_notes,
                            reflection,
                            json.dumps(merged_meta),
                            trade_id,
                        ),
                    )
                return self.get_trade(trade_id)  # type: ignore[return-value]
            finally:
                if should_close:
                    conn.close()

    def get_trade(self, trade_id: str) -> TradeJournalRecord | None:
        """Retrieve a trade record by trade_id."""
        with self._lock:
            conn = self._get_connection()
            should_close = conn != self._mem_conn
            try:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    "SELECT * FROM trades WHERE trade_id = ?;", (trade_id,)
                )
                row = cursor.fetchone()
                if not row:
                    return None
                return self._row_to_trade_record(row)
            finally:
                if should_close:
                    conn.close()

    def list_trades(
        self,
        pair: str | None = None,
        status: TradeStatus | str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 100,
    ) -> list[TradeJournalRecord]:
        """List trades matching filters."""
        query = "SELECT * FROM trades WHERE 1=1"
        params: list[Any] = []

        if pair:
            query += " AND pair = ?"
            params.append(pair.strip().upper())
        if status:
            s_val = status.value if hasattr(status, "value") else str(status)
            query += " AND status = ?"
            params.append(s_val)
        if start_date:
            query += " AND open_time_utc >= ?"
            params.append(start_date)
        if end_date:
            query += " AND open_time_utc <= ?"
            params.append(end_date)

        query += " ORDER BY open_time_utc DESC LIMIT ?;"
        params.append(limit)

        with self._lock:
            conn = self._get_connection()
            should_close = conn != self._mem_conn
            try:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(query, params)
                return [self._row_to_trade_record(row) for row in cursor.fetchall()]
            finally:
                if should_close:
                    conn.close()

    def update_trade_reflection(
        self,
        trade_id: str,
        reflection: str,
        tags: list[str] | None = None,
        notes: str | None = None,
    ) -> bool:
        """Attach post-trade learning, reflection, and categorization tags."""
        with self._lock:
            conn = self._get_connection()
            should_close = conn != self._mem_conn
            try:
                with conn:
                    if tags is not None and notes is not None:
                        cursor = conn.execute(
                            """
                            UPDATE trades
                            SET reflection = ?, tags_json = ?, notes = ?
                            WHERE trade_id = ?;
                            """,
                            (reflection, json.dumps(tags), notes, trade_id),
                        )
                    elif tags is not None:
                        cursor = conn.execute(
                            "UPDATE trades SET reflection = ?, tags_json = ? WHERE trade_id = ?;",
                            (reflection, json.dumps(tags), trade_id),
                        )
                    else:
                        cursor = conn.execute(
                            "UPDATE trades SET reflection = ? WHERE trade_id = ?;",
                            (reflection, trade_id),
                        )
                return cursor.rowcount > 0
            finally:
                if should_close:
                    conn.close()

    def update_trade_metadata(
        self,
        trade_id: str,
        metadata: dict[str, Any],
    ) -> bool:
        """Update or merge metadata JSON on an existing trade record."""
        with self._lock:
            conn = self._get_connection()
            should_close = conn != self._mem_conn
            try:
                with conn:
                    cursor = conn.execute(
                        "SELECT metadata_json FROM trades WHERE trade_id = ?;",
                        (trade_id,),
                    )
                    row = cursor.fetchone()
                    if not row:
                        return False
                    existing = (
                        json.loads(row[0])
                        if (row[0] and isinstance(row[0], str))
                        else {}
                    )
                    existing.update(metadata)
                    conn.execute(
                        "UPDATE trades SET metadata_json = ? WHERE trade_id = ?;",
                        (json.dumps(existing), trade_id),
                    )
                return True
            finally:
                if should_close:
                    conn.close()

    # -----------------------------------------------------------------------
    # Order Execution Fills
    # -----------------------------------------------------------------------

    def record_execution(
        self,
        deal_id: str,
        trade_id: str,
        pair: str,
        order_type: str,
        volume: float,
        price: float,
        proposal_id: str | None = None,
        slippage_pips: float = 0.0,
        spread_at_open_pips: float = 0.0,
        timestamp_utc: str | None = None,
    ) -> OrderExecutionRecord:
        """Record an individual execution fill event."""
        now_str = timestamp_utc or datetime.now(timezone.utc).isoformat()
        rec = OrderExecutionRecord(
            deal_id=deal_id,
            trade_id=trade_id,
            proposal_id=proposal_id,
            pair=pair.strip().upper(),
            order_type=order_type,
            volume=volume,
            price=price,
            slippage_pips=slippage_pips,
            spread_at_open_pips=spread_at_open_pips,
            timestamp_utc=now_str,
        )
        with self._lock:
            conn = self._get_connection()
            should_close = conn != self._mem_conn
            try:
                with conn:
                    conn.execute(
                        """
                        INSERT INTO executions (
                            deal_id, trade_id, proposal_id, pair, order_type,
                            volume, price, slippage_pips, spread_at_open_pips, timestamp_utc
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                        """,
                        (
                            rec.deal_id,
                            rec.trade_id,
                            rec.proposal_id,
                            rec.pair,
                            rec.order_type,
                            rec.volume,
                            rec.price,
                            rec.slippage_pips,
                            rec.spread_at_open_pips,
                            rec.timestamp_utc,
                        ),
                    )
                return rec
            finally:
                if should_close:
                    conn.close()

    def list_executions_for_trade(self, trade_id: str) -> list[OrderExecutionRecord]:
        """List execution fills for a specific trade."""
        with self._lock:
            conn = self._get_connection()
            should_close = conn != self._mem_conn
            try:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    "SELECT * FROM executions WHERE trade_id = ? ORDER BY timestamp_utc ASC;",
                    (trade_id,),
                )
                return [
                    OrderExecutionRecord(
                        deal_id=row["deal_id"],
                        trade_id=row["trade_id"],
                        proposal_id=row["proposal_id"],
                        pair=row["pair"],
                        order_type=row["order_type"],
                        volume=row["volume"],
                        price=row["price"],
                        slippage_pips=row["slippage_pips"],
                        spread_at_open_pips=row["spread_at_open_pips"],
                        timestamp_utc=row["timestamp_utc"],
                    )
                    for row in cursor.fetchall()
                ]
            finally:
                if should_close:
                    conn.close()

    def list_executions(self, limit: int = 1000) -> list[OrderExecutionRecord]:
        """List recent execution fills across all trades."""
        with self._lock:
            conn = self._get_connection()
            should_close = conn != self._mem_conn
            try:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    "SELECT * FROM executions ORDER BY timestamp_utc DESC LIMIT ?;",
                    (limit,),
                )
                return [
                    OrderExecutionRecord(
                        deal_id=row["deal_id"],
                        trade_id=row["trade_id"],
                        proposal_id=row["proposal_id"],
                        pair=row["pair"],
                        order_type=row["order_type"],
                        volume=row["volume"],
                        price=row["price"],
                        slippage_pips=row["slippage_pips"],
                        spread_at_open_pips=row["spread_at_open_pips"],
                        timestamp_utc=row["timestamp_utc"],
                    )
                    for row in cursor.fetchall()
                ]
            finally:
                if should_close:
                    conn.close()

    # -----------------------------------------------------------------------
    # Strategy Versioning
    # -----------------------------------------------------------------------

    def record_strategy_version(
        self,
        strategy_name: str,
        prompt_hash: str,
        model_name: str,
        parameters: dict[str, Any] | None = None,
        version_id: str | None = None,
    ) -> StrategyVersionRecord:
        """Store strategy versioning metadata."""
        vid = version_id or f"v_{uuid.uuid4().hex[:8]}"
        now_str = datetime.now(timezone.utc).isoformat()
        rec = StrategyVersionRecord(
            version_id=vid,
            strategy_name=strategy_name,
            prompt_hash=prompt_hash,
            model_name=model_name,
            parameters=parameters or {},
            created_at_utc=now_str,
        )
        with self._lock:
            conn = self._get_connection()
            should_close = conn != self._mem_conn
            try:
                with conn:
                    conn.execute(
                        """
                        INSERT INTO strategy_versions (
                            version_id, strategy_name, prompt_hash,
                            model_name, parameters_json, created_at_utc
                        ) VALUES (?, ?, ?, ?, ?, ?);
                        """,
                        (
                            rec.version_id,
                            rec.strategy_name,
                            rec.prompt_hash,
                            rec.model_name,
                            json.dumps(rec.parameters),
                            rec.created_at_utc,
                        ),
                    )
                return rec
            finally:
                if should_close:
                    conn.close()

    def get_strategy_version(self, version_id: str) -> StrategyVersionRecord | None:
        """Lookup strategy version by ID."""
        with self._lock:
            conn = self._get_connection()
            should_close = conn != self._mem_conn
            try:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    "SELECT * FROM strategy_versions WHERE version_id = ?;", (version_id,)
                )
                row = cursor.fetchone()
                if not row:
                    return None
                return StrategyVersionRecord(
                    version_id=row["version_id"],
                    strategy_name=row["strategy_name"],
                    prompt_hash=row["prompt_hash"],
                    model_name=row["model_name"],
                    parameters=json.loads(row["parameters_json"]),
                    created_at_utc=row["created_at_utc"],
                )
            finally:
                if should_close:
                    conn.close()

    # -----------------------------------------------------------------------
    # Performance Analytics & Journal Summary
    # -----------------------------------------------------------------------

    def get_journal_summary(self) -> dict[str, Any]:
        """Aggregate statistical performance metrics across the entire journal."""
        closed_trades = self.list_trades(status=TradeStatus.CLOSED, limit=10000)
        open_trades = self.list_trades(status=TradeStatus.OPEN, limit=1000)

        total_closed = len(closed_trades)
        if total_closed == 0:
            return {
                "total_trades": 0,
                "open_trades": len(open_trades),
                "winning_trades": 0,
                "losing_trades": 0,
                "win_rate": 0.0,
                "total_net_profit": 0.0,
                "profit_factor": 0.0,
                "average_r_multiple": 0.0,
                "total_pips": 0.0,
                "max_win_r": 0.0,
                "max_loss_r": 0.0,
            }

        winners = [t for t in closed_trades if (t.net_profit or 0.0) > 0]
        losers = [t for t in closed_trades if (t.net_profit or 0.0) <= 0]

        win_rate = round((len(winners) / total_closed) * 100.0, 1)
        net_pnl = round(sum(t.net_profit or 0.0 for t in closed_trades), 2)
        total_pips = round(sum(t.pips_gained or 0.0 for t in closed_trades), 1)

        gross_wins = sum(t.gross_profit or 0.0 for t in winners)
        gross_losses = abs(sum(t.gross_profit or 0.0 for t in losers))
        profit_factor = (
            round(gross_wins / gross_losses, 2)
            if gross_losses > 0
            else (999.0 if gross_wins > 0 else 0.0)
        )

        r_multiples = [t.r_multiple for t in closed_trades if t.r_multiple is not None]
        avg_r = round(sum(r_multiples) / len(r_multiples), 2) if r_multiples else 0.0
        max_win_r = max(r_multiples) if r_multiples else 0.0
        max_loss_r = min(r_multiples) if r_multiples else 0.0

        return {
            "total_trades": total_closed,
            "open_trades": len(open_trades),
            "winning_trades": len(winners),
            "losing_trades": len(losers),
            "win_rate": win_rate,
            "total_net_profit": net_pnl,
            "profit_factor": profit_factor,
            "average_r_multiple": avg_r,
            "total_pips": total_pips,
            "max_win_r": max_win_r,
            "max_loss_r": max_loss_r,
        }

    def get_pair_performance(self, pair: str) -> dict[str, Any]:
        """Aggregate performance metrics for a specific currency pair."""
        closed = self.list_trades(pair=pair, status=TradeStatus.CLOSED, limit=10000)
        if not closed:
            return {"pair": pair.upper(), "trades": 0, "net_profit": 0.0, "win_rate": 0.0}

        wins = [t for t in closed if (t.net_profit or 0.0) > 0]
        net_pnl = round(sum(t.net_profit or 0.0 for t in closed), 2)
        pips = round(sum(t.pips_gained or 0.0 for t in closed), 1)

        return {
            "pair": pair.upper(),
            "trades": len(closed),
            "winning_trades": len(wins),
            "win_rate": round((len(wins) / len(closed)) * 100.0, 1),
            "net_profit": net_pnl,
            "total_pips": pips,
        }

    # -----------------------------------------------------------------------
    # Row Parsing Helpers
    # -----------------------------------------------------------------------

    @staticmethod
    def _row_to_proposal_record(row: sqlite3.Row) -> ProposalRecord:
        return ProposalRecord(
            proposal_payload=json.loads(row["payload_json"]) if row["payload_json"] else None,
            run_id=row["run_id"], snapshot_id=row["snapshot_id"], version_id=row["version_id"],
            proposal_id=row["proposal_id"],
            created_at_utc=row["created_at_utc"],
            pair=row["pair"],
            action=ForexAction.from_str(row["action"]),
            order_type=OrderType.from_str(row["order_type"]),
            setup_type=SetupType.from_str(row["setup_type"]),
            timeframe=row["timeframe"],
            entry_price=row["entry_price"],
            entry_zone_low=row["entry_zone_low"],
            entry_zone_high=row["entry_zone_high"],
            stop_loss=row["stop_loss"],
            take_profit_1=row["take_profit_1"],
            take_profit_2=row["take_profit_2"],
            risk_reward_ratio=row["risk_reward_ratio"],
            sl_pips=row["sl_pips"],
            tp_pips=row["tp_pips"],
            confidence=dict(row).get("confidence"),
            suggested_risk_percent=row["suggested_risk_percent"],
            suggested_lot_size=row["suggested_lot_size"],
            confluence_factors=json.loads(row["confluence_factors_json"]),
            invalidation_condition=row["invalidation_condition"],
            reasoning=row["reasoning"],
            trade_rationale_summary=row["trade_rationale_summary"],
            status=ProposalStatus.from_str(row["status"]),
            risk_decision=json.loads(row["risk_decision_json"]) if row["risk_decision_json"] else None,
            metadata=json.loads(row["metadata_json"]),
        )

    @staticmethod
    def _row_to_trade_record(row: sqlite3.Row) -> TradeJournalRecord:
        return TradeJournalRecord(
            trade_id=row["trade_id"],
            proposal_id=row["proposal_id"],
            pair=row["pair"],
            action=ForexAction.from_str(row["action"]),
            status=TradeStatus.from_str(row["status"]),
            open_time_utc=row["open_time_utc"],
            close_time_utc=row["close_time_utc"],
            open_price=row["open_price"],
            close_price=row["close_price"],
            stop_loss=row["stop_loss"],
            take_profit=row["take_profit"],
            lots=row["lots"],
            commission=row["commission"],
            swap=row["swap"],
            gross_profit=row["gross_profit"],
            net_profit=row["net_profit"],
            pips_gained=row["pips_gained"],
            r_multiple=row["r_multiple"],
            confidence=dict(row).get("confidence"),
            exit_reason=TradeExitReason.from_str(row["exit_reason"]) if row["exit_reason"] else None,
            notes=row["notes"],
            reflection=row["reflection"],
            tags=json.loads(row["tags_json"]),
            metadata=json.loads(row["metadata_json"]),
        )

    # -----------------------------------------------------------------------
    # Trade Events Timeline CRUD (Phase 15)
    # -----------------------------------------------------------------------

    def record_event(
        self,
        event_type: Any,
        trade_id: str | None = None,
        proposal_id: str | None = None,
        broker_position_id: str | None = None,
        broker_order_id: str | None = None,
        broker_deal_id: str | None = None,
        old_value: Any = None,
        new_value: Any = None,
        price: float | None = None,
        volume: float | None = None,
        source: str | None = None,
        actor: str = "System",
        description: str = "",
        metadata: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        event_id: str | None = None,
        timestamp_utc: str | None = None,
    ) -> str:
        """Record an immutable chronological event in the trade timeline."""
        eid = event_id or f"evt_{uuid.uuid4().hex[:12]}"
        ts = timestamp_utc or datetime.now(timezone.utc).isoformat()
        eff_meta = metadata if metadata is not None else (payload or {})
        eff_source = source or actor
        payload_json = json.dumps(eff_meta)
        evt_type_str = str(event_type.value if hasattr(event_type, "value") else event_type)
        old_val_str = (
            json.dumps(old_value)
            if isinstance(old_value, (dict, list))
            else (str(old_value) if old_value is not None else None)
        )
        new_val_str = (
            json.dumps(new_value)
            if isinstance(new_value, (dict, list))
            else (str(new_value) if new_value is not None else None)
        )

        with self._lock:
            conn = self._get_connection()
            try:
                with conn:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO trade_events (
                            event_id, trade_id, proposal_id, broker_position_id,
                            broker_order_id, broker_deal_id, event_type, timestamp_utc,
                            old_value, new_value, price, volume, source, actor,
                            description, payload_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                        """,
                        (
                            eid,
                            trade_id,
                            proposal_id,
                            str(broker_position_id) if broker_position_id is not None else None,
                            str(broker_order_id) if broker_order_id is not None else None,
                            str(broker_deal_id) if broker_deal_id is not None else None,
                            evt_type_str,
                            ts,
                            old_val_str,
                            new_val_str,
                            float(price) if price is not None else None,
                            float(volume) if volume is not None else None,
                            eff_source,
                            actor,
                            description,
                            payload_json,
                        ),
                    )
                return eid
            finally:
                if not self._is_memory and conn is not self._mem_conn:
                    conn.close()

    def get_events(
        self,
        trade_id: str | None = None,
        proposal_id: str | None = None,
        event_type: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Query chronological events from trade audit timeline."""
        with self._lock:
            conn = self._get_connection()
            conn.row_factory = sqlite3.Row
            try:
                query = "SELECT * FROM trade_events WHERE 1=1"
                params: list[Any] = []
                if trade_id:
                    query += " AND trade_id = ?"
                    params.append(trade_id)
                if proposal_id:
                    query += " AND proposal_id = ?"
                    params.append(proposal_id)
                if event_type:
                    query += " AND event_type = ?"
                    params.append(str(event_type))
                query += " ORDER BY timestamp_utc ASC LIMIT ?"
                params.append(limit)

                cursor = conn.execute(query, params)
                rows = cursor.fetchall()
                col_names = set(rows[0].keys()) if rows else set()
                result: list[dict[str, Any]] = []
                for r in rows:
                    payload_data = json.loads(r["payload_json"]) if r["payload_json"] else {}
                    result.append({
                        "event_id": r["event_id"],
                        "trade_id": r["trade_id"],
                        "proposal_id": r["proposal_id"],
                        "broker_position_id": r["broker_position_id"] if "broker_position_id" in col_names else None,
                        "broker_order_id": r["broker_order_id"] if "broker_order_id" in col_names else None,
                        "broker_deal_id": r["broker_deal_id"] if "broker_deal_id" in col_names else None,
                        "event_type": r["event_type"],
                        "timestamp_utc": r["timestamp_utc"],
                        "old_value": r["old_value"] if "old_value" in col_names else None,
                        "new_value": r["new_value"] if "new_value" in col_names else None,
                        "price": r["price"] if "price" in col_names else None,
                        "volume": r["volume"] if "volume" in col_names else None,
                        "source": (r["source"] if "source" in col_names and r["source"] else r["actor"]),
                        "actor": r["actor"],
                        "description": r["description"],
                        "payload": payload_data,
                        "metadata": payload_data,
                    })
                return result
            finally:
                if not self._is_memory and conn is not self._mem_conn:
                    conn.close()

    # -----------------------------------------------------------------------
    # Trade Lessons Repository (Phase 17)
    # -----------------------------------------------------------------------

    def record_lesson(
        self,
        pair: str,
        setup_type: str,
        outcome_category: str,
        observation: str,
        root_cause: str,
        actionable_rule: str,
        lesson_id: str | None = None,
        trade_id: str | None = None,
        proposal_id: str | None = None,
        rule_violated: str | None = None,
        confidence_score: float = 1.0,
        tags: list[str] | None = None,
        created_at_utc: str | None = None,
    ) -> str:
        """Store a structured heuristic or pitfall lesson."""
        lid = lesson_id or f"lsn_{uuid.uuid4().hex[:12]}"
        now_str = created_at_utc or datetime.now(timezone.utc).isoformat()
        tags_json = json.dumps(tags or [])

        with self._lock:
            conn = self._get_connection()
            try:
                with conn:
                    conn.execute(
                        """
                        INSERT INTO trade_lessons (
                            lesson_id, trade_id, proposal_id, pair, setup_type,
                            outcome_category, rule_violated, observation, root_cause,
                            actionable_rule, confidence_score, tags_json, created_at_utc
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                        """,
                        (
                            lid,
                            trade_id,
                            proposal_id,
                            pair.strip().upper(),
                            str(setup_type),
                            str(outcome_category),
                            rule_violated,
                            observation,
                            root_cause,
                            actionable_rule,
                            confidence_score,
                            tags_json,
                            now_str,
                        ),
                    )
                return lid
            finally:
                if not self._is_memory and conn is not self._mem_conn:
                    conn.close()

    def get_lesson(self, lesson_id: str) -> dict[str, Any] | None:
        """Retrieve a specific lesson by ID."""
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
                return {
                    "lesson_id": row["lesson_id"],
                    "trade_id": row["trade_id"],
                    "proposal_id": row["proposal_id"],
                    "pair": row["pair"],
                    "setup_type": row["setup_type"],
                    "outcome_category": row["outcome_category"],
                    "rule_violated": row["rule_violated"],
                    "observation": row["observation"],
                    "root_cause": row["root_cause"],
                    "actionable_rule": row["actionable_rule"],
                    "confidence_score": row["confidence_score"],
                    "tags": json.loads(row["tags_json"]) if row["tags_json"] else [],
                    "created_at_utc": row["created_at_utc"],
                }
            finally:
                if not self._is_memory and conn is not self._mem_conn:
                    conn.close()

    def list_lessons(
        self,
        pair: str | None = None,
        setup_type: str | None = None,
        outcome_category: str | None = None,
        trade_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List lessons matching optional filters."""
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
                result: list[dict[str, Any]] = []
                for r in rows:
                    result.append({
                        "lesson_id": r["lesson_id"],
                        "trade_id": r["trade_id"],
                        "proposal_id": r["proposal_id"],
                        "pair": r["pair"],
                        "setup_type": r["setup_type"],
                        "outcome_category": r["outcome_category"],
                        "rule_violated": r["rule_violated"],
                        "observation": r["observation"],
                        "root_cause": r["root_cause"],
                        "actionable_rule": r["actionable_rule"],
                        "confidence_score": r["confidence_score"],
                        "tags": json.loads(r["tags_json"]) if r["tags_json"] else [],
                        "created_at_utc": r["created_at_utc"],
                    })
                return result
            finally:
                if not self._is_memory and conn is not self._mem_conn:
                    conn.close()

    # -----------------------------------------------------------------------
    # Confidence Calibration (Phase 18)
    # -----------------------------------------------------------------------

    def get_confidence_calibration(
        self,
        pair: str | None = None,
        min_samples: int = 10,
    ) -> Any:
        """Aggregate trade outcomes by confidence bucket and compute empirical calibration metrics (Phase 18)."""
        from tradingagents.metrics.confidence_calibration import ConfidenceCalibrationEngine

        trades = self.list_trades(pair=pair, limit=10000)
        engine = ConfidenceCalibrationEngine(min_samples=min_samples)
        return engine.compute_calibration(trades=trades, min_samples=min_samples)

    # Aliases
    record_trade = record_trade_open

