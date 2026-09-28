"""Integration Tests for Automated Closed-Trade Processing Pipeline (Phase 13).

Verifies that when MT5 observer detects a fully closed trade, the full
post-close calculation and reflection lifecycle is triggered automatically:
close journal trade -> load MT5 history -> PnL -> pips -> R -> MFE -> MAE
-> execution quality -> outcome classification -> reflection.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

from tradingagents.agents.schemas_forex import ForexAction
from tradingagents.database.journal import ForexTradeJournal
from tradingagents.database.models import ProposalRecord, TradeExitReason, TradeStatus
from tradingagents.dataflows.forex_data import ForexBar
from tradingagents.journal.manager import ForexJournalManager
from tradingagents.journal.post_close import (
    ClosedTradeProcessor,
    PostCloseProcessingStatus,
)
from tradingagents.learning.history_provider import InMemoryTradeHistoryProvider
from tradingagents.mt5.models import MT5Deal, MT5Position
from tradingagents.mt5.service import MT5ObservationService


def test_closed_trade_processor_full_pipeline(tmp_path: Path):
    """Verify end-to-end execution of the closed-trade pipeline across all phases."""
    db_file = tmp_path / "test_pipeline.db"
    journal = ForexTradeJournal(db_path=db_file)

    t_open = datetime(2025, 1, 15, 10, 0, tzinfo=timezone.utc)
    t_close = datetime(2025, 1, 15, 10, 10, tzinfo=timezone.utc)

    # 1. Record immutable proposal
    prop = ProposalRecord(
        proposal_id="prop_pipe_1",
        pair="EURUSD",
        action=ForexAction.LONG,
        entry_price=1.0800,
        stop_loss=1.0750,
        take_profit_1=1.0900,
        suggested_lot_size=1.0,
        created_at_utc=t_open.isoformat(),
    )
    journal.save_proposal(prop)

    # 2. Open trade
    trade = journal.record_trade_open(
        trade_id="trd_pipe_1",
        proposal_id="prop_pipe_1",
        pair="EURUSD",
        action=ForexAction.LONG,
        open_price=1.0802,
        stop_loss=1.0750,
        take_profit=1.0900,
        lots=1.0,
        open_time_utc=t_open.isoformat(),
    )
    # Close trade
    journal.record_trade_close(
        trade_id=trade.trade_id,
        close_price=1.0880,
        close_time_utc=t_close.isoformat(),
        exit_reason=TradeExitReason.TAKE_PROFIT,
    )

    # 3. Setup history bars during hold period
    bars = [
        ForexBar(timestamp=t_open + timedelta(minutes=2), open=1.0802, high=1.0840, low=1.0790, close=1.0830, volume=10.0),
        ForexBar(timestamp=t_open + timedelta(minutes=5), open=1.0830, high=1.0910, low=1.0825, close=1.0890, volume=15.0),
        ForexBar(timestamp=t_open + timedelta(minutes=8), open=1.0890, high=1.0895, low=1.0870, close=1.0880, volume=8.0),
    ]
    provider = InMemoryTradeHistoryProvider(candle_map={"EURUSD": bars})
    processor = ClosedTradeProcessor(journal=journal, history_provider=provider)

    assert processor.get_status("trd_pipe_1") == PostCloseProcessingStatus.PENDING

    # 4. Trigger automated processing
    res = processor.process_closed_trade("trd_pipe_1")

    assert res.status == PostCloseProcessingStatus.COMPLETED
    assert res.trade_id == "trd_pipe_1"
    assert res.pips == 78.0  # 1.0880 - 1.0802 = 78 pips
    assert res.r_multiple == round(78.0 / 52.0, 2)  # SL distance is 52 pips -> 1.5R
    assert res.mfe_r is not None and res.mfe_r > 0.0
    assert res.outcome_category in ("STANDARD_WIN", "PERFECT_EXIT")
    assert res.reflection_rating is not None

    # Verify journal trade was updated with reflection
    updated = journal.get_trade("trd_pipe_1")
    assert updated.reflection != ""
    assert updated.metadata.get("post_close_status") == "COMPLETED"
    assert updated.metadata.get("execution_quality") is not None
    assert updated.metadata.get("mfe_mae") is not None


def test_duplicate_reflection_prevention(tmp_path: Path):
    """Verify that calling process_closed_trade on a completed trade prevents duplicate reflection."""
    db_file = tmp_path / "test_dup.db"
    journal = ForexTradeJournal(db_path=db_file)
    t_open = datetime(2025, 1, 15, 10, 0, tzinfo=timezone.utc)
    t_close = datetime(2025, 1, 15, 10, 5, tzinfo=timezone.utc)

    open_rec = journal.record_trade_open(
        trade_id="trd_dup_1",
        pair="EURUSD",
        action=ForexAction.LONG,
        open_price=1.0800,
        stop_loss=1.0750,
        lots=1.0,
        open_time_utc=t_open.isoformat(),
    )
    journal.record_trade_close(
        trade_id=open_rec.trade_id,
        close_price=1.0850,
        close_time_utc=t_close.isoformat(),
    )

    provider = InMemoryTradeHistoryProvider(candle_map={"EURUSD": []})
    processor = ClosedTradeProcessor(journal=journal, history_provider=provider)

    res1 = processor.process_closed_trade("trd_dup_1")
    assert res1.status == PostCloseProcessingStatus.COMPLETED

    # Spy on learning_mgr.reflect_on_trade
    processor.learning_mgr.reflect_on_trade = MagicMock()

    # Second call without force
    res2 = processor.process_closed_trade("trd_dup_1", force=False)
    assert res2.status == PostCloseProcessingStatus.COMPLETED
    processor.learning_mgr.reflect_on_trade.assert_not_called()


def test_transient_failure_and_retry(tmp_path: Path):
    """Verify transient error records FAILED state and is cleanly retryable."""
    db_file = tmp_path / "test_retry.db"
    journal = ForexTradeJournal(db_path=db_file)
    t_open = datetime(2025, 1, 15, 10, 0, tzinfo=timezone.utc)
    t_close = datetime(2025, 1, 15, 10, 5, tzinfo=timezone.utc)

    open_rec = journal.record_trade_open(
        trade_id="trd_retry_1",
        pair="GBPUSD",
        action=ForexAction.SHORT,
        open_price=1.2500,
        stop_loss=1.2550,
        lots=1.0,
        open_time_utc=t_open.isoformat(),
    )
    journal.record_trade_close(
        trade_id=open_rec.trade_id,
        close_price=1.2450,
        close_time_utc=t_close.isoformat(),
    )

    failing_provider = MagicMock()
    failing_provider.get_history.side_effect = RuntimeError("MT5 transient socket timeout")

    processor = ClosedTradeProcessor(journal=journal, history_provider=failing_provider)

    # First attempt fails
    res_fail = processor.process_closed_trade("trd_retry_1")
    assert res_fail.status == PostCloseProcessingStatus.FAILED
    assert "socket timeout" in (res_fail.error_message or "")
    assert processor.get_status("trd_retry_1") == PostCloseProcessingStatus.FAILED

    # Second attempt with healthy provider succeeds
    healthy_provider = InMemoryTradeHistoryProvider(candle_map={"GBPUSD": []})
    processor.history_provider = healthy_provider
    processor.learning_mgr.history_provider = healthy_provider

    res_ok = processor.process_closed_trade("trd_retry_1")
    assert res_ok.status == PostCloseProcessingStatus.COMPLETED
    assert processor.get_status("trd_retry_1") == PostCloseProcessingStatus.COMPLETED


def test_automatic_orchestration_via_mt5_observation_service(tmp_path: Path):
    """Integration test proving automatic orchestration from MT5 detection to completed reflection."""
    db_file = tmp_path / "test_mt5_auto.db"
    journal = ForexTradeJournal(db_path=db_file)
    journal_mgr = ForexJournalManager(journal=journal)

    # 1. Open trade in journal linked to ticket 99991
    open_rec = journal.record_trade_open(
        trade_id="trd_auto_mt5",
        pair="EURUSD",
        action=ForexAction.LONG,
        open_price=1.0800,
        stop_loss=1.0750,
        take_profit=1.0900,
        lots=1.0,
        metadata={"broker_ticket": "99991"},
    )

    pos = MT5Position(
        ticket=99991,
        symbol="EURUSD",
        type=ForexAction.LONG,
        volume=1.0,
        price_open=1.0800,
        sl=1.0750,
        tp=1.0900,
        price_current=1.0850,
        profit=500.0,
        swap=0.0,
        time=datetime.now(timezone.utc),
    )

    mock_obs = MagicMock()
    mock_obs.is_connected = True
    # First poll: position is open
    mock_obs.get_open_positions.return_value = [pos]
    mock_obs.get_pending_orders.return_value = []
    mock_obs.get_deals.return_value = []

    history_provider = InMemoryTradeHistoryProvider(candle_map={"EURUSD": []})
    processor = ClosedTradeProcessor(
        journal=journal,
        history_provider=history_provider,
    )

    service = MT5ObservationService(
        observer=mock_obs,
        journal_mgr=journal_mgr,
        post_close_processor=processor,
    )

    # Poll 1: knows open position
    service.poll_once()
    assert 99991 in service._known_positions
    assert journal.get_trade(open_rec.trade_id).status == TradeStatus.OPEN

    # Now MT5 observer detects position is GONE (closed at 1.0890)
    mock_obs.get_open_positions.return_value = []
    close_deal = MT5Deal(
        ticket=88881,
        order=77771,
        position_id=99991,
        time=datetime.now(timezone.utc),
        type="SELL",
        entry="OUT",
        symbol="EURUSD",
        volume=1.0,
        price=1.0890,
        profit=900.0,
        commission=-4.0,
        swap=-1.0,
    )
    mock_obs.get_deals.return_value = [close_deal]

    # Poll 2: observer automatically detects closure and orchestrates full post-close pipeline
    service.poll_once()

    # Verify journal trade is closed
    trade_closed = journal.get_trade(open_rec.trade_id)
    assert trade_closed.status == TradeStatus.CLOSED
    assert trade_closed.close_price == 1.0890

    # Verify post-close pipeline automatically completed without user clicking Reflect
    status = processor.get_status(open_rec.trade_id)
    assert status == PostCloseProcessingStatus.COMPLETED
    assert trade_closed.reflection != ""
    assert trade_closed.metadata.get("post_close_status") == "COMPLETED"
