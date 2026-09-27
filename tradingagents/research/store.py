"""Research persistence on the journal's existing SQLite connection and lock."""

import hashlib
import json
from contextlib import contextmanager

from tradingagents.research.contracts import (
    RUN_TRANSITIONS,
    AgentReport,
    AnalysisRequest,
    AnalysisRun,
    BrokerEvent,
    MarketSnapshot,
    RunStatus,
    canonical_json,
)


class ResearchStore:
    def __init__(self, journal):
        self.journal = journal

    @contextmanager
    def _connection(self):
        with self.journal._lock:
            conn = self.journal._get_connection()
            try:
                with conn:
                    yield conn
            finally:
                if conn is not self.journal._mem_conn:
                    conn.close()

    def create_run(self, run: AnalysisRun):
        if run.status != RunStatus.QUEUED or run.error is not None:
            raise ValueError("New runs must start queued without an error")
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO analysis_runs VALUES (?, ?, ?, ?, ?, ?, ?)",
                (run.run_id, run.schema_version, run.request.model_dump_json(),
                 run.created_at_utc.isoformat(), run.as_of_utc.isoformat(), run.status.value, run.error),
            )
        return run.run_id

    def get_run(self, run_id):
        with self._connection() as conn:
            row = conn.execute("SELECT run_id, schema_version, request_json, created_at_utc, as_of_utc, status, error FROM analysis_runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            return None
        return AnalysisRun(run_id=row[0], schema_version=row[1], request=AnalysisRequest.model_validate_json(row[2]),
                           created_at_utc=row[3], as_of_utc=row[4], status=row[5], error=row[6])

    def transition_run(self, run_id, status, *, error=None):
        status = RunStatus(status)
        if (status == RunStatus.FAILED) != bool(error):
            raise ValueError("Failed runs require an error; other states cannot have one")
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT status FROM analysis_runs WHERE run_id=?", (run_id,)).fetchone()
            if row is None:
                raise KeyError(run_id)
            if status not in RUN_TRANSITIONS[RunStatus(row[0])]:
                raise ValueError(f"Invalid run transition: {row[0]} -> {status.value}")
            conn.execute("UPDATE analysis_runs SET status=?, error=? WHERE run_id=?", (status.value, error, run_id))

    def link_version(self, run_id, role, version_id):
        with self._connection() as conn:
            conn.execute("INSERT INTO run_versions VALUES (?, ?, ?)", (run_id, role, version_id))

    def _save(self, table, key, record, links):
        raw = canonical_json(record.model_dump(mode="json"))
        digest = hashlib.sha256(raw.encode()).hexdigest()
        identity = getattr(record, key)
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(f"SELECT record_json, content_hash FROM {table} WHERE {key}=?", (identity,)).fetchone()
            if existing is not None:
                if tuple(existing) != (raw, digest):
                    raise ValueError("Immutable record ID conflicts with existing content")
                return identity
            columns = [key, *links, "record_json", "content_hash"]
            conn.execute(f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                         (identity, *links.values(), raw, digest))
        return identity

    def _get(self, table, key, identity, model):
        with self._connection() as conn:
            row = conn.execute(f"SELECT record_json, content_hash FROM {table} WHERE {key}=?", (identity,)).fetchone()
        if row is None:
            return None
        if hashlib.sha256(row[0].encode()).hexdigest() != row[1]:
            raise ValueError("Research record integrity check failed")
        return model.model_validate_json(row[0])

    def save_snapshot(self, snapshot: MarketSnapshot):
        run = self.get_run(snapshot.run_id)
        if run is None or run.request.pair != snapshot.pair or run.as_of_utc != snapshot.as_of_utc:
            raise ValueError("Snapshot must match its run's pair and cutoff")
        return self._save("market_snapshots", "snapshot_id", snapshot, {"run_id": snapshot.run_id})

    def get_snapshot(self, snapshot_id):
        return self._get("market_snapshots", "snapshot_id", snapshot_id, MarketSnapshot)

    def save_report(self, report: AgentReport):
        return self._save("agent_reports", "report_id", report,
                          {"run_id": report.run_id, "snapshot_id": report.snapshot_id, "version_id": report.version_id})

    def get_report(self, report_id):
        return self._get("agent_reports", "report_id", report_id, AgentReport)

    def save_broker_event(self, event: BrokerEvent):
        return self._save("broker_events", "event_id", event,
                          {"broker": event.broker, "account_ref": event.account_ref,
                           "external_id": event.external_id, "event_type": event.event_type})

    def get_broker_event(self, event_id):
        return self._get("broker_events", "event_id", event_id, BrokerEvent)

    def get_initial_proposal_evidence(self, proposal_id):
        with self._connection() as conn:
            row = conn.execute("SELECT payload_json, initial_risk_json, content_hash FROM proposal_evidence WHERE proposal_id=?", (proposal_id,)).fetchone()
        if row is None:
            return None
        raw = canonical_json({"proposal": json.loads(row[0]), "risk": json.loads(row[1]) if row[1] else None})
        if hashlib.sha256(raw.encode()).hexdigest() != row[2]:
            raise ValueError("Proposal evidence integrity check failed")
        return json.loads(raw)
