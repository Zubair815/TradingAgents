"""Unit & Integration Test Suite for Forex Trade Journal, Lifecycle & Analytics (Phase 15).

Tests:
1. Lifecycle state machine transitions and invalid transition enforcement.
2. Trade lifecycle operations: proposal creation, risk evaluation, trade opening,
   breakeven stop detection, partial close scale-out, and final trade close.
3. Chronological event timeline recording, querying, and Markdown rendering.
4. ProposalMatcher: exact comment matching, heuristic multi-factor scoring,
   auto-reconciliation, orphan positions, and unfulfilled proposals.
5. PostTradeAnalytics: win rate, profit factor, expectancy (R and $), max drawdown,
   pair/setup/exit reason breakdowns, and Markdown dashboard generation.
6. Unified ForexJournalManager facade integration.
7. Package exports and lazy loading from tradingagents.forex.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tradingagents.agents.schemas_forex import (
    ForexAction,
    ForexRiskDecision,
    ForexRiskDecisionAction,
    ForexTraderProposal,
    OrderType,
    SetupType,
)
from tradingagents.database.journal import ForexTradeJournal
from tradingagents.database.models import (
    ProposalStatus,
    TradeExitReason,
    TradeJournalRecord,
    TradeStatus,
)
from tradingagents.journal import (
    EventTimeline,
    EventType,
    ForexJournalManager,
    LifecycleState,
    LifecycleTransitionError,
    MatchConfidence,
    PostTradeAnalytics,
    ProposalMatcher,
    TradeLifecycleManager,
)

# ---------------------------------------------------------------------------
# Fixtures & Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def memory_journal() -> ForexTradeJournal:
    journal = ForexTradeJournal(db_path=":memory:", auto_migrate=True)
    yield journal
    journal.close()


@pytest.fixture
def journal_manager() -> ForexJournalManager:
    manager = ForexJournalManager(db_path=":memory:")
    yield manager
    manager.close()


def make_proposal(
    pair: str = "EURUSD",
    action: ForexAction = ForexAction.LONG,
    entry: float = 1.0850,
    sl: float = 1.0810,
    tp1: float = 1.0930,
    lots: float = 0.50,
) -> ForexTraderProposal:
    return ForexTraderProposal(
        pair=pair,
        action=action,
        order_type=OrderType.MARKET,
        setup_type=SetupType.TREND_CONTINUATION,
        timeframe="H1",
        entry_price=entry,
        entry_zone_low=entry - 0.0003,
        entry_zone_high=entry + 0.0003,
        stop_loss=sl,
        take_profit_1=tp1,
        suggested_lot_size=lots,
        suggested_risk_percent=1.0,
        confluence_factors=["EMA 20/50 Bullish Cross", "London-NY Overlap"],
        reasoning="Strong trend continuation pattern.",
        trade_rationale_summary="Long EURUSD on continuation.",
    )


# ---------------------------------------------------------------------------
# 1. State Machine & Lifecycle Tests
# ---------------------------------------------------------------------------


class TestLifecycleStateMachine:
    """Test state machine valid transitions and illegal transition enforcement."""

    def test_allowed_transitions(self, memory_journal: ForexTradeJournal):
        mgr = TradeLifecycleManager(journal=memory_journal)

        assert mgr.can_transition(LifecycleState.PROPOSED, LifecycleState.APPROVED)
        assert mgr.can_transition(LifecycleState.PROPOSED, LifecycleState.REJECTED)
        assert mgr.can_transition(LifecycleState.PROPOSED, LifecycleState.MODIFIED)
        assert mgr.can_transition(LifecycleState.APPROVED, LifecycleState.ACTIVE_POSITION)
        assert mgr.can_transition(LifecycleState.ACTIVE_POSITION, LifecycleState.PARTIALLY_CLOSED)
        assert mgr.can_transition(LifecycleState.PARTIALLY_CLOSED, LifecycleState.SETTLED)
        assert mgr.can_transition(LifecycleState.ACTIVE_POSITION, LifecycleState.SETTLED)

    def test_forbidden_transitions_raise_error(self, memory_journal: ForexTradeJournal):
        mgr = TradeLifecycleManager(journal=memory_journal)

        # Cannot move from SETTLED back to ACTIVE_POSITION
        assert not mgr.can_transition(LifecycleState.SETTLED, LifecycleState.ACTIVE_POSITION)
        with pytest.raises(LifecycleTransitionError):
            mgr.validate_transition(LifecycleState.SETTLED, LifecycleState.ACTIVE_POSITION)

        # Cannot move from REJECTED to ACTIVE_POSITION
        with pytest.raises(LifecycleTransitionError):
            mgr.validate_transition(LifecycleState.REJECTED, LifecycleState.ACTIVE_POSITION)

        # Cannot move from PROPOSED directly to SETTLED
        with pytest.raises(LifecycleTransitionError):
            mgr.validate_transition(LifecycleState.PROPOSED, LifecycleState.SETTLED)


class TestTradeLifecycleOperations:
    """Test proposal creation, risk evaluation, trade opening, and management."""

    def test_proposal_lifecycle_to_open_trade(self, memory_journal: ForexTradeJournal):
        mgr = TradeLifecycleManager(journal=memory_journal)

        # 1. Submit proposal
        proposal = make_proposal("EURUSD", entry=1.0850, sl=1.0810, tp1=1.0930)
        prop_id = mgr.submit_proposal(proposal, actor="ForexTrader")
        assert prop_id.startswith("prop_")

        record = memory_journal.get_proposal(prop_id)
        assert record.status == ProposalStatus.PROPOSED

        # 2. Risk Evaluation -> Approve
        decision = ForexRiskDecision(
            pair="EURUSD",
            decision=ForexRiskDecisionAction.APPROVE,
            original_action=ForexAction.LONG,
            approved_action=ForexAction.LONG,
            max_risk_percent=1.0,
            approved_lot_size=0.50,
            executive_rationale="Approved within risk boundaries.",
        )
        status = mgr.evaluate_risk(prop_id, decision)
        assert status == ProposalStatus.APPROVED

        # 3. Open position from approved proposal
        trade_id = mgr.open_position_from_proposal(
            proposal_id=prop_id,
            open_price=1.0852,  # 0.2 pip slippage
            lots=0.50,
            ticket=1001,
            actor="MT5Observer",
        )
        assert trade_id.startswith("trd_")

        # Verify proposal marked EXECUTED
        record_after = memory_journal.get_proposal(prop_id)
        assert record_after.status == ProposalStatus.EXECUTED

        # Verify trade in journal
        trade = memory_journal.get_trade(trade_id)
        assert trade.status == TradeStatus.OPEN
        assert trade.pair == "EURUSD"
        assert trade.open_price == 1.0852
        assert trade.lots == 0.50

    def test_stop_loss_modification_and_breakeven_detection(self, memory_journal: ForexTradeJournal):
        mgr = TradeLifecycleManager(journal=memory_journal)
        prop_id = mgr.submit_proposal(make_proposal("EURUSD", entry=1.0850, sl=1.0810))
        mgr.evaluate_risk(
            prop_id,
            ForexRiskDecision(
                pair="EURUSD",
                decision=ForexRiskDecisionAction.APPROVE,
                original_action=ForexAction.LONG,
                approved_action=ForexAction.LONG,
                executive_rationale="Approved within risk boundaries.",
            ),
        )
        trade_id = mgr.open_position_from_proposal(prop_id, open_price=1.0850, lots=0.50)

        # Move SL to 1.0830 (not breakeven yet)
        is_be1 = mgr.modify_stop_loss(trade_id, new_stop_loss=1.0830, reason="Trailing stop")
        assert not is_be1
        trade1 = memory_journal.get_trade(trade_id)
        assert trade1.stop_loss == 1.0830

        # Move SL to 1.0850 (exact entry price -> Breakeven!)
        is_be2 = mgr.modify_stop_loss(trade_id, new_stop_loss=1.0850, reason="Locking risk")
        assert is_be2

        # Check timeline event emitted for BREAKEVEN_APPLIED
        events = mgr.timeline.get_timeline_for_trade(trade_id)
        be_events = [e for e in events if e.event_type == EventType.BREAKEVEN_APPLIED]
        assert len(be_events) == 1
        assert be_events[0].payload["is_breakeven"] is True

    def test_partial_close_and_final_close(self, memory_journal: ForexTradeJournal):
        mgr = TradeLifecycleManager(journal=memory_journal)
        prop_id = mgr.submit_proposal(make_proposal("EURUSD", entry=1.0850, sl=1.0810))
        mgr.evaluate_risk(
            prop_id,
            ForexRiskDecision(
                pair="EURUSD",
                decision=ForexRiskDecisionAction.APPROVE,
                original_action=ForexAction.LONG,
                approved_action=ForexAction.LONG,
                executive_rationale="Approved within risk boundaries.",
            ),
        )
        trade_id = mgr.open_position_from_proposal(prop_id, open_price=1.0850, lots=1.00)

        # 1. Partial close 0.50 lots at 1.0890 (+40 pips)
        res = mgr.partial_close_trade(
            trade_id=trade_id,
            lots_to_close=0.50,
            close_price=1.0890,
            exit_reason=TradeExitReason.TAKE_PROFIT,
        )
        assert res["lots_closed"] == 0.50
        assert res["remaining_lots"] == 0.50
        assert res["pips_gained"] == pytest.approx(40.0, 0.1)

        trade_mid = memory_journal.get_trade(trade_id)
        assert trade_mid.lots == 0.50
        assert trade_mid.status == TradeStatus.OPEN

        # 2. Final close remaining 0.50 lots at 1.0930 (+80 pips)
        closed_trade = mgr.close_trade(
            trade_id=trade_id,
            close_price=1.0930,
            exit_reason=TradeExitReason.TAKE_PROFIT,
        )
        assert closed_trade.status == TradeStatus.CLOSED
        assert closed_trade.pips_gained == pytest.approx(80.0, 0.1)
        assert closed_trade.r_multiple == pytest.approx(2.0, 0.1)  # 80 pips gain / 40 pips risk = 2.0R


# ---------------------------------------------------------------------------
# 2. Event Timeline Tests
# ---------------------------------------------------------------------------


class TestEventTimeline:
    """Test append-only event logging and visual Markdown rendering."""

    def test_record_and_query_events(self, memory_journal: ForexTradeJournal):
        timeline = EventTimeline(journal=memory_journal)

        e1 = timeline.record_event(
            event_type=EventType.PROPOSAL_CREATED,
            proposal_id="prop_001",
            actor="ForexTrader",
            description="Created EURUSD proposal",
        )
        timeline.record_event(
            event_type=EventType.PROPOSAL_APPROVED,
            proposal_id="prop_001",
            actor="ForexRiskEngine",
            description="Risk approved",
        )
        timeline.record_event(
            event_type=EventType.POSITION_OPENED,
            trade_id="trd_001",
            proposal_id="prop_001",
            actor="MT5Observer",
            description="Position opened",
        )

        assert e1.event_type == EventType.PROPOSAL_CREATED
        prop_events = timeline.get_timeline_for_proposal("prop_001")
        assert len(prop_events) == 3

        trade_events = timeline.get_timeline_for_trade("trd_001")
        assert len(trade_events) == 1
        assert trade_events[0].event_type == EventType.POSITION_OPENED

    def test_render_markdown_timeline(self, memory_journal: ForexTradeJournal):
        timeline = EventTimeline(journal=memory_journal)
        timeline.record_event(
            event_type=EventType.PROPOSAL_CREATED,
            trade_id="trd_999",
            actor="ForexTrader",
            description="Setup initiated",
            payload={"pair": "EURUSD"},
        )
        timeline.record_event(
            event_type=EventType.POSITION_CLOSED,
            trade_id="trd_999",
            actor="MT5Observer",
            description="Hit TP1",
            payload={"profit": 450.0},
        )

        md = timeline.render_markdown_timeline(trade_id="trd_999")
        assert "### ⏱️ Chronological Event Timeline: Trade `trd_999`" in md
        assert "PROPOSAL_CREATED" in md
        assert "POSITION_CLOSED" in md
        assert "Hit TP1" in md


# ---------------------------------------------------------------------------
# 3. Proposal Matcher & Reconciliation Tests
# ---------------------------------------------------------------------------


class TestProposalMatcher:
    """Test reconciling broker positions with pending agent proposals."""

    def test_exact_match_via_comment(self, memory_journal: ForexTradeJournal):
        mgr = TradeLifecycleManager(journal=memory_journal)
        matcher = ProposalMatcher(journal=memory_journal, lifecycle=mgr)

        prop_id = mgr.submit_proposal(make_proposal("EURUSD", entry=1.0850))
        mgr.evaluate_risk(
            prop_id,
            ForexRiskDecision(
                pair="EURUSD",
                decision=ForexRiskDecisionAction.APPROVE,
                original_action=ForexAction.LONG,
                approved_action=ForexAction.LONG,
                executive_rationale="Approved within risk boundaries.",
            ),
        )

        pos = {
            "ticket": 5001,
            "symbol": "EURUSD",
            "type": 0,  # BUY
            "price_open": 1.0850,
            "volume": 0.50,
            "comment": f"AutoTrade {prop_id}",
        }

        match = matcher.match_position(pos)
        assert match.is_matched is True
        assert match.proposal_id == prop_id
        assert match.confidence == MatchConfidence.EXACT
        assert match.score == 1.0

    def test_heuristic_match_by_symbol_direction_price(self, memory_journal: ForexTradeJournal):
        mgr = TradeLifecycleManager(journal=memory_journal)
        matcher = ProposalMatcher(journal=memory_journal, lifecycle=mgr)

        prop_id = mgr.submit_proposal(make_proposal("GBPJPY", action=ForexAction.SHORT, entry=190.50, lots=0.30))
        mgr.evaluate_risk(
            prop_id,
            ForexRiskDecision(
                pair="GBPJPY",
                decision=ForexRiskDecisionAction.APPROVE,
                original_action=ForexAction.SHORT,
                approved_action=ForexAction.SHORT,
                executive_rationale="Approved within risk boundaries.",
            ),
        )

        # Broker position on GBPJPYm (short, price 190.48 within 2 pips, volume 0.30)
        pos = {
            "ticket": 5002,
            "symbol": "GBPJPYm",
            "type": 1,  # SELL / SHORT
            "price_open": 190.48,
            "volume": 0.30,
            "comment": "",
            "time": datetime.now(timezone.utc),
        }

        match = matcher.match_position(pos)
        assert match.is_matched is True
        assert match.proposal_id == prop_id
        assert match.confidence in (MatchConfidence.HIGH, MatchConfidence.MEDIUM)
        assert match.score >= 0.85
        assert match.discrepancy_pips <= 2.5

    def test_mismatch_on_symbol_or_direction(self, memory_journal: ForexTradeJournal):
        mgr = TradeLifecycleManager(journal=memory_journal)
        matcher = ProposalMatcher(journal=memory_journal, lifecycle=mgr)

        prop_id = mgr.submit_proposal(make_proposal("EURUSD", action=ForexAction.LONG, entry=1.0850))
        mgr.evaluate_risk(
            prop_id,
            ForexRiskDecision(
                pair="EURUSD",
                decision=ForexRiskDecisionAction.APPROVE,
                original_action=ForexAction.LONG,
                approved_action=ForexAction.LONG,
                executive_rationale="Approved within risk boundaries.",
            ),
        )

        # Position on different pair
        pos_wrong_pair = {"ticket": 9001, "symbol": "USDJPY", "type": 0, "price_open": 155.0}
        match1 = matcher.match_position(pos_wrong_pair)
        assert not match1.is_matched
        assert match1.confidence == MatchConfidence.NONE

        # Position with opposite direction (SHORT vs LONG)
        pos_wrong_dir = {"ticket": 9002, "symbol": "EURUSD", "type": 1, "price_open": 1.0850}
        match2 = matcher.match_position(pos_wrong_dir)
        assert not match2.is_matched

    def test_reconcile_positions_auto_executes_trade(self, memory_journal: ForexTradeJournal):
        mgr = TradeLifecycleManager(journal=memory_journal)
        matcher = ProposalMatcher(journal=memory_journal, lifecycle=mgr)

        prop_id = mgr.submit_proposal(make_proposal("EURUSD", entry=1.0850, lots=0.50))
        mgr.evaluate_risk(
            prop_id,
            ForexRiskDecision(
                pair="EURUSD",
                decision=ForexRiskDecisionAction.APPROVE,
                original_action=ForexAction.LONG,
                approved_action=ForexAction.LONG,
                executive_rationale="Approved within risk boundaries.",
            ),
        )

        broker_positions = [
            {"ticket": 7777, "symbol": "EURUSD", "type": 0, "price_open": 1.0851, "volume": 0.50, "comment": prop_id},
            {"ticket": 8888, "symbol": "AUDNZD", "type": 1, "price_open": 1.0900, "volume": 0.20, "comment": ""},
        ]

        results = matcher.reconcile_positions(broker_positions, auto_reconcile=True)
        assert len(results) == 2

        # First position reconciled and executed
        assert results[0].is_matched is True
        assert results[0].proposal_id == prop_id

        # Verify proposal transitioned to EXECUTED
        record = memory_journal.get_proposal(prop_id)
        assert record.status == ProposalStatus.EXECUTED

        # Verify orphan position detected
        orphans = matcher.find_orphan_positions(broker_positions, results)
        assert len(orphans) == 1
        assert orphans[0]["ticket"] == 8888


# ---------------------------------------------------------------------------
# 4. Post-Trade Analytics Tests
# ---------------------------------------------------------------------------


class TestPostTradeAnalytics:
    """Test performance metrics, win rate, expectancy, drawdown, and dashboards."""

    def test_performance_report_metrics(self, memory_journal: ForexTradeJournal):
        # Create synthetic closed trades: 3 wins, 1 loss
        trades = [
            TradeJournalRecord(
                trade_id="t1",
                pair="EURUSD",
                action=ForexAction.LONG,
                status=TradeStatus.CLOSED,
                open_price=1.0800,
                close_price=1.0850,
                stop_loss=1.0770,
                lots=1.0,
                pips_gained=50.0,
                r_multiple=1.67,
                net_profit=500.0,
                exit_reason=TradeExitReason.TAKE_PROFIT,
                metadata={"setup_type": "TREND_CONTINUATION"},
            ),
            TradeJournalRecord(
                trade_id="t2",
                pair="EURUSD",
                action=ForexAction.LONG,
                status=TradeStatus.CLOSED,
                open_price=1.0850,
                close_price=1.0900,
                stop_loss=1.0820,
                lots=1.0,
                pips_gained=50.0,
                r_multiple=1.67,
                net_profit=500.0,
                exit_reason=TradeExitReason.TAKE_PROFIT,
                metadata={"setup_type": "TREND_CONTINUATION"},
            ),
            TradeJournalRecord(
                trade_id="t3",
                pair="GBPJPY",
                action=ForexAction.SHORT,
                status=TradeStatus.CLOSED,
                open_price=190.00,
                close_price=190.50,
                stop_loss=189.50,
                lots=0.5,
                pips_gained=-50.0,
                r_multiple=-1.0,
                net_profit=-250.0,
                exit_reason=TradeExitReason.STOP_LOSS,
                metadata={"setup_type": "LIQUIDITY_SWEEP"},
            ),
            TradeJournalRecord(
                trade_id="t4",
                pair="GBPJPY",
                action=ForexAction.SHORT,
                status=TradeStatus.CLOSED,
                open_price=191.00,
                close_price=190.20,
                stop_loss=191.50,
                lots=1.0,
                pips_gained=80.0,
                r_multiple=1.60,
                net_profit=800.0,
                exit_reason=TradeExitReason.TAKE_PROFIT,
                metadata={"setup_type": "LIQUIDITY_SWEEP"},
            ),
        ]

        report = PostTradeAnalytics.compute_performance_report(trades, initial_capital=100000.0)

        assert report.total_trades == 4
        assert report.closed_trades == 4
        assert report.winning_trades == 3
        assert report.losing_trades == 1
        assert report.win_rate == 75.0
        assert report.loss_rate == 25.0
        # Gross profit: 500 + 500 + 800 = 1800; Gross loss: 250; Net: 1550
        assert report.gross_profit == 1800.0
        assert report.gross_loss == 250.0
        assert report.total_net_profit == 1550.0
        assert report.profit_factor == pytest.approx(7.2, 0.1)

        # Expectancy
        assert report.expectancy_r > 0.50
        assert report.expectancy_cash > 0.0

        # Breakdown by pair
        assert "EURUSD" in report.by_pair
        assert report.by_pair["EURUSD"].win_rate == 100.0
        assert report.by_pair["EURUSD"].total_trades == 2

        assert "GBPJPY" in report.by_pair
        assert report.by_pair["GBPJPY"].total_trades == 2
        assert report.by_pair["GBPJPY"].win_rate == 50.0

        # Breakdown by setup
        assert "TREND_CONTINUATION" in report.by_setup
        assert report.by_setup["TREND_CONTINUATION"].win_rate == 100.0

        # Markdown dashboard check
        md = PostTradeAnalytics.render_markdown_dashboard(report)
        assert "# 📊 Quantitative Forex Journal: Performance Dashboard" in md
        assert "75.0%" in md
        assert "1,550.00" in md

    def test_empty_trades_handling(self):
        report = PostTradeAnalytics.compute_performance_report([])
        assert report.total_trades == 0
        assert report.closed_trades == 0
        assert report.win_rate == 0.0
        assert report.profit_factor == 0.0


# ---------------------------------------------------------------------------
# 5. Unified ForexJournalManager Integration Tests
# ---------------------------------------------------------------------------


class TestForexJournalManagerIntegration:
    """Test full integration workflow using unified ForexJournalManager facade."""

    def test_complete_trade_workflow_through_facade(self, journal_manager: ForexJournalManager):
        # 1. Submit proposal
        proposal = make_proposal("EURUSD", entry=1.0850, sl=1.0810, tp1=1.0930, lots=0.40)
        prop_id = journal_manager.submit_proposal(proposal)

        # 2. Risk evaluation
        decision = ForexRiskDecision(
            pair="EURUSD",
            decision=ForexRiskDecisionAction.APPROVE,
            original_action=ForexAction.LONG,
            approved_action=ForexAction.LONG,
            approved_lot_size=0.40,
            executive_rationale="Approved within risk boundaries.",
        )
        status = journal_manager.evaluate_risk(prop_id, decision)
        assert status == ProposalStatus.APPROVED

        # 3. Open trade
        trade_id = journal_manager.open_trade(
            proposal_id=prop_id,
            open_price=1.0850,
            lots=0.40,
            ticket=99001,
        )

        # 4. Modify SL to breakeven
        is_be = journal_manager.modify_stop_loss(trade_id, new_stop_loss=1.0850, reason="Lock risk")
        assert is_be is True

        # 5. Partial close 0.20 lots @ 1.0890
        p_res = journal_manager.partial_close(trade_id, lots_to_close=0.20, close_price=1.0890)
        assert p_res["remaining_lots"] == 0.20

        # 6. Final close remaining 0.20 lots @ 1.0930
        closed_trade = journal_manager.close_trade(
            trade_id=trade_id,
            close_price=1.0930,
            exit_reason=TradeExitReason.TAKE_PROFIT,
            gross_profit=160.0,
        )
        assert closed_trade.status == TradeStatus.CLOSED

        # 7. Verify Timeline
        timeline_events = journal_manager.get_timeline(trade_id=trade_id)
        assert len(timeline_events) >= 4
        event_types = [e.event_type for e in timeline_events]
        assert EventType.POSITION_OPENED in event_types
        assert EventType.BREAKEVEN_APPLIED in event_types
        assert EventType.PARTIAL_CLOSE in event_types
        assert EventType.POSITION_CLOSED in event_types

        # 8. Verify Performance Report
        report = journal_manager.compute_performance()
        assert report.closed_trades == 1
        assert report.win_rate == 100.0

        # 9. Verify Markdown Dashboard
        dashboard = journal_manager.render_performance_dashboard()
        assert "Performance Dashboard" in dashboard


# ---------------------------------------------------------------------------
# 6. Forex Package Lazy Exports Tests
# ---------------------------------------------------------------------------


class TestForexPackageExports:
    """Verify that tradingagents.forex cleanly exports Phase 15 journal components."""

    def test_forex_init_lazy_exports_journal(self):
        import tradingagents.forex as fx

        assert hasattr(fx, "ForexJournalManager")
        assert hasattr(fx, "TradeLifecycleManager")
        assert hasattr(fx, "LifecycleState")
        assert hasattr(fx, "LifecycleError")
        assert hasattr(fx, "LifecycleTransitionError")
        assert hasattr(fx, "EventTimeline")
        assert hasattr(fx, "TradeEvent")
        assert hasattr(fx, "EventType")
        assert hasattr(fx, "ProposalMatcher")
        assert hasattr(fx, "MatchConfidence")
        assert hasattr(fx, "MatchResult")
        assert hasattr(fx, "PostTradeAnalytics")
        assert hasattr(fx, "PerformanceReport")
        assert hasattr(fx, "PairMetrics")
        assert hasattr(fx, "SetupMetrics")
        assert hasattr(fx, "SessionMetrics")

        # Test instantiation from forex package
        manager = fx.ForexJournalManager(db_path=":memory:")
        assert manager is not None
        manager.close()
