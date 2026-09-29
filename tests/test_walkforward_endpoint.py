"""Tests for Walk-Forward validation endpoint."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

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


def test_walkforward_with_user_candles(client):
    candles = generate_dict_bars(count=40)
    payload = {
        "pair": "EURUSD",
        "timeframe": "H1",
        "date_from": None,
        "date_to": None,
        "candles": candles,
    }
    res = client.post("/api/forex/backtest/walkforward?n_splits=1", json=payload)
    assert res.status_code == 200
    j = res.json()
    assert "validation_id" in j
    assert "markdown_summary" in j or "markdown_summary" in j.get("markdown_summary", j)

    # Ensure the run was stored and appears in runs listing
    runs_res = client.get("/api/forex/backtest/runs")
    assert runs_res.status_code == 200
    runs = runs_res.json().get("backtests", [])
    assert any(r.get("backtest_id") and r.get("strategy") == "walk_forward_validation" for r in runs)
