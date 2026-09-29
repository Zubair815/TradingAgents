"""Phase 31: lifecycle ownership and the automatic read-only broker learning loop."""

import ast
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from tradingagents.database.journal import ForexTradeJournal
from tradingagents.database.models import ProposalRecord, ProposalStatus, TradeStatus
from tradingagents.dataflows.forex_data import ForexBar
from tradingagents.journal.manager import ForexJournalManager
from tradingagents.journal.post_close import PostCloseProcessingStatus
from tradingagents.learning.history_provider import MT5TradeHistoryProvider
from tradingagents.mt5.connection import MT5ConnectionManager
from tradingagents.mt5.models import MT5Deal, MT5Position
from tradingagents.mt5.observer import MT5Observer
from tradingagents.mt5.service import MT5ObservationService
from web.forex_routes import get_forex_runtime, reset_forex_state, set_forex_dependencies
from web.server import app


class ReadOnlyTerminal:
    """Narrow fake with no broker execution methods."""
    def __init__(self):
        self.connected = False
        self.initializations = 0
        self.shutdowns = 0

    def initialize(self, **kwargs):
        self.initializations += 1
        self.connected = True
        return True

    def login(self, **kwargs):
        return True

    def terminal_info(self):
        return SimpleNamespace(connected=self.connected)

    def shutdown(self):
        self.shutdowns += 1
        self.connected = False


def eventually(predicate, timeout=4):
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.01)
    assert predicate(), "Background worker did not reach expected state"


@pytest.fixture
def runtime_env(tmp_path):
    reset_forex_state()
    journal = ForexTradeJournal(tmp_path / "runtime.db")
    manager = ForexJournalManager(journal=journal)
    api = ReadOnlyTerminal()
    connection = MT5ConnectionManager(mt5_api=api)
    observer = MagicMock(spec=MT5Observer)
    observer.connection = connection
    observer.get_open_positions.return_value = []
    observer.get_pending_orders.return_value = []
    observer.get_deals.return_value = []
    set_forex_dependencies(journal=journal, journal_manager=manager, mt5_observer=observer)
    runtime = get_forex_runtime()
    runtime.service.poll_interval = .02
    yield runtime, observer, api
    runtime.close()
    eventually(lambda: runtime.closed)
    reset_forex_state()


def position(ticket=1001, **updates):
    values = {"ticket": ticket, "identifier": ticket, "symbol": "EURUSD", "type": "LONG", "volume": 1.0,
                  "price_open": 1.08, "sl": 1.075, "tp": 1.095,
                  "time": datetime.now(timezone.utc)-timedelta(minutes=10)}
    values.update(updates)
    return MT5Position(**values)


def deal(ticket, pos, volume, entry="OUT", **updates):
    values = {"ticket": ticket, "order": ticket+100, "position_id": pos.identifier, "symbol": pos.symbol,
                  "type": "SELL" if entry == "OUT" else "BUY", "entry": entry, "volume": volume,
                  "price": 1.09, "time": pos.time+timedelta(minutes=5), "profit": 100, "commission": -2,
                  "swap": -1, "fee": -.5}
    values.update(updates)
    return MT5Deal(**values)


@pytest.mark.parametrize("failure", ["missing_package", "closed_terminal", "missing_credentials", "unreachable_broker"])
def test_lifespan_starts_without_mt5(runtime_env, failure):
    runtime, observer, api = runtime_env
    if failure == "missing_package":
        observer.connection._mt5 = None
    else:
        api.initialize = MagicMock(side_effect=RuntimeError(failure))
    with TestClient(app) as client:
        assert app.state.forex_runtime is runtime
        client.get("/")
        response = client.get("/api/forex/mt5/status")
        assert response.status_code == 200
        assert response.json()["status"] in ("DISCONNECTED", "UNAVAILABLE")
        assert not response.json()["service_running"]
        assert api.initializations == 0
    assert not runtime.service.is_running


