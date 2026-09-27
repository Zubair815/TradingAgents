"""Additive schema for shared research records; legacy trading rows stay intact."""

SCHEMA_V2 = """
CREATE TABLE analysis_runs (
    run_id TEXT PRIMARY KEY,
    schema_version INTEGER NOT NULL CHECK(schema_version = 1),
    request_json TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    as_of_utc TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('queued','running','completed','failed','cancelled')),
    error TEXT
);
CREATE INDEX idx_analysis_runs_status ON analysis_runs(status, created_at_utc);
CREATE TABLE market_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES analysis_runs(run_id),
    record_json TEXT NOT NULL,
    content_hash TEXT NOT NULL
);
CREATE INDEX idx_snapshots_run ON market_snapshots(run_id);
CREATE TABLE run_versions (
    run_id TEXT NOT NULL REFERENCES analysis_runs(run_id),
    role TEXT NOT NULL,
    version_id TEXT NOT NULL REFERENCES strategy_versions(version_id),
    PRIMARY KEY(run_id, role)
);
CREATE TABLE agent_reports (
    report_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES analysis_runs(run_id),
    snapshot_id TEXT NOT NULL REFERENCES market_snapshots(snapshot_id),
    version_id TEXT NOT NULL REFERENCES strategy_versions(version_id),
    record_json TEXT NOT NULL,
    content_hash TEXT NOT NULL
);
CREATE INDEX idx_agent_reports_run ON agent_reports(run_id);
CREATE TABLE proposal_evidence (
    proposal_id TEXT PRIMARY KEY REFERENCES proposals(proposal_id),
    schema_version INTEGER NOT NULL CHECK(schema_version = 1),
    run_id TEXT REFERENCES analysis_runs(run_id),
    snapshot_id TEXT REFERENCES market_snapshots(snapshot_id),
    version_id TEXT REFERENCES strategy_versions(version_id),
    payload_json TEXT NOT NULL,
    initial_risk_json TEXT,
    content_hash TEXT NOT NULL
);
CREATE INDEX idx_proposal_evidence_run ON proposal_evidence(run_id);
CREATE TABLE broker_events (
    event_id TEXT PRIMARY KEY,
    broker TEXT NOT NULL,
    account_ref TEXT NOT NULL,
    external_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    record_json TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    UNIQUE(broker, account_ref, external_id, event_type)
);
CREATE TRIGGER analysis_run_identity_immutable
BEFORE UPDATE OF run_id, schema_version, request_json, created_at_utc, as_of_utc ON analysis_runs
BEGIN SELECT RAISE(ABORT, 'Analysis run identity is immutable'); END;
CREATE TRIGGER analysis_run_state_guard BEFORE UPDATE OF status ON analysis_runs
WHEN NOT (
    OLD.status = NEW.status
    OR (OLD.status = 'queued' AND NEW.status IN ('running','failed','cancelled'))
    OR (OLD.status = 'running' AND NEW.status IN ('completed','failed','cancelled'))
)
BEGIN SELECT RAISE(ABORT, 'Invalid analysis run transition'); END;
CREATE TRIGGER proposal_snapshot_run_guard BEFORE INSERT ON proposal_evidence
WHEN NEW.snapshot_id IS NOT NULL AND (
    NEW.run_id IS NULL OR NEW.run_id != (SELECT run_id FROM market_snapshots WHERE snapshot_id = NEW.snapshot_id)
)
BEGIN SELECT RAISE(ABORT, 'Proposal snapshot belongs to another run'); END;
CREATE TRIGGER report_snapshot_run_guard BEFORE INSERT ON agent_reports
WHEN NEW.run_id != (SELECT run_id FROM market_snapshots WHERE snapshot_id = NEW.snapshot_id)
BEGIN SELECT RAISE(ABORT, 'Report snapshot belongs to another run'); END;
CREATE TRIGGER proposal_fields_immutable BEFORE UPDATE OF
    proposal_id, created_at_utc, pair, action, order_type, setup_type, timeframe,
    entry_price, entry_zone_low, entry_zone_high, stop_loss, take_profit_1,
    take_profit_2, risk_reward_ratio, sl_pips, tp_pips, suggested_risk_percent,
    suggested_lot_size, confluence_factors_json, invalidation_condition, reasoning,
    trade_rationale_summary, metadata_json ON proposals
BEGIN SELECT RAISE(ABORT, 'Proposal evidence is immutable'); END;
"""

for _table, _key in (
    ("market_snapshots", "snapshot_id"), ("agent_reports", "report_id"),
    ("proposal_evidence", "proposal_id"), ("broker_events", "event_id"),
):
    SCHEMA_V2 += f"""
    CREATE TRIGGER {_table}_immutable_update BEFORE UPDATE ON {_table}
    BEGIN SELECT RAISE(ABORT, 'Immutable research record'); END;
    CREATE TRIGGER {_table}_immutable_delete BEFORE DELETE ON {_table}
    BEGIN SELECT RAISE(ABORT, 'Immutable research record'); END;
    CREATE TRIGGER {_table}_immutable_replace BEFORE INSERT ON {_table}
    WHEN EXISTS(SELECT 1 FROM {_table} WHERE {_key}=NEW.{_key})
    BEGIN SELECT RAISE(ABORT, 'Immutable research record already exists'); END;
    """
