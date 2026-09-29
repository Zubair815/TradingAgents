from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from web import server
from web.server import app


def test_run_cancel_endpoint_sets_cancelled_status():
    client = TestClient(app)
    client.get("/")
    run_id = "run-cancel-1"
    server._runs[run_id] = {
        "run_id": run_id,
        "ticker": "AAPL",
        "date": "2026-09-29",
        "status": "queued",
        "provider": "openai",
        "quick_model": "gpt-4o-mini",
        "deep_model": "gpt-4o",
        "started_at": datetime.now().isoformat(),
        "finished_at": None,
        "error": None,
        "signal": None,
    }
    server._run_events.setdefault(run_id, [])

    res = client.post(f"/api/runs/{run_id}/cancel")
    assert res.status_code == 200
    assert server._runs[run_id]["status"] == "cancelled"
    assert server._runs[run_id]["error"] == "Cancelled by user"


def test_run_store_prunes_expired_runs():
    expired_id = "expired-run"
    server._runs[expired_id] = {
        "run_id": expired_id,
        "status": "queued",
        "started_at": (datetime.now() - timedelta(hours=1)).isoformat(),
    }
    live_id = "live-run"
    server._runs[live_id] = {
        "run_id": live_id,
        "status": "queued",
        "started_at": datetime.now().isoformat(),
    }

    pruned = server._prune_expired_runs()
    assert expired_id in pruned
    assert live_id in server._runs
    assert expired_id not in server._runs