def test_connect_repeat_disconnect_reconnect_shutdown_share_instances(runtime_env):
    runtime, observer, api = runtime_env
    with TestClient(app) as client:
        client.get("/")
        assert runtime.service.observer is observer
        assert runtime.service.journal is runtime.journal_manager.journal is runtime.journal
        assert runtime.processor.learning_mgr is runtime.learning_manager
        assert runtime.learning_manager.history_provider.observer is observer
        assert runtime.learning_manager.store.journal is runtime.journal
        for _ in range(3):
            response = client.post("/api/forex/mt5/connect", json={"login": 123456, "password": "sensitive", "server": "demo"})
            assert response.status_code == 200
            assert response.json()["connected"]
        worker = runtime.service._worker_thread
        assert api.initializations == 1
        eventually(lambda: runtime.service.last_successful_poll_at is not None)
        status = client.get("/api/forex/mt5/status")
        assert "123456" not in status.text and "sensitive" not in status.text
        for key in ("service_running", "mt5_connected", "last_poll_at", "last_successful_poll_at",
                    "last_error", "poll_interval", "tracked_positions", "tracked_orders"):
            assert key in status.json()
        assert client.post("/api/forex/mt5/disconnect").json()["status"] == "DISCONNECTED"
        assert not worker.is_alive()
        calls = observer.get_open_positions.call_count
        time.sleep(.06)
        assert observer.get_open_positions.call_count == calls
        assert client.post("/api/forex/mt5/connect", json={}).json()["connected"]
        assert runtime.service._worker_thread is not worker
        assert api.initializations == 2
    assert not runtime.service.is_running
    assert runtime.closed


def test_lost_connection_can_reconnect_without_second_worker(runtime_env):
    runtime, _, api = runtime_env
    assert runtime.connect()
    first = runtime.service._worker_thread
    api.connected = False
    assert runtime.connect()
    assert not first.is_alive()
    assert runtime.service.is_running
    assert api.initializations == 2


def test_stalled_poll_has_bounded_stop_and_no_duplicate_worker(runtime_env):
    runtime, observer, _ = runtime_env
    entered, release = threading.Event(), threading.Event()
    def blocked():
        entered.set()
        release.wait(5)
        return []
    observer.get_open_positions.side_effect = blocked
    assert runtime.connect()
    assert entered.wait(2)
    worker = runtime.service._worker_thread
    try:
        start = time.monotonic()
        assert not runtime.disconnect(timeout=.02)
        assert time.monotonic()-start < .5
        assert runtime.service.is_running
        runtime.service.start()
        assert runtime.service._worker_thread is worker
        with pytest.raises(RuntimeError, match="stopping"):
            runtime.connect()
        assert runtime.journal.list_trades() == []  # cleanup has not closed DB
    finally:
        release.set()
    eventually(lambda: not runtime._disconnect_pending)
    assert not worker.is_alive()


def test_automatic_proposal_to_lesson_acceptance_flow(runtime_env):
    runtime, observer, _ = runtime_env
    pos = position(comment="proposal-runtime")
    runtime.journal.save_proposal(ProposalRecord(
        proposal_id="proposal-runtime", pair="EURUSD", action="LONG", status=ProposalStatus.APPROVED,
        entry_price=1.08, stop_loss=1.075, take_profit_1=1.095, suggested_lot_size=1,
        created_at_utc=pos.time.isoformat(),
    ))
    observer.get_open_positions.return_value = [pos]
    entry = deal(1, pos, 1, "IN", time=pos.time, price=1.08, profit=0, swap=0)
    observer.get_deals.return_value = [entry]
    observer.get_candles_range.return_value = [
        ForexBar(timestamp=pos.time+timedelta(minutes=i), open=1.08, high=1.096, low=1.079, close=1.09)
        for i in range(1, 6)
    ]
    with TestClient(app) as client:
        client.get("/")
        assert client.post("/api/forex/mt5/connect", json={}).json()["connected"]
        eventually(lambda: len(runtime.journal.list_trades()) == 1)
        trade = runtime.journal.list_trades()[0]
        assert trade.proposal_id == "proposal-runtime"
        observer.get_open_positions.return_value = [pos.model_copy(update={"sl": 1.08, "tp": 1.10})]
        eventually(lambda: runtime.journal.get_trade(trade.trade_id).stop_loss == 1.08)
        partial = deal(2, pos, .4)
        observer.get_deals.return_value = [entry, partial]
        observer.get_open_positions.return_value = [pos.model_copy(update={"sl": 1.08, "tp": 1.10, "volume": .6})]
        eventually(lambda: runtime.journal.get_trade(trade.trade_id).lots == .6)
        final = deal(3, pos, .6, time=pos.time+timedelta(minutes=7), profit=300, price=1.095, reason=5)
        observer.get_deals.return_value = [entry, partial, final]
        observer.get_open_positions.return_value = []
        eventually(lambda: runtime.journal.get_trade(trade.trade_id).metadata.get("post_close_status") == "COMPLETED")
        closed = runtime.journal.get_trade(trade.trade_id)
        assert closed.status == TradeStatus.CLOSED
        assert closed.gross_profit == 400
        assert closed.commission == 7.5 and closed.swap == -2
        assert closed.net_profit == 390.5
        assert closed.metadata["mfe_mae"]["mfe_r"] == pytest.approx(3.2)
        assert "execution_quality" in closed.metadata
        assert closed.reflection
        assert runtime.learning_manager.store.list_lessons()
        events = runtime.journal_manager.timeline.get_events(trade_id=trade.trade_id)
        kinds = [e.event_type.value for e in events]
        assert kinds.count("BREAKEVEN_APPLIED") == 1
        assert kinds.count("TAKE_PROFIT_MODIFIED") == 1
        assert kinds.count("PARTIAL_CLOSE") == 1
        assert kinds.count("POSITION_CLOSED") == 1
        executions = runtime.journal.list_executions_for_trade(trade.trade_id)
        assert len(executions) == 3
        count = len(events)
        for _ in range(3):
            runtime.service.poll_once()
        assert len(runtime.journal_manager.timeline.get_events(trade_id=trade.trade_id)) == count
        assert len(runtime.journal.list_trades()) == 1


