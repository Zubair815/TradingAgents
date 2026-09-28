"""Unit and integration test suite for SQLite Trade Journal & Proposal Storage (Phase 12).

Tests:
1. Migration runner and schema versioning idempotency.
2. Proposal persistent storage, retrieval, and status updates.
3. Bidirectional conversion between ForexTraderProposal and ProposalRecord.
4. Trade open and close lifecycle with deterministic pip, R-multiple, and PnL calculation.
5. Proposal-to-trade foreign key linking and automatic status transition.
6. Broker deal execution fill recording.
7. Strategy and prompt versioning metadata storage.
8. Performance analytics aggregation and pair performance reporting.
9. Post-trade reflection and tag updates.
10. In-memory and file-based database persistence.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tradingagents.agents.schemas_forex import (
    ForexAction,
    ForexRiskDecision,
    ForexRiskDecisionAction,
    ForexTraderProposal,
    OrderType,
    SetupType,
)
from tradingagents.database import (
    ForexTradeJournal,
    ProposalRecord,
    ProposalStatus,
    TradeExitReason,
    TradeStatus,
    get_current_schema_version,
    run_migrations,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def memory_journal() -> ForexTradeJournal:
    """Provide a fresh in-memory ForexTradeJournal instance."""
    journal = ForexTradeJournal(db_path=":memory:", auto_migrate=True)
    yield journal
    journal.close()


def make_test_proposal(pair: str = "EURUSD") -> ForexTraderProposal:
    return ForexTraderProposal(
        pair=pair,
        action=ForexAction.LONG,
        order_type=OrderType.MARKET,
        setup_type=SetupType.PULLBACK,
        timeframe="H1",
        entry_price=1.08500,
        stop_loss=1.08200,    # 30 pips
        take_profit_1=1.09100, # 60 pips (R:R 2.0)
        suggested_risk_percent=1.0,
        confluence_factors=["H1 Bullish EMA Stack", "London Session Overlap"],
        reasoning="Strong institutional pullback setup.",
        trade_rationale_summary="Long EURUSD on pullback.",
    )


# ---------------------------------------------------------------------------
# 1. Migration Runner & Schema Idempotency Tests
# ---------------------------------------------------------------------------


class TestDatabaseMigrations:
    def test_migrations_create_tables_and_track_version(self, tmp_path: Path):
        from tradingagents.database.migrations import MIGRATIONS

        db_file = tmp_path / "test_migration.db"

        # Initial run: applies legacy, research, and subsequent migrations
        applied = run_migrations(db_file)
        assert applied == len(MIGRATIONS)

        ver = get_current_schema_version(db_file)
        assert ver == len(MIGRATIONS)

        # Idempotency check: running again applies 0 migrations
        applied_again = run_migrations(db_file)
        assert applied_again == 0
        assert get_current_schema_version(db_file) == len(MIGRATIONS)

    def test_in_memory_migration(self):
        from tradingagents.database.migrations import MIGRATIONS

        journal = ForexTradeJournal(db_path=":memory:", auto_migrate=True)
        try:
            assert get_current_schema_version(journal._get_connection()) == len(MIGRATIONS)
        finally:
            journal.close()


# ---------------------------------------------------------------------------
# 2. Proposal Persistent Storage Tests
# ---------------------------------------------------------------------------


class TestProposalStorage:
    def test_save_and_retrieve_proposal(self, memory_journal: ForexTradeJournal):
        proposal = make_test_proposal("EURUSD")
        risk_decision = ForexRiskDecision(
            pair="EURUSD",
            decision=ForexRiskDecisionAction.APPROVE,
            original_action=ForexAction.LONG,
            approved_action=ForexAction.LONG,
            max_risk_percent=1.0,
            approved_lot_size=0.33,
            entry_price=1.08500,
            stop_loss=1.08200,
            take_profit=1.09100,
            risk_reward_ratio=2.0,
            executive_rationale="Approved by risk engine.",
        )

        proposal_id = memory_journal.save_proposal(
            proposal=proposal,
            risk_decision=risk_decision,
            status=ProposalStatus.APPROVED,
            metadata={"agent": "ForexTrader_v1"},
        )

        assert proposal_id.startswith("prop_")

        record = memory_journal.get_proposal(proposal_id)
        assert record is not None
        assert record.pair == "EURUSD"
        assert record.action == ForexAction.LONG
        assert record.entry_price == 1.08500
        assert record.stop_loss == 1.08200
        assert record.take_profit_1 == 1.09100
        assert record.sl_pips == 30.0
        assert record.tp_pips == 60.0
        assert record.status == ProposalStatus.APPROVED
        assert len(record.confluence_factors) == 2
        assert record.metadata.get("agent") == "ForexTrader_v1"
        assert record.risk_decision is not None
        assert record.risk_decision["decision"] == "APPROVE"

    def test_bidirectional_proposal_conversion(self, memory_journal: ForexTradeJournal):
        original = make_test_proposal("GBPJPY")
        proposal_id = memory_journal.save_proposal(original)

        record = memory_journal.get_proposal(proposal_id)
        assert record is not None

        converted = record.to_forex_trader_proposal()
        assert converted.pair == "GBPJPY"
        assert converted.action == original.action
        assert converted.entry_price == original.entry_price
        assert converted.stop_loss == original.stop_loss
        assert converted.take_profit_1 == original.take_profit_1

    def test_update_proposal_status(self, memory_journal: ForexTradeJournal):
        proposal = make_test_proposal()
        pid = memory_journal.save_proposal(proposal, status=ProposalStatus.PROPOSED)

        success = memory_journal.update_proposal_status(pid, ProposalStatus.REJECTED)
        assert success is True

        updated = memory_journal.get_proposal(pid)
        assert updated is not None
        assert updated.status == ProposalStatus.REJECTED

    def test_list_proposals_with_filters(self, memory_journal: ForexTradeJournal):
        memory_journal.save_proposal(make_test_proposal("EURUSD"), status=ProposalStatus.APPROVED)
        memory_journal.save_proposal(make_test_proposal("EURUSD"), status=ProposalStatus.REJECTED)
        memory_journal.save_proposal(make_test_proposal("GBPUSD"), status=ProposalStatus.APPROVED)

        eur_proposals = memory_journal.list_proposals(pair="EURUSD")
        assert len(eur_proposals) == 2

        approved_proposals = memory_journal.list_proposals(status=ProposalStatus.APPROVED)
        assert len(approved_proposals) == 2

        gbp_approved = memory_journal.list_proposals(pair="GBPUSD", status=ProposalStatus.APPROVED)
        assert len(gbp_approved) == 1

    def test_proposals_are_append_only_and_immutable(self, memory_journal: ForexTradeJournal):
        proposal = make_test_proposal("EURUSD")
        pid = memory_journal.save_proposal(proposal)

        # Attempting to re-save with identical proposal_id must raise IntegrityError
        existing = memory_journal.get_proposal(pid)
        assert existing is not None
        tampered_record = ProposalRecord.from_forex_trader_proposal(
            proposal=make_test_proposal("EURUSD"),
            status=ProposalStatus.APPROVED,
        )
        object.__setattr__(tampered_record, "proposal_id", pid)
        object.__setattr__(tampered_record, "entry_price", 999.99)

        with pytest.raises(sqlite3.IntegrityError):
            memory_journal.save_proposal(tampered_record)

        # Original proposal must remain untampered
        persisted = memory_journal.get_proposal(pid)
        assert persisted.entry_price == existing.entry_price


# ---------------------------------------------------------------------------
# 3. Trade Open & Close Lifecycle Tests
# ---------------------------------------------------------------------------


class TestTradeLifecycle:
    def test_long_winning_trade_lifecycle(self, memory_journal: ForexTradeJournal):
        # Open LONG EURUSD: 1.08500, SL 1.08200 (30 pips risk), 0.50 lots, commission $3.50
        trade = memory_journal.record_trade_open(
            pair="EURUSD",
            action=ForexAction.LONG,
            open_price=1.08500,
            stop_loss=1.08200,
            take_profit=1.09100,
            lots=0.50,
            commission=3.50,
            tags=["Breakout", "London"],
            notes="Opening breakout long",
        )

        assert trade.status == TradeStatus.OPEN
        assert trade.trade_id.startswith("trd_")

        open_trades = memory_journal.list_trades(status=TradeStatus.OPEN)
        assert len(open_trades) == 1
        assert open_trades[0].trade_id == trade.trade_id

        # Close trade at 1.09100 (60 pips profit, 2.0R)
        closed_trade = memory_journal.record_trade_close(
            trade_id=trade.trade_id,
            close_price=1.09100,
            exit_reason=TradeExitReason.TAKE_PROFIT,
            swap=1.20,
            reflection="Execution followed plan with clean TP hit.",
        )

        assert closed_trade.status == TradeStatus.CLOSED
        assert closed_trade.pips_gained == 60.0
        assert closed_trade.r_multiple == 2.0
        # 60 pips * $10/pip * 0.50 lots = $300.00 gross profit
        assert closed_trade.gross_profit == 300.00
        # Net profit = $300.00 - $3.50 + $1.20 = $297.70
        assert closed_trade.net_profit == 297.70
        assert closed_trade.exit_reason == TradeExitReason.TAKE_PROFIT
        assert closed_trade.is_winner is True

    def test_short_losing_trade_lifecycle(self, memory_journal: ForexTradeJournal):
        # Open SHORT GBPUSD: 1.27000, SL 1.27300 (30 pips risk), 1.00 lot
        trade = memory_journal.record_trade_open(
            pair="GBPUSD",
            action=ForexAction.SHORT,
            open_price=1.27000,
            stop_loss=1.27300,
            lots=1.00,
            commission=5.00,
        )

        # Stopped out at 1.27300 (-30 pips, -1.0R)
        closed = memory_journal.record_trade_close(
            trade_id=trade.trade_id,
            close_price=1.27300,
            exit_reason=TradeExitReason.STOP_LOSS,
        )

        assert closed.status == TradeStatus.CLOSED
        assert closed.pips_gained == -30.0
        assert closed.r_multiple == -1.0
        assert closed.gross_profit == -300.00
        assert closed.net_profit == -305.00
        assert closed.exit_reason == TradeExitReason.STOP_LOSS
        assert closed.is_winner is False

    def test_proposal_linking_marks_executed(self, memory_journal: ForexTradeJournal):
        proposal = make_test_proposal("EURUSD")
        pid = memory_journal.save_proposal(proposal, status=ProposalStatus.APPROVED)

        # Open trade linked to proposal
        trade = memory_journal.record_trade_open(
            pair="EURUSD",
            action=ForexAction.LONG,
            open_price=1.08500,
            stop_loss=1.08200,
            lots=0.50,
            proposal_id=pid,
        )

        assert trade.proposal_id == pid

        # Check proposal was automatically transitioned to EXECUTED
        updated_proposal = memory_journal.get_proposal(pid)
        assert updated_proposal is not None
        assert updated_proposal.status == ProposalStatus.EXECUTED


# ---------------------------------------------------------------------------
# 4. Order Execution Fills Tests
# ---------------------------------------------------------------------------


class TestOrderExecutionFills:
    def test_record_and_list_executions(self, memory_journal: ForexTradeJournal):
        trade = memory_journal.record_trade_open(
            pair="EURUSD",
            action=ForexAction.LONG,
            open_price=1.08500,
            stop_loss=1.08200,
            lots=0.50,
        )

        exec1 = memory_journal.record_execution(
            deal_id="deal_1001",
            trade_id=trade.trade_id,
            pair="EURUSD",
            order_type="BUY",
            volume=0.50,
            price=1.08502,
            slippage_pips=0.2,
            spread_at_open_pips=1.1,
        )

        assert exec1.deal_id == "deal_1001"
        assert exec1.slippage_pips == 0.2

        deals = memory_journal.list_executions_for_trade(trade.trade_id)
        assert len(deals) == 1
        assert deals[0].deal_id == "deal_1001"
        assert deals[0].price == 1.08502


# ---------------------------------------------------------------------------
# 5. Strategy Versioning Tests
# ---------------------------------------------------------------------------


class TestStrategyVersioning:
    def test_record_and_get_strategy_version(self, memory_journal: ForexTradeJournal):
        rec = memory_journal.record_strategy_version(
            strategy_name="Forex_ICT_Pullback_v1",
            prompt_hash="sha256_abcdef123456",
            model_name="claude-3-5-sonnet",
            parameters={"timeframe": "H1", "risk_percent": 1.0, "atr_filter": True},
        )

        assert rec.version_id.startswith("v_")
        assert rec.strategy_name == "Forex_ICT_Pullback_v1"

        retrieved = memory_journal.get_strategy_version(rec.version_id)
        assert retrieved is not None
        assert retrieved.prompt_hash == "sha256_abcdef123456"
        assert retrieved.parameters["timeframe"] == "H1"


# ---------------------------------------------------------------------------
# 6. Performance Analytics & Journal Summary Tests
# ---------------------------------------------------------------------------


class TestPerformanceAnalytics:
    def test_journal_summary_metrics(self, memory_journal: ForexTradeJournal):
        # Empty summary
        empty_sum = memory_journal.get_journal_summary()
        assert empty_sum["total_trades"] == 0
        assert empty_sum["win_rate"] == 0.0

        # Trade 1: Win (+2.0R, +$200)
        t1 = memory_journal.record_trade_open("EURUSD", ForexAction.LONG, 1.0850, 1.0825, 0.5)
        memory_journal.record_trade_close(t1.trade_id, 1.0900)

        # Trade 2: Win (+1.5R, +$150)
        t2 = memory_journal.record_trade_open("GBPUSD", ForexAction.LONG, 1.2700, 1.2675, 0.5)
        memory_journal.record_trade_close(t2.trade_id, 1.27375)

        # Trade 3: Loss (-1.0R, -$100)
        t3 = memory_journal.record_trade_open("EURUSD", ForexAction.LONG, 1.0850, 1.0825, 0.5)
        memory_journal.record_trade_close(t3.trade_id, 1.0825)

        # Trade 4: Open trade (not yet closed)
        memory_journal.record_trade_open("USDJPY", ForexAction.SHORT, 155.00, 156.00, 0.5)

        summary = memory_journal.get_journal_summary()
        assert summary["total_trades"] == 3
        assert summary["open_trades"] == 1
        assert summary["winning_trades"] == 2
        assert summary["losing_trades"] == 1
        assert summary["win_rate"] == 66.7
        assert summary["total_net_profit"] > 0
        assert summary["profit_factor"] > 1.0
        assert summary["average_r_multiple"] > 0

    def test_pair_performance(self, memory_journal: ForexTradeJournal):
        t1 = memory_journal.record_trade_open("EURUSD", ForexAction.LONG, 1.0850, 1.0825, 0.5)
        memory_journal.record_trade_close(t1.trade_id, 1.0900)

        t2 = memory_journal.record_trade_open("EURUSD", ForexAction.LONG, 1.0850, 1.0825, 0.5)
        memory_journal.record_trade_close(t2.trade_id, 1.0825)

        perf = memory_journal.get_pair_performance("EURUSD")
        assert perf["pair"] == "EURUSD"
        assert perf["trades"] == 2
        assert perf["winning_trades"] == 1
        assert perf["win_rate"] == 50.0


# ---------------------------------------------------------------------------
# 7. Post-Trade Reflection & Tagging Tests
# ---------------------------------------------------------------------------


class TestReflectionAndTagging:
    def test_update_trade_reflection(self, memory_journal: ForexTradeJournal):
        trade = memory_journal.record_trade_open("EURUSD", ForexAction.LONG, 1.0850, 1.0820, 0.5)
        memory_journal.record_trade_close(trade.trade_id, 1.0910)

        success = memory_journal.update_trade_reflection(
            trade_id=trade.trade_id,
            reflection="Excellent discipline holding to target through NY session noise.",
            tags=["Discipline", "A+ Setup", "London Continuation"],
            notes="Follow-up: Check 15m order block next time.",
        )
        assert success is True

        updated = memory_journal.get_trade(trade.trade_id)
        assert updated is not None
        assert "Excellent discipline" in updated.reflection
        assert "A+ Setup" in updated.tags
        assert "Follow-up" in updated.notes


# ---------------------------------------------------------------------------
# 8. File-Based SQLite Database Persistence Tests
# ---------------------------------------------------------------------------


class TestFilePersistence:
    def test_persistent_storage_across_instances(self, tmp_path: Path):
        db_file = tmp_path / "forex_trade_journal.db"

        # Instance 1: write proposal and open trade
        with ForexTradeJournal(db_path=db_file) as journal1:
            proposal = make_test_proposal("EURUSD")
            pid = journal1.save_proposal(proposal)
            trade = journal1.record_trade_open(
                pair="EURUSD",
                action=ForexAction.LONG,
                open_price=1.08500,
                stop_loss=1.08200,
                lots=0.50,
                proposal_id=pid,
            )
            tid = trade.trade_id

        # Instance 2: reopen file and close trade
        with ForexTradeJournal(db_path=db_file) as journal2:
            prop = journal2.get_proposal(pid)
            assert prop is not None
            assert prop.pair == "EURUSD"

            trade = journal2.get_trade(tid)
            assert trade is not None
            assert trade.status == TradeStatus.OPEN

            closed = journal2.record_trade_close(tid, 1.09000)
            assert closed.status == TradeStatus.CLOSED
            assert closed.pips_gained == 50.0

        # Instance 3: verify state is fully persisted
        with ForexTradeJournal(db_path=db_file) as journal3:
            trade_final = journal3.get_trade(tid)
            assert trade_final is not None
            assert trade_final.status == TradeStatus.CLOSED
            assert trade_final.net_profit is not None
            assert trade_final.net_profit > 0
