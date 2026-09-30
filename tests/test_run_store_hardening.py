"""Stress and retention contracts for the local single-process web runtime."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from tradingagents.database.journal import ForexTradeJournal
from tradingagents.research.contracts import AnalysisRequest
from web import forex_routes, server
from web.retention import RetentionPolicy


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


@pytest.fixture(autouse=True)
def isolated_transient_stores(monkeypatch):
    policy = RetentionPolicy(
        stale_active_seconds=10,
        terminal_max_age_seconds=20,
        terminal_max_count=2,
        event_max_count=3,
        backtest_max_age_seconds=30,
        backtest_max_count=2,
        tombstone_max_age_seconds=60,
        tombstone_max_count=10,
    )
    monkeypatch.setattr(server, "_RUN_RETENTION_POLICY", policy)
    monkeypatch.setattr(forex_routes, "_FOREX_RETENTION_POLICY", policy)
    with server._run_store_lock:
        server._runs.clear()
        server._run_events.clear()
        server._completed_reports.clear()
        server._expired_runs.clear()
    with forex_routes._lock:
        forex_routes._forex_runs.clear()
        forex_routes._forex_run_events.clear()
        forex_routes._forex_completed_reports.clear()
        forex_routes._backtest_runs.clear()
        forex_routes._expired_forex_runs.clear()
        forex_routes._expired_backtests.clear()
        forex_routes._forex_cancellations.clear()
    yield policy
    with server._run_store_lock:
        server._runs.clear()
        server._run_events.clear()
        server._completed_reports.clear()
        server._expired_runs.clear()
    with forex_routes._lock:
        forex_routes._forex_runs.clear()
        forex_routes._forex_run_events.clear()
        forex_routes._forex_completed_reports.clear()
        forex_routes._backtest_runs.clear()
        forex_routes._expired_forex_runs.clear()
        forex_routes._expired_backtests.clear()
        forex_routes._forex_cancellations.clear()


def test_stale_active_pruned_but_recent_active_retained(isolated_transient_stores):
    now = 1_000.0
    server._runs.update({
        "stale": {"run_id": "stale", "status": "running", "started_at": _iso(now - 11)},
        "recent": {"run_id": "recent", "status": "running", "started_at": _iso(now - 9)},
    })
    server._run_events.update({"stale": [{"type": "status"}], "recent": []})

    assert server._prune_expired_runs(now=now) == ["stale"]
    assert "recent" in server._runs
    assert "stale" not in server._run_events
    assert "stale" in server._expired_runs


def test_run_cancel_endpoint_sets_terminal_state(isolated_transient_stores):
    run_id = "run-cancel-1"
    server._runs[run_id] = {
        "run_id": run_id,
        "status": "queued",
        "started_at": _iso(time.time()),
    }
    server._run_events[run_id] = []
    with TestClient(server.app) as client:
        client.get("/")
        response = client.post(f"/api/runs/{run_id}/cancel")
    assert response.status_code == 200
    assert server._runs[run_id]["status"] == "cancelled"
    assert server._runs[run_id]["finished_at"]
    assert server._run_events[run_id][-1]["type"] == "cancelled"


def test_forex_cancel_is_cooperative_terminal_and_never_persists_success(
    isolated_transient_stores,
):
    run_id = "fx-cancel-cooperative"
    started = _iso(time.time())
    forex_routes._forex_runs[run_id] = {
        "run_id": run_id,
        "status": "queued",
        "started_at": started,
        "last_activity_at": started,
    }
    forex_routes._forex_run_events[run_id] = []
    forex_routes._forex_cancellations[run_id] = threading.Event()
    provider_returned = threading.Event()
    graph = MagicMock()

    def stream(*_args, **_kwargs):
        yield {"forex_technical_report": "done"}
        provider_returned.wait(timeout=2)
        yield {"forex_macro_report": "should not be processed"}

    graph.stream.side_effect = stream
    worker = threading.Thread(
        target=forex_routes._run_forex_analysis,
        args=(run_id, AnalysisRequest(account_source="manual")),
    )
    with patch("web.forex_routes.ForexTradingAgentsGraph", return_value=graph):
        worker.start()
        deadline = time.time() + 2
        while not forex_routes._forex_run_events[run_id] and time.time() < deadline:
            time.sleep(0.01)
        first = asyncio.run(forex_routes.cancel_forex_run(run_id))
        second = asyncio.run(forex_routes.cancel_forex_run(run_id))
        provider_returned.set()
        worker.join(timeout=2)

    assert first["status"] == second["status"] == "cancelled"
    assert forex_routes._forex_runs[run_id]["status"] == "cancelled"
    assert run_id not in forex_routes._forex_completed_reports
    terminal_events = [
        event["type"]
        for event in forex_routes._forex_run_events[run_id]
        if event["type"] in {"complete", "error", "cancelled"}
    ]
    assert terminal_events == ["cancelled"]


def test_terminal_count_eviction_releases_events_and_report_aliases(isolated_transient_stores):
    now = 2_000.0
    for index in range(3):
        run_id = f"run-{index}"
        server._runs[run_id] = {
            "run_id": run_id,
            "status": "completed",
            "started_at": _iso(now - 10 + index),
            "finished_at": _iso(now - 10 + index),
            "report_id": f"report-{index}",
        }
        server._run_events[run_id] = [{"type": "complete"}]
        report = {"run_id": run_id, "payload": index}
        server._completed_reports[run_id] = report
        server._completed_reports[f"report-{index}"] = report

    assert server._prune_expired_runs(now=now) == ["run-0"]
    assert list(server._runs) == ["run-1", "run-2"]
    assert "run-0" not in server._run_events
    assert not any(report.get("run_id") == "run-0" for report in server._completed_reports.values())


def test_event_buffer_is_bounded_and_keeps_terminal_suffix(isolated_transient_stores):
    server._runs["run"] = {"run_id": "run", "status": "running", "started_at": _iso(time.time())}
    server._run_events["run"] = []
    for index in range(5):
        server._emit("run", "node", {"index": index})
    server._emit("run", "complete", {"status": "completed"})

    events = server._run_events["run"]
    assert len(events) == 3
    assert [event["data"].get("index") for event in events[:-1]] == [3, 4]
    assert events[-1]["type"] == "complete"


def test_forex_backtests_are_bounded_and_expiration_is_distinct(isolated_transient_stores):
    now = 3_000.0
    for index in range(3):
        forex_routes._backtest_runs[f"bt-{index}"] = {
            "backtest_id": f"bt-{index}",
            "status": "completed",
            "created_at": _iso(now - 3 + index),
        }
    forex_routes._prune_expired_forex_runs(now=now)

    assert list(forex_routes._backtest_runs) == ["bt-1", "bt-2"]
    assert "bt-0" in forex_routes._expired_backtests
    with pytest.raises(HTTPException) as exc_info:
        forex_routes._backtest_not_found("bt-0")
    assert exc_info.value.status_code == 410


def test_pruning_transient_forex_state_does_not_touch_journal(tmp_path, isolated_transient_stores):
    journal = ForexTradeJournal(tmp_path / "authoritative.db")
    trade = journal.record_trade_open(
        pair="EURUSD",
        action="LONG",
        open_price=1.08,
        stop_loss=1.07,
        take_profit=1.10,
        lots=0.1,
    )
    now = 4_000.0
    forex_routes._forex_runs["expired"] = {
        "run_id": "expired",
        "status": "completed",
        "finished_at": _iso(now - 21),
    }
    forex_routes._forex_run_events["expired"] = [{"type": "complete"}]

    forex_routes._prune_expired_forex_runs(now=now)

    assert journal.get_trade(trade.trade_id).trade_id == trade.trade_id
    journal.close()


def test_concurrent_add_snapshot_and_prune_is_safe(isolated_transient_stores):
    now = time.time()

    def add(index: int):
        with server._run_store_lock:
            run_id = f"concurrent-{index}"
            server._runs[run_id] = {
                "run_id": run_id,
                "status": "completed",
                "finished_at": _iso(now + index / 1000),
            }
            server._run_events[run_id] = []
        server._prune_expired_runs(now=now + 1)

    def snapshot(_: int):
        with server._run_store_lock:
            return [dict(run) for run in server._runs.values()]

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(add, range(100)))
        snapshots = list(pool.map(snapshot, range(100)))

    assert all(isinstance(item, list) for item in snapshots)
    assert len(server._runs) <= isolated_transient_stores.terminal_max_count


def test_sse_exits_after_terminal_event(isolated_transient_stores):
    server._runs["terminal"] = {
        "run_id": "terminal",
        "status": "completed",
        "started_at": _iso(time.time()),
        "finished_at": _iso(time.time()),
    }
    server._run_events["terminal"] = [
        {"type": "complete", "data": {"status": "completed"}, "ts": time.time()}
    ]
    with (
        TestClient(server.app) as client,
        client.stream("GET", "/api/runs/terminal/events") as response,
    ):
        body = "".join(response.iter_text())
    assert response.status_code == 200
    assert "event: complete" in body


def test_runtime_diagnostics_are_aggregate_and_secret_free(isolated_transient_stores):
    with TestClient(server.app) as client:
        client.get("/")
        response = client.get("/api/runtime/diagnostics")
    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"equities", "forex"}
    encoded = json.dumps(payload).lower()
    assert not any(secret in encoded for secret in ("api_key", "password", "token", "broker"))