def test_post_close_history_failure_preserves_close_and_can_retry(runtime_env):
    runtime, observer, _ = runtime_env
    pos = position()
    observer.get_open_positions.return_value = [pos]
    runtime.service.poll_once()
    trade = runtime.journal.list_trades()[0]
    observer.get_open_positions.return_value = []
    observer.get_deals.return_value = [deal(20, pos, 1)]
    observer.get_candles_range.return_value = []
    runtime.service.poll_once()
    failed = runtime.journal.get_trade(trade.trade_id)
    assert failed.status == TradeStatus.CLOSED
    assert failed.metadata["post_close_status"] == "FAILED"
    other = position(2002)
    observer.get_open_positions.return_value = [other]
    runtime.service.poll_once()
    assert len(runtime.journal.list_trades()) == 2
    observer.get_candles_range.return_value = [ForexBar(timestamp=pos.time+timedelta(minutes=1), open=1.08, high=1.09, low=1.079, close=1.085)]
    observer.connection.connect()
    result = runtime.processor.process_closed_trade(trade.trade_id)
    assert result.status == PostCloseProcessingStatus.COMPLETED
    assert runtime.journal.get_trade(trade.trade_id).net_profit == failed.net_profit


@pytest.mark.parametrize("failure", ["reflection", "lesson_store"])
def test_post_close_failure_is_retryable_and_observation_continues(runtime_env, failure):
    runtime, observer, _ = runtime_env
    observer.connection.connect()
    pos = position()
    observer.get_open_positions.return_value = [pos]
    observer.get_candles_range.return_value = [ForexBar(timestamp=pos.time, open=1.08, high=1.1, low=1.07, close=1.09)]
    runtime.service.poll_once()
    trade_id = runtime.journal.list_trades()[0].trade_id
    observer.get_open_positions.return_value = []
    observer.get_deals.return_value = [deal(30, pos, 1)]
    target = runtime.learning_manager.agent if failure == "reflection" else runtime.learning_manager.store
    method = "reflect" if failure == "reflection" else "save_lessons"
    with patch.object(target, method, side_effect=RuntimeError("temporary failure")):
        runtime.service.poll_once()
    assert runtime.journal.get_trade(trade_id).metadata["post_close_status"] == "FAILED"
    observer.get_open_positions.return_value = [position(2002)]
    runtime.service.poll_once()
    assert len(runtime.journal.list_trades()) == 2
    assert runtime.processor.process_closed_trade(trade_id).status == PostCloseProcessingStatus.COMPLETED


def test_history_provider_checks_actual_connection_method(runtime_env):
    _, observer, _ = runtime_env
    provider = MT5TradeHistoryProvider(observer)
    now = datetime.now(timezone.utc)
    result = provider.get_history("EURUSD", now-timedelta(minutes=2), now)
    assert not result.is_available
    observer.get_candles_range.assert_not_called()


