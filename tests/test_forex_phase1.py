"""Integrity and upgrade regressions for the shared research persistence layer."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from tradingagents.agents.schemas_forex import ForexAction, ForexTraderProposal
from tradingagents.database import migrations
from tradingagents.database.journal import ForexTradeJournal
from tradingagents.research.contracts import (
    AgentReport,
    AnalysisRequest,
    AnalysisRun,
    BrokerEvent,
    MarketSnapshot,
    RunStatus,
)

CUTOFF = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)


def run_and_snapshot(journal):
    run = AnalysisRun(request=AnalysisRequest(), as_of_utc=CUTOFF)
    journal.research.create_run(run)
    snapshot = MarketSnapshot(run_id=run.run_id, pair="EURUSD", as_of_utc=CUTOFF,
                              retrieved_at_utc=CUTOFF, source="test-provider", payload_json='{"candles":[]}')
    journal.research.save_snapshot(snapshot)
    return run, snapshot


def test_upgrade_preserves_legacy_rows_and_is_idempotent(tmp_path, monkeypatch):
    path = tmp_path / "legacy.db"
    with monkeypatch.context() as patch:
        patch.setattr(migrations, "MIGRATIONS", migrations.MIGRATIONS[:1])
        assert migrations.run_migrations(path) == 1
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO proposals (proposal_id,created_at_utc,pair,action,order_type,setup_type,timeframe,status) VALUES ('legacy','2026-09-01T00:00:00+00:00','EURUSD','NO_TRADE','MARKET','PULLBACK','H1','PROPOSED')")
    assert migrations.run_migrations(path) == 1
    assert migrations.run_migrations(path) == 0
    with ForexTradeJournal(path) as journal:
        old = journal.get_proposal("legacy")
        assert old.proposal_id == "legacy"
        assert old.proposal_payload is None  # Missing historical fields are not invented.
        assert old.to_forex_trader_proposal().action == ForexAction.NO_TRADE
        assert journal.research.get_initial_proposal_evidence("legacy") is None


def test_migration_failure_rolls_back_ddl_and_version(tmp_path, monkeypatch):
    path = tmp_path / "atomic.db"
    migrations.run_migrations(path)
    bad = {"version": 3, "description": "intentional failure", "sql":
           "CREATE TABLE should_rollback (id TEXT);\nINSERT INTO nonexistent VALUES ('bad');\n"}
    monkeypatch.setattr(migrations, "MIGRATIONS", [*migrations.MIGRATIONS, bad])
    with pytest.raises(sqlite3.OperationalError):
        migrations.run_migrations(path)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='should_rollback'").fetchone() is None
        assert migrations.get_current_schema_version(conn) == 2


def test_concurrent_migrations_are_idempotent(tmp_path):
    path = tmp_path / "concurrent.db"
    with ThreadPoolExecutor(max_workers=4) as workers:
        results = list(workers.map(lambda _: migrations.run_migrations(path), range(4)))
    assert sum(results) == 2
    assert migrations.get_current_schema_version(path) == 2


def test_research_round_trip_survives_reopening(tmp_path):
    path = tmp_path / "research.db"
    with ForexTradeJournal(path) as journal:
        run, snapshot = run_and_snapshot(journal)
        version = journal.record_strategy_version("test-strategy", "prompt-sha256", "test-model")
        journal.research.link_version(run.run_id, "technical", version.version_id)
        report = AgentReport(run_id=run.run_id, snapshot_id=snapshot.snapshot_id,
                             agent="technical", content="Sourced observations", version_id=version.version_id)
        journal.research.save_report(report)
        proposal = ForexTraderProposal(pair="EURUSD", action=ForexAction.NO_TRADE, reasoning="No setup")
        identity = journal.save_proposal(proposal, risk_decision={"decision": "APPROVE"},
                                         run_id=run.run_id, snapshot_id=snapshot.snapshot_id, version_id=version.version_id)
        journal.research.transition_run(run.run_id, RunStatus.RUNNING)
        journal.research.transition_run(run.run_id, RunStatus.COMPLETED)
    with ForexTradeJournal(path) as journal:
        assert journal.research.get_run(run.run_id).status == RunStatus.COMPLETED
        assert journal.research.get_snapshot(snapshot.snapshot_id) == snapshot
        assert journal.research.get_report(report.report_id) == report
        record = journal.get_proposal(identity)
        assert record.run_id == run.run_id and record.version_id == version.version_id
        assert record.to_forex_trader_proposal().model_dump() == proposal.model_dump()
        assert journal.list_trades() == []


@pytest.mark.parametrize("payload", [{}, {"decision": "APPROVE"}])
def test_initial_risk_evidence_survives_later_status_update(payload):
    with ForexTradeJournal(":memory:") as journal:
        proposal = ForexTraderProposal(pair="EURUSD", action=ForexAction.NO_TRADE, reasoning="No setup")
        identity = journal.save_proposal(proposal, risk_decision=payload)
        journal.update_proposal_status(identity, "REJECTED", risk_decision={"decision": "REJECT"})
        evidence = journal.research.get_initial_proposal_evidence(identity)
        assert evidence["risk"] == payload
        assert evidence["proposal"] == proposal.model_dump(mode="json")


def test_snapshot_is_detached_and_duplicate_save_is_idempotent():
    with ForexTradeJournal(":memory:") as journal:
        _, snapshot = run_and_snapshot(journal)
        snapshot.payload["candles"].append("mutation")
        assert snapshot.payload == {"candles": []}
        assert journal.research.save_snapshot(snapshot) == snapshot.snapshot_id
        with pytest.raises(ValueError, match="conflicts"):
            journal.research.save_snapshot(snapshot.model_copy(update={"payload_json": '{"changed":true}'}))
        assert journal.research.get_snapshot(snapshot.snapshot_id) == snapshot


@pytest.mark.parametrize("statement", [
    "UPDATE market_snapshots SET record_json='{}' WHERE snapshot_id=?",
    "DELETE FROM market_snapshots WHERE snapshot_id=?",
    "INSERT OR REPLACE INTO market_snapshots SELECT snapshot_id,run_id,'{}',content_hash FROM market_snapshots WHERE snapshot_id=?",
])
def test_database_blocks_snapshot_mutation(statement):
    with ForexTradeJournal(":memory:") as journal:
        _, snapshot = run_and_snapshot(journal)
        with pytest.raises(sqlite3.IntegrityError, match="Immutable"), journal._get_connection() as conn:
            conn.execute(statement, (snapshot.snapshot_id,))
        assert journal.research.get_snapshot(snapshot.snapshot_id) == snapshot


def test_bad_research_links_rollback_proposal_insert():
    with ForexTradeJournal(":memory:") as journal:
        run, snapshot = run_and_snapshot(journal)
        other = AnalysisRun(request=AnalysisRequest(), as_of_utc=CUTOFF)
        journal.research.create_run(other)
        version = journal.record_strategy_version("test", "hash", "model")
        report = AgentReport(run_id=other.run_id, snapshot_id=snapshot.snapshot_id,
                             agent="technical", content="wrong run", version_id=version.version_id)
        with pytest.raises(sqlite3.IntegrityError, match="another run"):
            journal.research.save_report(report)
        with pytest.raises(sqlite3.IntegrityError):
            journal.save_proposal(ForexTraderProposal(pair="EURUSD", action=ForexAction.NO_TRADE, reasoning="No setup"),
                                  run_id=run.run_id, snapshot_id="missing")
        assert journal.list_proposals() == []
        assert journal.research.get_report(report.report_id) is None


def test_run_transitions_are_checked_in_api_and_database():
    with ForexTradeJournal(":memory:") as journal:
        run, _ = run_and_snapshot(journal)
        with pytest.raises(ValueError, match="Invalid run transition"):
            journal.research.transition_run(run.run_id, "completed")
        journal.research.transition_run(run.run_id, "running")
        with pytest.raises(ValueError, match="require an error"):
            journal.research.transition_run(run.run_id, "failed")
        journal.research.transition_run(run.run_id, "failed", error="provider unavailable")
        assert journal.research.get_run(run.run_id).error == "provider unavailable"
        with pytest.raises(sqlite3.IntegrityError, match="Invalid analysis run transition"), journal._get_connection() as conn:
            conn.execute("UPDATE analysis_runs SET status='completed' WHERE run_id=?", (run.run_id,))


@pytest.mark.parametrize("kwargs", [{"schema_version": 2}, {"as_of_utc": "2026-09-27T12:00:00"}])
def test_unknown_schema_versions_and_naive_timestamps_rejected(kwargs):
    with pytest.raises(ValidationError):
        AnalysisRun(request=AnalysisRequest(), **({"as_of_utc": CUTOFF} | kwargs))


@pytest.mark.parametrize("kwargs", [{"pair": "garbage"}, {"timeframe": "garbage"},
                                     {"account_balance": float("inf")}, {"date": "not-a-date"},
                                     {"analysts": ["unknown"]}, {"higher_timeframes": ["invalid"]}])
def test_request_validation_shared_with_web(kwargs):
    from web.forex_routes import ForexAnalysisRequest
    assert ForexAnalysisRequest is AnalysisRequest
    with pytest.raises(ValidationError):
        AnalysisRequest(**kwargs)


def test_broker_event_identity_and_provenance_round_trip():
    with ForexTradeJournal(":memory:") as journal:
        event = BrokerEvent(broker="test", account_ref="account-test", external_id="deal-42", event_type="deal",
                            occurred_at_utc=CUTOFF, observed_at_utc=CUTOFF, payload_json='{"price":1.08}')
        assert journal.research.save_broker_event(event) == event.event_id
        assert journal.research.save_broker_event(event) == event.event_id
        assert journal.research.get_broker_event(event.event_id) == event
        with pytest.raises(sqlite3.IntegrityError):
            journal.research.save_broker_event(event.model_copy(update={"event_id": "duplicate-natural-key"}))


def test_portfolio_manager_returns_persisted_identity_and_propagates_storage_failure(monkeypatch):
    from tradingagents.graph.forex_graph import create_forex_portfolio_manager
    proposal = ForexTraderProposal(pair="EURUSD", action=ForexAction.NO_TRADE, reasoning="No setup")
    state = {"company_of_interest": "EURUSD", "trade_date": "2026-09-27", "forex_proposal": proposal.model_dump()}
    with ForexTradeJournal(":memory:") as journal:
        manager = create_forex_portfolio_manager(journal=journal)
        result = manager(state)
        assert journal.get_proposal(result["forex_proposal_id"]).pair == "EURUSD"
        monkeypatch.setattr(journal, "save_proposal", lambda **kwargs: (_ for _ in ()).throw(sqlite3.OperationalError("disk full")))
        with pytest.raises(sqlite3.OperationalError, match="disk full"):
            manager(state)
