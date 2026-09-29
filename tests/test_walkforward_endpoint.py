"""Tests for Walk-Forward validation endpoint."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from tradingagents.database.journal import ForexTradeJournal
from tradingagents.journal.manager import ForexJournalManager
from tradingagents.mt5.observer import MT5Observer
from web.forex_routes import reset_forex_state, set_forex_dependencies
from web.server import app


@pytest.fixture()
def client():
    reset_forex_state()
    in_memory_journal = ForexTradeJournal(db_path=":memory:", auto_migrate=True)
    journal_mgr = ForexJournalManager(journal=in_memory_journal)
    mock_conn = MagicMock()
    mock_conn.get_status.return_value = None
    mock_conn.is_connected.return_value = False
    mock_observer = MagicMock(spec=MT5Observer)
    mock_observer.connection = mock_conn
    set_forex_dependencies(journal=in_memory_journal, journal_manager=journal_mgr, mt5_observer=mock_observer)
    c = TestClient(app)
    c.get("/")
    yield c
    reset_forex_state()


def make_candle_dict(ts: datetime, open_v: float, high: float, low: float, close: float):
    return {
        "time": ts.isoformat(),
        "open": open_v,
        "high": high,
        "low": low,
        "close": close,
        "volume": 100.0,
    }


def generate_dict_bars(count: int = 50, base_price: float = 1.0800, interval_minutes: int = 60):
    start = datetime(2025, 1, 1, 0, 0, tzinfo=timezone.utc)
    bars = []
    curr = base_price
    for i in range(count):
        ts = start + timedelta(minutes=i * interval_minutes)
        o = curr
        h = o + 0.0010
        low = o - 0.0010
        c = o + 0.0005
        curr = c
        bars.append(make_candle_dict(ts, o, h, low, c))
    return bars


def test_walkforward_with_explicit_demo_candles(client):
    candles = generate_dict_bars(count=40)
    payload = {
        "pair": "EURUSD",
        "timeframe": "H1",
        "date_from": None,
        "date_to": None,
        "candles": candles,
        "demo_mode": True,
    }
    res = client.post("/api/forex/backtest/walkforward?n_splits=1", json=payload)
    assert res.status_code == 200
    j = res.json()
    assert "validation_id" in j
    assert "markdown_summary" in j or "markdown_summary" in j.get("markdown_summary", j)

    # Ensure the run was stored and appears in runs listing
    runs_res = client.get("/api/forex/backtest/runs")
    assert runs_res.status_code == 200
    runs = runs_res.json().get("runs", [])
    listed = next(r for r in runs if r.get("backtest_id") == j["validation_id"])
    assert listed["mode"] == "WALK_FORWARD"
    assert listed["data_source"] == "user_supplied_unverified"
    assert listed["split_count"] == 1
    assert listed["total_trades"] is None
    assert listed["win_rate_pct"] is None
    assert listed["profit_factor"] is None
    assert listed["net_profit"] is None

    detail = client.get(f"/api/forex/backtest/{j['validation_id']}")
    assert detail.status_code == 200
    assert detail.json()["mode"] == "WALK_FORWARD"
    assert detail.json()["result"] is None
    assert detail.json()["validation_report"]["splits"]


def test_walkforward_rejects_unverified_candles_without_demo_opt_in(client):
    response = client.post("/api/forex/backtest/walkforward", json={
        "pair": "EURUSD",
        "timeframe": "H1",
        "candles": generate_dict_bars(count=40),
    })
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "HISTORICAL_DATA_UNAVAILABLE"
    assert client.get("/api/forex/backtest/runs").json()["count"] == 0


def test_frontend_consumes_canonical_runs_and_escapes_structured_detail():
    root = Path(__file__).resolve().parents[1]
    source = (root / "web/static/app.js").read_text(encoding="utf-8")

    assert "const runs = data.runs || []" in source
    assert "data.backtests" not in source
    assert "r.total_trades != null" in source
    assert "r.total_trades ||" not in source
    assert "Technical details" in source
    assert "Walk-forward splits (out-of-sample)" in source
    assert "escapeText(r.data_source" in source
    assert "reasons.map(reason => `<li>${escapeText(reason)}</li>`)" in source
    assert "escapeText(JSON.stringify(data, null, 2))" in source


def test_walkforward_provider_failure_is_safe_and_saves_nothing(client):
    with patch(
        "tradingagents.backtest.historical_data.load_historical_candles",
        side_effect=RuntimeError("SENTINEL_PROVIDER_SECRET"),
    ):
        response = client.post("/api/forex/backtest/walkforward", json={
            "pair": "EURUSD",
            "timeframe": "H1",
        })

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "WALK_FORWARD_FAILED"
    assert "SENTINEL_PROVIDER_SECRET" not in response.text
    assert client.get("/api/forex/backtest/runs").json()["count"] == 0
