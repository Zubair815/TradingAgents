"""Unit & Integration Tests for Continuous Read-Only MT5 Observation Service (Phase 8)."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from tradingagents.agents.schemas_forex import ForexAction
from tradingagents.database.journal import ForexTradeJournal
from tradingagents.database.models import TradeStatus
from tradingagents.journal.manager import ForexJournalManager
from tradingagents.journal.models import EventType
from tradingagents.mt5.models import MT5Deal, MT5Order, MT5Position
from tradingagents.mt5.service import MT5ObservationService


@pytest.fixture
def memory_journal_mgr() -> ForexJournalManager:
    journal = ForexTradeJournal(db_path=":memory:", auto_migrate=True)
    mgr = ForexJournalManager(journal=journal)
    yield mgr
    mgr.close()


def test_mt5_observation_service_lifecycle_and_snapshots(memory_journal_mgr: ForexJournalManager):
    """Test 6-step snapshot progression:
    poll 1: nothing
    poll 2: open trade
    poll 3: same state (idempotent, no dupes)
    poll 4: SL changed
    poll 5: partial close
    poll 6: final close
    Assert:
    one logical trade, no duplicates, correct event timeline.
    """
    mock_observer = MagicMock()
    service = MT5ObservationService(
        observer=mock_observer,
        journal_mgr=memory_journal_mgr,
        poll_interval_seconds=0.1,
        auto_reconcile=True,
    )

    # -------------------------------------------------------------
    # Poll 1: Nothing
    # -------------------------------------------------------------
    mock_observer.get_open_positions.return_value = []
    mock_observer.get_pending_orders.return_value = []
    mock_observer.get_deals.return_value = []

    res1 = service.poll_once()
    assert res1["events_count"] == 0
    assert len(memory_journal_mgr.journal.list_trades()) == 0

    # -------------------------------------------------------------
    # Poll 2: Open Trade (Ticket 1001)
    # -------------------------------------------------------------
    pos_open = MT5Position(
        ticket=1001,
        time=datetime.now(timezone.utc),
        type=ForexAction.LONG,  # BUY / LONG
        magic=0,
        identifier=1001,
        reason=0,
        volume=1.0,
        price_open=1.0850,
        sl=1.0810,
        tp=1.0930,
        price_current=1.0855,
        swap=0.0,
        profit=50.0,
        symbol="EURUSD",
        comment="Manual test trade",
    )
    mock_observer.get_open_positions.return_value = [pos_open]

    res2 = service.poll_once()
    assert res2["events_count"] == 1
    assert res2["events"][0]["type"] == "NEW_POSITION"

    trades = memory_journal_mgr.journal.list_trades()
    assert len(trades) == 1
    trade_id = trades[0].trade_id
    assert trades[0].status == TradeStatus.OPEN
    assert trades[0].lots == 1.0
    assert trades[0].open_price == 1.0850

    # -------------------------------------------------------------
    # Poll 3: Same State (Idempotent - No Duplicate Events/Trades)
    # -------------------------------------------------------------
    res3 = service.poll_once()
    assert res3["events_count"] == 0
    assert len(memory_journal_mgr.journal.list_trades()) == 1

    # -------------------------------------------------------------
    # Poll 4: SL Changed (from 1.0810 to 1.0830)
    # -------------------------------------------------------------
    pos_sl_moved = MT5Position(
        ticket=1001,
        time=pos_open.time,
        type=ForexAction.LONG,
        magic=0,
        identifier=1001,
        reason=0,
        volume=1.0,
        price_open=1.0850,
        sl=1.0830,  # Modified SL
        tp=1.0930,
        price_current=1.0870,
        swap=0.0,
        profit=200.0,
        symbol="EURUSD",
        comment="Manual test trade",
    )
    mock_observer.get_open_positions.return_value = [pos_sl_moved]

    res4 = service.poll_once()
    assert res4["events_count"] == 1
    assert res4["events"][0]["type"] == "SL_MODIFIED"

    # Trade in journal should have updated SL
    updated_trade = memory_journal_mgr.journal.get_trade(trade_id)
    assert updated_trade.stop_loss == 1.0830
    assert len(memory_journal_mgr.journal.list_trades()) == 1

    # -------------------------------------------------------------
    # Poll 5: Partial Close (Volume reduced from 1.0 to 0.6 lots)
    # -------------------------------------------------------------
    pos_partial = MT5Position(
        ticket=1001,
        time=pos_open.time,
        type=ForexAction.LONG,
        magic=0,
        identifier=1001,
        reason=0,
        volume=0.6,  # 0.4 closed
        price_open=1.0850,
        sl=1.0830,
        tp=1.0930,
        price_current=1.0900,
        swap=0.0,
        profit=300.0,
        symbol="EURUSD",
        comment="Manual test trade",
    )
    partial_deal = MT5Deal(
        ticket=5001,
        order=2001,
        time=datetime.now(timezone.utc),
        type="DEAL_TYPE_SELL",
        entry="OUT",
        magic=0,
        position_id=1001,
        reason=0,
        volume=0.4,
        price=1.0900,
        commission=-2.0,
        swap=0.0,
        profit=200.0,
        fee=0.0,
        symbol="EURUSD",
        comment="Partial take profit",
    )
    mock_observer.get_open_positions.return_value = [pos_partial]
    mock_observer.get_deals.return_value = [partial_deal]

    res5 = service.poll_once()
    assert res5["events_count"] == 1
    assert res5["events"][0]["type"] == "PARTIAL_CLOSE"
    assert res5["events"][0]["volume_closed"] == 0.4

    trade_after_partial = memory_journal_mgr.journal.get_trade(trade_id)
    assert trade_after_partial.lots == 0.6
    assert trade_after_partial.status == TradeStatus.OPEN

    # -------------------------------------------------------------
    # Poll 6: Final Close
    # -------------------------------------------------------------
    exit_deal = MT5Deal(
        ticket=5002,
        order=2002,
        time=datetime.now(timezone.utc),
        type="DEAL_TYPE_SELL",
        entry="OUT",
        magic=0,
        position_id=1001,
        reason=0,
        volume=0.6,
        price=1.0930,
        commission=-3.0,
        swap=-1.5,
        profit=480.0,
        fee=0.0,
        symbol="EURUSD",
        comment="Full close hit target",
    )
    mock_observer.get_open_positions.return_value = []  # No open positions anymore
    mock_observer.get_deals.return_value = [partial_deal, exit_deal]

    res6 = service.poll_once()
    assert res6["events_count"] == 1
    assert res6["events"][0]["type"] == "POSITION_CLOSED"

    # Assert: Exactly one logical trade exists, and it is CLOSED
    all_trades = memory_journal_mgr.journal.list_trades()
    assert len(all_trades) == 1
    final_trade = all_trades[0]
    assert final_trade.trade_id == trade_id
    assert final_trade.status == TradeStatus.CLOSED
    assert final_trade.close_price == 1.0930
    assert final_trade.gross_profit == 680.0  # partial and final broker fills
    assert final_trade.commission == 5.0
    assert final_trade.swap == -1.5
    assert final_trade.net_profit == 673.5
    assert len(memory_journal_mgr.journal.list_executions_for_trade(trade_id)) == 2

    # Assert: Timeline contains chronological audit trail
    timeline = memory_journal_mgr.get_timeline(trade_id=trade_id)
    event_types = [e.event_type for e in timeline]
    assert EventType.POSITION_OPENED in event_types
    assert EventType.PARTIAL_CLOSE in event_types
    assert EventType.POSITION_CLOSED in event_types


def test_mt5_service_restart_reconstruction(memory_journal_mgr: ForexJournalManager):
    """Verify that restarting the service reconstructs active state from the journal."""
    # Pre-populate journal with an active trade
    trade = memory_journal_mgr.journal.record_trade_open(
        pair="GBPUSD",
        action=ForexAction.LONG,
        open_price=1.2650,
        stop_loss=1.2600,
        take_profit=1.2750,
        lots=0.5,
        metadata={"broker_ticket": "8888"},
    )

    mock_observer = MagicMock()
    # Create service instance (simulating app restart)
    service = MT5ObservationService(
        observer=mock_observer,
        journal_mgr=memory_journal_mgr,
    )

    # Must have reconstructed ticket 8888 -> trade.trade_id
    assert 8888 in service._trade_position_map
    assert service._trade_position_map[8888] == trade.trade_id

    # If MT5 reports position 8888, it must NOT open a new trade
    mock_observer.get_open_positions.return_value = [
        MT5Position(
            ticket=8888,
            time=datetime.now(timezone.utc),
            type=ForexAction.LONG,
            magic=0,
            identifier=8888,
            reason=0,
            volume=0.5,
            price_open=1.2650,
            sl=1.2600,
            tp=1.2750,
            price_current=1.2660,
            swap=0.0,
            profit=50.0,
            symbol="GBPUSD",
            comment="",
        )
    ]
    mock_observer.get_pending_orders.return_value = []
    mock_observer.get_deals.return_value = []

    res = service.poll_once()
    assert res["events_count"] == 0  # No duplicate trade created
    assert len(memory_journal_mgr.journal.list_trades()) == 1


def test_mt5_service_background_thread_start_stop(memory_journal_mgr: ForexJournalManager):
    """Verify that start() and stop() manage worker thread cleanly."""
    mock_observer = MagicMock()
    mock_observer.get_open_positions.return_value = []
    mock_observer.get_pending_orders.return_value = []
    mock_observer.get_deals.return_value = []

    service = MT5ObservationService(
        observer=mock_observer,
        journal_mgr=memory_journal_mgr,
        poll_interval_seconds=0.1,
    )

    assert not service.is_running
    service.start()
    assert service.is_running

    time.sleep(0.25)
    assert mock_observer.get_open_positions.call_count >= 1

    service.stop(timeout=2.0)
    assert not service.is_running


def test_mt5_service_read_only_guarantee():
    """Verify that the service contains NO order execution methods."""
    for forbidden in ["order_send", "order_calc_margin", "order_calc_profit", "order_check"]:
        assert not hasattr(MT5ObservationService, forbidden)


def test_mt5_service_detects_pending_orders(memory_journal_mgr: ForexJournalManager):
    """Verify that new pending orders are detected and logged as audit events."""
    mock_observer = MagicMock()
    mock_observer.get_open_positions.return_value = []
    mock_observer.get_deals.return_value = []

    pending = MT5Order(
        ticket=77001,
        time_setup=datetime.now(timezone.utc),
        type="ORDER_TYPE_BUY_LIMIT",
        state="ORDER_STATE_PLACED",
        magic=0,
        symbol="EURUSD",
        volume_initial=0.5,
        volume_current=0.5,
        price_open=1.0820,
        sl=1.0790,
        tp=1.0900,
        comment="Pending limit",
    )
    mock_observer.get_pending_orders.return_value = [pending]

    service = MT5ObservationService(
        observer=mock_observer,
        journal_mgr=memory_journal_mgr,
    )

    res = service.poll_once()
    assert res["events_count"] == 1
    assert res["events"][0]["type"] == "NEW_ORDER"
    assert res["events"][0]["ticket"] == 77001

    # Re-polling should be idempotent
    res_repeat = service.poll_once()
    assert res_repeat["events_count"] == 0