def test_post_close_retry_api_and_outside_holding_window(runtime_env):
    runtime, observer, _ = runtime_env
    pos = position()
    observer.get_open_positions.return_value = [pos]
    runtime.service.poll_once()
    trade_id = runtime.journal.list_trades()[0].trade_id
    # Direct TestClient use here leaves the deterministic test's worker stopped.
    client = TestClient(app)
    client.get("/")
    route = f"/api/forex/journal/trades/{trade_id}/post-close/retry"
    assert client.post(route).status_code == 409
    assert client.post("/api/forex/journal/trades/missing/post-close/retry").status_code == 404
    observer.connection.connect()
    observer.get_open_positions.return_value = []
    observer.get_deals.return_value = [deal(61, pos, 1)]
    observer.get_candles_range.return_value = [ForexBar(timestamp=pos.time-timedelta(days=1), open=1.08, high=1.1, low=1.07, close=1.09)]
    runtime.service.poll_once()
    assert runtime.journal.get_trade(trade_id).metadata["post_close_status"] == "FAILED"
    observer.get_candles_range.return_value = [ForexBar(timestamp=pos.time, open=1.08, high=1.1, low=1.07, close=1.09)]
    response = client.post(route)
    assert response.status_code == 200
    assert response.json()["status"] == "COMPLETED"
    assert runtime.journal.get_trade(trade_id).metadata["post_close_error"] is None
    lesson_count = len(runtime.learning_manager.store.list_lessons())
    assert client.post(route).json()["status"] == "COMPLETED"
    assert len(runtime.learning_manager.store.list_lessons()) == lesson_count


def test_restart_replays_partial_and_final_deals_once(runtime_env):
    runtime, observer, _ = runtime_env
    pos = position(identifier=9999)
    observer.get_open_positions.return_value = [pos]
    runtime.service.poll_once()
    trade_id = runtime.journal.list_trades()[0].trade_id
    partial = deal(41, pos, .4)
    observer.get_open_positions.return_value = [pos.model_copy(update={"volume": .6})]
    observer.get_deals.return_value = [partial]
    runtime.service.poll_once()
    restarted = MT5ObservationService(observer, runtime.journal_manager,
                                      post_close_processor=runtime.processor)
    restarted.poll_once()
    assert runtime.journal.get_trade(trade_id).lots == .6
    assert len(runtime.journal.list_executions_for_trade(trade_id)) == 1
    observer.get_open_positions.return_value = []
    observer.get_deals.return_value = [partial, deal(42, pos, .6)]
    restarted.poll_once()
    restarted.poll_once()
    assert runtime.journal.get_trade(trade_id).status == TradeStatus.CLOSED
    assert len(runtime.journal.list_executions_for_trade(trade_id)) == 2
    observer.get_open_positions.return_value = [pos]
    restarted.poll_once()  # Stale position snapshots cannot reopen confirmed closes.
    MT5ObservationService(observer, runtime.journal_manager,
                          post_close_processor=runtime.processor).poll_once()
    assert len(runtime.journal.list_trades()) == 1


@pytest.mark.parametrize("status", ["PENDING", "PROCESSING"])
def test_restart_recovers_interrupted_post_close(runtime_env, status):
    from tradingagents.journal.broker_deals import record_broker_deal
    from tradingagents.journal.post_close import ClosedTradeProcessor

    runtime, observer, _ = runtime_env
    observer.connection.connect()
    pos = position()
    observer.get_open_positions.return_value = [pos]
    runtime.service.poll_once()
    trade_id = runtime.journal.list_trades()[0].trade_id
    record_broker_deal(runtime.journal, trade_id, deal(51, pos, 1))
    runtime.journal.update_trade_metadata(trade_id, {"post_close_status": status})
    observer.get_open_positions.return_value = []
    observer.get_candles_range.return_value = [ForexBar(timestamp=pos.time, open=1.08, high=1.1, low=1.07, close=1.09)]
    processor = ClosedTradeProcessor(runtime.journal, runtime.learning_manager.history_provider, runtime.learning_manager)
    restarted = MT5ObservationService(observer, runtime.journal_manager, post_close_processor=processor)
    restarted.poll_once()
    assert runtime.journal.get_trade(trade_id).metadata["post_close_status"] == "COMPLETED"
    lesson_count = len(runtime.learning_manager.store.list_lessons())
    restarted.poll_once()
    assert len(runtime.learning_manager.store.list_lessons()) == lesson_count


def test_runtime_code_never_calls_broker_execution():
    root = Path(__file__).resolve().parents[1]
    for folder in ("tradingagents", "web"):
        for path in (root / folder).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    name = getattr(node.func, "attr", getattr(node.func, "id", ""))
                    assert name != "order_send", str(path)
