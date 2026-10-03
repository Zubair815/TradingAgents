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
    ReconciliationStatus,
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
    reasoning: str = "Strong trend continuation pattern.",
    valid_until: str | None = None,
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
        reasoning=reasoning,
        trade_rationale_summary="Long EURUSD on continuation.",
        valid_until=valid_until,
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

    def test_phase10_raw_lifecycle_events_and_fields(self, memory_journal: ForexTradeJournal):
        timeline = EventTimeline(journal=memory_journal)

        event_specs = [
            (EventType.ORDER_CREATED, "ord_101", None, None, None, 1.0850, 1.0),
            (EventType.DEAL_FILLED, "ord_101", "deal_201", "pos_301", None, 1.0852, 1.0),
            (EventType.POSITION_OPENED, None, None, "pos_301", None, 1.0852, 1.0),
            (EventType.SL_CHANGED, None, None, "pos_301", "1.0800", "1.0820", None),
            (EventType.TP_CHANGED, None, None, "pos_301", "1.0920", "1.0950", None),
            (EventType.POSITION_ADDED, None, "deal_202", "pos_301", None, 1.0860, 0.5),
            (EventType.BREAK_EVEN_MOVE, None, None, "pos_301", "1.0820", "1.0852", None),
            (EventType.PARTIAL_CLOSE, None, "deal_203", "pos_301", "1.5", 1.0890, 0.5),
            (EventType.COMMISSION, None, "deal_201", "pos_301", None, -5.0, None),
            (EventType.SWAP, None, "deal_201", "pos_301", None, -1.25, None),
            (EventType.FEE, None, None, "pos_301", None, -0.50, None),
            (EventType.FINAL_CLOSE, None, "deal_204", "pos_301", "1.0", 1.0920, 1.0),
        ]

        for etype, ord_id, deal_id, pos_id, old_v, new_or_p, vol in event_specs:
            price_val = new_or_p if isinstance(new_or_p, (int, float)) else None
            new_val = str(new_or_p) if isinstance(new_or_p, str) else None
            evt = timeline.record_event(
                event_type=etype,
                trade_id="trd_phase10",
                proposal_id="prop_phase10",
                broker_order_id=ord_id,
                broker_deal_id=deal_id,
                broker_position_id=pos_id,
                old_value=old_v,
                new_value=new_val,
                price=price_val,
                volume=vol,
                source="MT5Observer",
                description=f"Recorded {etype.value}",
                metadata={"test_key": "test_val"},
            )
            assert evt.event_type == etype
            assert evt.trade_id == "trd_phase10"
            assert evt.source == "MT5Observer"

        # Verify querying from journal preserves all fields
        events = timeline.get_timeline_for_trade("trd_phase10")
        assert len(events) == len(event_specs)
        deal_event = [e for e in events if e.event_type == EventType.DEAL_FILLED][0]
        assert deal_event.broker_deal_id == "deal_201"
        assert deal_event.price == 1.0852
        assert deal_event.volume == 1.0

    def test_duplicate_deal_event_prevented_by_unique_constraint(self, memory_journal: ForexTradeJournal):
        timeline = EventTimeline(journal=memory_journal)

        # Record first deal fill
        e1 = timeline.record_event(
            event_type=EventType.DEAL_FILLED,
            trade_id="trd_uniq",
            broker_deal_id="deal_9999",
            price=1.0850,
            volume=0.5,
            source="MT5Observer",
            description="First fill",
        )
        assert e1 is not None

        # Re-polling the same deal must not create a duplicate event
        e2 = timeline.record_event(
            event_type=EventType.DEAL_FILLED,
            trade_id="trd_uniq",
            broker_deal_id="deal_9999",
            price=1.0850,
            volume=0.5,
            source="MT5Observer",
            description="Duplicate polling fill",
        )
        assert e2 is not None

        # Database must only have 1 event
        events = timeline.get_timeline_for_trade("trd_uniq")
        assert len(events) == 1
        assert events[0].broker_deal_id == "deal_9999"

    def test_partial_close_preserves_same_logical_trade(self, memory_journal: ForexTradeJournal):
        mgr = TradeLifecycleManager(journal=memory_journal)
        prop_id = mgr.submit_proposal(make_proposal("EURUSD", entry=1.0850, lots=1.0))
        mgr.evaluate_risk(
            prop_id,
            ForexRiskDecision(
                pair="EURUSD",
                decision=ForexRiskDecisionAction.APPROVE,
                original_action=ForexAction.LONG,
                approved_action=ForexAction.LONG,
                executive_rationale="Approved",
            ),
        )
        trade_id = mgr.open_position_from_proposal(prop_id, open_price=1.0850, lots=1.0)

        # Partially close 0.4 lots
        res = mgr.partial_close_trade(
            trade_id=trade_id,
            lots_to_close=0.4,
            close_price=1.0890,
            actor="Trader",
        )
        assert res["lots_closed"] == 0.4
        assert res["remaining_lots"] == 0.6

        # Verify same logical trade is still open with 0.6 lots
        trade = memory_journal.get_trade(trade_id)
        assert trade is not None
        assert trade.trade_id == trade_id
        assert trade.lots == 0.6
        assert trade.status == TradeStatus.OPEN

        # Verify PARTIAL_CLOSE event logged to trade timeline
        events = mgr.timeline.get_timeline_for_trade(trade_id)
        partial_events = [e for e in events if e.event_type == EventType.PARTIAL_CLOSE]
        assert len(partial_events) == 1
        assert partial_events[0].payload["lots_closed"] == 0.4
        assert partial_events[0].payload["remaining_lots"] == 0.6


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

    def test_single_obvious_proposal_matched(self, memory_journal: ForexTradeJournal):
        mgr = TradeLifecycleManager(journal=memory_journal)
        matcher = ProposalMatcher(journal=memory_journal, lifecycle=mgr)

        prop_id = mgr.submit_proposal(make_proposal("EURUSD", action=ForexAction.LONG, entry=1.0850, lots=0.50))
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
            "ticket": 6001,
            "symbol": "EURUSD",
            "type": 0,
            "price_open": 1.0850,
            "volume": 0.50,
            "time": datetime.now(timezone.utc),
        }

        match = matcher.match_position(pos)
        assert match.is_matched is True
        assert match.status == ReconciliationStatus.MATCHED
        assert match.proposal_id == prop_id

    def test_ambiguous_proposals_require_confirmation(self, memory_journal: ForexTradeJournal):
        mgr = TradeLifecycleManager(journal=memory_journal)
        matcher = ProposalMatcher(journal=memory_journal, lifecycle=mgr, ambiguity_delta=0.05)

        # Create two very similar proposals on EURUSD
        prop_id1 = mgr.submit_proposal(make_proposal("EURUSD", action=ForexAction.LONG, entry=1.0850, lots=0.50))
        mgr.evaluate_risk(
            prop_id1,
            ForexRiskDecision(
                pair="EURUSD",
                decision=ForexRiskDecisionAction.APPROVE,
                original_action=ForexAction.LONG,
                approved_action=ForexAction.LONG,
                executive_rationale="Approved.",
            ),
        )

        prop_id2 = mgr.submit_proposal(make_proposal("EURUSD", action=ForexAction.LONG, entry=1.0850, lots=0.50))
        mgr.evaluate_risk(
            prop_id2,
            ForexRiskDecision(
                pair="EURUSD",
                decision=ForexRiskDecisionAction.APPROVE,
                original_action=ForexAction.LONG,
                approved_action=ForexAction.LONG,
                executive_rationale="Approved.",
            ),
        )

        pos = {
            "ticket": 6002,
            "symbol": "EURUSD",
            "type": 0,
            "price_open": 1.0850,
            "volume": 0.50,
            "time": datetime.now(timezone.utc),
        }

        match = matcher.match_position(pos)
        assert match.is_matched is False
        assert match.status == ReconciliationStatus.NEEDS_CONFIRMATION
        assert any("Ambiguous match" in r for r in match.reasons)

    def test_manual_unplanned_trade_journaling(self, memory_journal: ForexTradeJournal):
        mgr = TradeLifecycleManager(journal=memory_journal)
        matcher = ProposalMatcher(journal=memory_journal, lifecycle=mgr)

        pos = {
            "ticket": 6003,
            "symbol": "USDCHF",
            "type": 1,  # SELL
            "price_open": 0.8920,
            "volume": 0.25,
            "sl": 0.8960,
            "tp": 0.8840,
            "time": datetime.now(timezone.utc),
        }

        trade_id, match_res = matcher.record_manual_unplanned_trade(pos)
        assert match_res.status == ReconciliationStatus.MANUAL_UNPLANNED
        assert match_res.is_matched is False
        assert match_res.proposal_id is None

        trade = memory_journal.get_trade(trade_id)
        assert trade is not None
        assert trade.pair == "USDCHF"
        assert trade.action == ForexAction.SHORT
        assert trade.open_price == 0.8920
        assert "MANUAL_UNPLANNED" in trade.tags
        assert trade.status == TradeStatus.OPEN

    def test_entry_outside_validity_unmatched(self, memory_journal: ForexTradeJournal):
        from datetime import timedelta
        mgr = TradeLifecycleManager(journal=memory_journal)
        matcher = ProposalMatcher(journal=memory_journal, lifecycle=mgr)
        now = datetime.now(timezone.utc)
        valid_until = (now - timedelta(hours=1)).isoformat()

        prop_id = mgr.submit_proposal(
            make_proposal("EURUSD", action=ForexAction.LONG, entry=1.0850, lots=0.50, valid_until=valid_until)
        )
        mgr.evaluate_risk(
            prop_id,
            ForexRiskDecision(
                pair="EURUSD",
                decision=ForexRiskDecisionAction.APPROVE,
                original_action=ForexAction.LONG,
                approved_action=ForexAction.LONG,
                executive_rationale="Approved.",
            ),
        )

        # Position executed now (after valid_until)
        pos = {
            "ticket": 6004,
            "symbol": "EURUSD",
            "type": 0,
            "price_open": 1.0850,
            "volume": 0.50,
            "time": now,
        }

        match = matcher.match_position(pos)
        assert match.is_matched is False
        assert match.status == ReconciliationStatus.UNMATCHED


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


# ---------------------------------------------------------------------------
# 7. Proposal Lifecycle (Phase 7)
# ---------------------------------------------------------------------------


class TestProposalLifecycleComplete:
    """Validate full proposal lifecycle states, transitions, expiry, supersession, and immutability."""

    def test_user_actions_executed_skipped_wait(self, memory_journal: ForexTradeJournal):
        mgr = TradeLifecycleManager(journal=memory_journal)

        # 1. Create and approve a proposal
        prop = make_proposal("EURUSD", entry=1.0850, sl=1.0810, tp1=1.0930)
        prop_id = mgr.submit_proposal(prop)
        decision = ForexRiskDecision(
            pair="EURUSD",
            decision=ForexRiskDecisionAction.APPROVE,
            original_action=ForexAction.LONG,
            approved_action=ForexAction.LONG,
            max_risk_percent=1.0,
            approved_lot_size=0.50,
            executive_rationale="Approved within risk boundaries.",
        )
        mgr.evaluate_risk(prop_id, decision)

        # 2. Action: WAIT -> transitions to WAITING_USER
        status = mgr.record_user_action(prop_id, action="WAIT", reason="Awaiting London open")
        assert status == ProposalStatus.WAITING_USER
        assert memory_journal.get_proposal(prop_id).status == ProposalStatus.WAITING_USER

        # 3. Action: EXECUTED -> records workflow intent/state
        status_exec = mgr.record_user_action(prop_id, action="EXECUTED", reason="Manual fill confirmed by trader")
        assert status_exec == ProposalStatus.EXECUTED
        assert memory_journal.get_proposal(prop_id).status == ProposalStatus.EXECUTED

        # Test SKIPPED action on another proposal
        prop2 = make_proposal("GBPUSD", entry=1.2650, sl=1.2610, tp1=1.2730)
        prop2_id = mgr.submit_proposal(prop2)
        mgr.evaluate_risk(prop2_id, decision)

        status_skip = mgr.record_user_action(prop2_id, action="SKIPPED", reason="News event coming up")
        assert status_skip == ProposalStatus.SKIPPED
        assert memory_journal.get_proposal(prop2_id).status == ProposalStatus.SKIPPED

    def test_invalid_lifecycle_transitions_raise(self, memory_journal: ForexTradeJournal):
        from tradingagents.journal.lifecycle import LifecycleTransitionError

        mgr = TradeLifecycleManager(journal=memory_journal)

        prop = make_proposal("EURUSD", entry=1.0850, sl=1.0810, tp1=1.0930)
        prop_id = mgr.submit_proposal(prop)

        # PROPOSED cannot jump directly to SETTLED
        curr_state = LifecycleState(memory_journal.get_proposal(prop_id).status.value)
        with pytest.raises(LifecycleTransitionError):
            mgr.validate_transition(curr_state, LifecycleState.SETTLED)

        # Reject proposal
        decision = ForexRiskDecision(
            pair="EURUSD",
            decision=ForexRiskDecisionAction.REJECT,
            original_action=ForexAction.LONG,
            approved_action=ForexAction.NO_TRADE,
            max_risk_percent=0.0,
            approved_lot_size=0.0,
            executive_rationale="Spread too wide",
        )
        mgr.evaluate_risk(prop_id, decision)
        rejected_state = LifecycleState(memory_journal.get_proposal(prop_id).status.value)

        # Terminal REJECTED cannot transition to APPROVED or EXECUTED
        with pytest.raises(LifecycleTransitionError):
            mgr.validate_transition(rejected_state, LifecycleState.APPROVED)
        with pytest.raises(LifecycleTransitionError):
            mgr.validate_transition(rejected_state, LifecycleState.EXECUTED)

    def test_proposal_expiry_from_valid_until(self, memory_journal: ForexTradeJournal):
        from datetime import datetime, timedelta, timezone

        mgr = TradeLifecycleManager(journal=memory_journal)

        now = datetime.now(timezone.utc)
        expired_time = (now - timedelta(hours=2)).isoformat()
        future_time = (now + timedelta(hours=2)).isoformat()

        # Proposal 1: expired validity
        prop1 = make_proposal("EURUSD", entry=1.0850, sl=1.0810, tp1=1.0930)
        prop1.valid_until = expired_time
        p1_id = mgr.submit_proposal(prop1)

        # Proposal 2: still valid
        prop2 = make_proposal("USDJPY", entry=155.20, sl=154.80, tp1=156.00)
        prop2.valid_until = future_time
        p2_id = mgr.submit_proposal(prop2)

        # Check and expire
        expired_ids = mgr.check_and_expire_proposals(current_time=now)

        assert p1_id in expired_ids
        assert p2_id not in expired_ids
        assert memory_journal.get_proposal(p1_id).status == ProposalStatus.EXPIRED
        assert memory_journal.get_proposal(p2_id).status == ProposalStatus.PROPOSED

    def test_proposal_supersession_for_same_pair(self, memory_journal: ForexTradeJournal):
        mgr = TradeLifecycleManager(journal=memory_journal)

        decision = ForexRiskDecision(
            pair="EURUSD",
            decision=ForexRiskDecisionAction.APPROVE,
            original_action=ForexAction.LONG,
            approved_action=ForexAction.LONG,
            max_risk_percent=1.0,
            approved_lot_size=0.50,
            executive_rationale="Approved within risk boundaries.",
        )

        # Submit first proposal for EURUSD
        p1 = make_proposal("EURUSD", entry=1.0850, sl=1.0810, tp1=1.0930)
        p1_id = mgr.submit_proposal(p1)
        mgr.evaluate_risk(p1_id, decision)

        # Submit proposal for GBPUSD (different pair)
        p_other = make_proposal("GBPUSD", entry=1.2650, sl=1.2610, tp1=1.2730)
        p_other_id = mgr.submit_proposal(p_other)
        mgr.evaluate_risk(p_other_id, decision)

        # Submit second proposal for EURUSD
        p2 = make_proposal("EURUSD", entry=1.0880, sl=1.0840, tp1=1.0960)
        p2_id = mgr.submit_proposal(p2)
        mgr.evaluate_risk(p2_id, decision)

        # Supersede older proposals for EURUSD
        superseded = memory_journal.supersede_proposals(pair="EURUSD", exclude_proposal_id=p2_id)

        assert p1_id in superseded
        assert p2_id not in superseded
        assert p_other_id not in superseded

        assert memory_journal.get_proposal(p1_id).status == ProposalStatus.SUPERSEDED
        assert memory_journal.get_proposal(p2_id).status == ProposalStatus.APPROVED
        assert memory_journal.get_proposal(p_other_id).status == ProposalStatus.APPROVED

    def test_proposal_immutability(self, memory_journal: ForexTradeJournal):
        mgr = TradeLifecycleManager(journal=memory_journal)

        original_prop = make_proposal(
            "EURUSD",
            entry=1.0850,
            sl=1.0810,
            tp1=1.0930,
            lots=0.45,
            reasoning="Original deep reasoning thesis that must never be altered.",
        )
        prop_id = mgr.submit_proposal(original_prop)

        # Read original stored record
        record_before = memory_journal.get_proposal(prop_id)
        assert record_before.entry_price == 1.0850
        assert record_before.stop_loss == 1.0810
        assert record_before.take_profit_1 == 1.0930
        assert record_before.suggested_lot_size == 0.45
        assert record_before.reasoning == "Original deep reasoning thesis that must never be altered."

        # Transition status to SUPERSEDED
        memory_journal.supersede_proposals(pair="EURUSD")

        # Verify status updated, but all quantitative and thesis data remain identical
        record_after = memory_journal.get_proposal(prop_id)
        assert record_after.status == ProposalStatus.SUPERSEDED
        assert record_after.entry_price == 1.0850
        assert record_after.stop_loss == 1.0810
        assert record_after.take_profit_1 == 1.0930
        assert record_after.suggested_lot_size == 0.45
        assert record_after.reasoning == "Original deep reasoning thesis that must never be altered."


class TestForexJournalSchemaAndProvenance:
    """Tests for DOM-010 and DOM-011 schema versioning and provenance contracts."""

    def test_trade_record_has_schema_version(self, memory_journal: ForexTradeJournal):
        trade = memory_journal.record_trade_open(
            pair="EURUSD",
            action=ForexAction.LONG,
            open_price=1.0850,
            stop_loss=1.0810,
            take_profit=1.0930,
            lots=0.5,
        )
        assert trade.schema_version == 1

        retrieved = memory_journal.get_trade(trade.trade_id)
        assert retrieved is not None
        assert retrieved.schema_version == 1
        assert retrieved.pair == "EURUSD"

    def test_trades_table_has_schema_version_column(self, memory_journal: ForexTradeJournal):
        conn = memory_journal._get_connection()
        try:
            cursor = conn.execute("PRAGMA table_info(trades);")
            cols = {row[1] for row in cursor.fetchall()}
            assert "schema_version" in cols
        finally:
            if conn != memory_journal._mem_conn:
                conn.close()

    def test_migration_5_upgrade_preserves_historical_trades(self, tmp_path, monkeypatch):
        import sqlite3

        from tradingagents.database import migrations

        db_path = tmp_path / "legacy_trades.db"
        with monkeypatch.context() as patch:
            patch.setattr(migrations, "MIGRATIONS", migrations.MIGRATIONS[:1])
            assert migrations.run_migrations(db_path) == 1

        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                INSERT INTO trades (
                    trade_id, pair, action, status, open_time_utc, open_price, stop_loss, lots
                ) VALUES ('leg_1', 'EURUSD', 'LONG', 'OPEN', '2026-09-01T00:00:00+00:00', 1.08, 1.075, 0.1);
                """
            )

        applied = migrations.run_migrations(db_path)
        assert applied == len(migrations.MIGRATIONS) - 1

        with ForexTradeJournal(db_path=db_path) as journal:
            trade = journal.get_trade("leg_1")
            assert trade is not None
            assert trade.trade_id == "leg_1"
            assert trade.schema_version == 1
            assert trade.open_price == 1.08

    def test_strategy_version_provenance_round_trip(self, memory_journal: ForexTradeJournal):
        import hashlib
        prompt = "System prompt for Forex institutional trader"
        prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()

        rec = memory_journal.record_strategy_version(
            strategy_name="ForexBreakoutV1",
            prompt_hash=prompt_hash,
            model_name="gpt-4.1",
            parameters={"timeframe": "H1", "risk_percent": 1.0},
        )
        assert rec.version_id.startswith("v_")
        assert rec.schema_version == 1
        assert rec.prompt_hash == prompt_hash

        fetched = memory_journal.get_strategy_version(rec.version_id)
        assert fetched is not None
        assert fetched.version_id == rec.version_id
        assert fetched.prompt_hash == prompt_hash
        assert fetched.parameters["risk_percent"] == 1.0


class TestProposalLifecycleAndJournalCompletion:
    """Phase 6: Comprehensive tests for deterministic proposal expiry, lifecycle transitions,
    immutability preservation, user-note isolation, and export functionality.
    """

    def test_expiry_boundary(self, memory_journal: ForexTradeJournal):
        mgr = TradeLifecycleManager(journal=memory_journal)
        cutoff_str = "2026-10-03T12:00:00+00:00"
        cutoff_dt = datetime.fromisoformat(cutoff_str)

        prop_id = mgr.submit_proposal(
            make_proposal("EURUSD", entry=1.0850, valid_until=cutoff_str)
        )

        # 1 second before cutoff: must NOT expire
        res_before = mgr.check_and_expire_proposals(
            current_time=datetime(2026, 10, 3, 11, 59, 59, tzinfo=timezone.utc)
        )
        assert res_before == []
        rec_before = memory_journal.get_proposal(prop_id)
        assert rec_before.status == ProposalStatus.PROPOSED

        # Exactly at cutoff boundary: MUST expire
        res_exact = mgr.check_and_expire_proposals(current_time=cutoff_dt)
        assert res_exact == [prop_id]
        rec_exact = memory_journal.get_proposal(prop_id)
        assert rec_exact.status == ProposalStatus.EXPIRED

        # Verify timeline event
        events = mgr.timeline.get_timeline_for_proposal(prop_id)
        exp_events = [e for e in events if e.event_type == EventType.PROPOSAL_EXPIRED]
        assert len(exp_events) == 1
        assert "Proposal validity window elapsed" in exp_events[0].description

    def test_already_executed_proposal_cannot_expire(self, memory_journal: ForexTradeJournal):
        mgr = TradeLifecycleManager(journal=memory_journal)
        cutoff_str = "2026-10-03T12:00:00+00:00"

        prop_id = mgr.submit_proposal(
            make_proposal("EURUSD", entry=1.0850, lots=0.5, valid_until=cutoff_str)
        )
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
        mgr.open_position_from_proposal(proposal_id=prop_id, open_price=1.0850, lots=0.5)

        # Check proposal is executed
        rec = memory_journal.get_proposal(prop_id)
        assert rec.status == ProposalStatus.EXECUTED

        # Running check_and_expire_proposals past validity window must NOT expire it
        after_cutoff = datetime(2026, 10, 3, 13, 0, 0, tzinfo=timezone.utc)
        res = mgr.check_and_expire_proposals(current_time=after_cutoff)
        assert res == []
        assert memory_journal.get_proposal(prop_id).status == ProposalStatus.EXECUTED

        # Direct expiration call must raise LifecycleTransitionError
        with pytest.raises(LifecycleTransitionError):
            mgr.expire_proposal(prop_id)

    def test_rejected_proposal_cannot_expire(self, memory_journal: ForexTradeJournal):
        mgr = TradeLifecycleManager(journal=memory_journal)
        cutoff_str = "2026-10-03T12:00:00+00:00"

        prop_id = mgr.submit_proposal(
            make_proposal("EURUSD", entry=1.0850, valid_until=cutoff_str)
        )
        mgr.evaluate_risk(
            prop_id,
            ForexRiskDecision(
                pair="EURUSD",
                decision=ForexRiskDecisionAction.REJECT,
                original_action=ForexAction.LONG,
                approved_action=ForexAction.NO_TRADE,
                executive_rationale="Risk limit exceeded.",
            ),
        )
        assert memory_journal.get_proposal(prop_id).status == ProposalStatus.REJECTED

        # Cannot expire via automated check
        after_cutoff = datetime(2026, 10, 3, 13, 0, 0, tzinfo=timezone.utc)
        res = mgr.check_and_expire_proposals(current_time=after_cutoff)
        assert res == []
        assert memory_journal.get_proposal(prop_id).status == ProposalStatus.REJECTED

        # Direct expiration call must raise LifecycleTransitionError
        with pytest.raises(LifecycleTransitionError):
            mgr.expire_proposal(prop_id)

    def test_superseded_proposal_cannot_expire(self, memory_journal: ForexTradeJournal):
        mgr = TradeLifecycleManager(journal=memory_journal)
        cutoff_str = "2026-10-03T12:00:00+00:00"

        prop_id1 = mgr.submit_proposal(
            make_proposal("EURUSD", entry=1.0850, valid_until=cutoff_str)
        )
        mgr.supersede_proposal(prop_id1, reason="Superseded by newer analysis")
        assert memory_journal.get_proposal(prop_id1).status == ProposalStatus.SUPERSEDED

        # Cannot expire via automated check
        after_cutoff = datetime(2026, 10, 3, 13, 0, 0, tzinfo=timezone.utc)
        res = mgr.check_and_expire_proposals(current_time=after_cutoff)
        assert res == []
        assert memory_journal.get_proposal(prop_id1).status == ProposalStatus.SUPERSEDED

        # Direct expiration call must raise LifecycleTransitionError
        with pytest.raises(LifecycleTransitionError):
            mgr.expire_proposal(prop_id1)

    def test_repeated_expiry_processing_idempotent(self, memory_journal: ForexTradeJournal):
        mgr = TradeLifecycleManager(journal=memory_journal)
        cutoff_str = "2026-10-03T10:00:00+00:00"

        prop_id = mgr.submit_proposal(
            make_proposal("EURUSD", entry=1.0850, valid_until=cutoff_str)
        )
        after_cutoff = datetime(2026, 10, 3, 11, 0, 0, tzinfo=timezone.utc)

        # First run expires proposal
        res1 = mgr.check_and_expire_proposals(current_time=after_cutoff)
        assert res1 == [prop_id]
        assert memory_journal.get_proposal(prop_id).status == ProposalStatus.EXPIRED

        # Second run is completely idempotent
        res2 = mgr.check_and_expire_proposals(current_time=after_cutoff)
        assert res2 == []

        # Direct expire_proposal call on already-expired proposal is safe no-op
        mgr.expire_proposal(prop_id)
        assert memory_journal.get_proposal(prop_id).status == ProposalStatus.EXPIRED

        # Exactly 1 PROPOSAL_EXPIRED event exists in timeline
        events = mgr.timeline.get_timeline_for_proposal(prop_id)
        exp_events = [e for e in events if e.event_type == EventType.PROPOSAL_EXPIRED]
        assert len(exp_events) == 1

    def test_restart_and_persistence_of_proposals_and_expiry(self, tmp_path):
        db_file = tmp_path / "persistence_test_journal.db"
        cutoff_str = "2026-10-03T10:00:00+00:00"

        # Session 1: Create proposal with past validity
        with ForexTradeJournal(db_path=db_file, auto_migrate=True) as j1:
            mgr1 = TradeLifecycleManager(journal=j1)
            prop_id = mgr1.submit_proposal(
                make_proposal("GBPUSD", entry=1.2850, valid_until=cutoff_str)
            )
            # Expire proposal in session 1
            res1 = mgr1.check_and_expire_proposals(
                current_time=datetime(2026, 10, 3, 10, 30, 0, tzinfo=timezone.utc)
            )
            assert res1 == [prop_id]

        # Session 2: Reopen journal and manager from disk
        with ForexTradeJournal(db_path=db_file, auto_migrate=True) as j2:
            rec = j2.get_proposal(prop_id)
            assert rec is not None
            assert rec.status == ProposalStatus.EXPIRED

            mgr2 = TradeLifecycleManager(journal=j2)
            # Repeated check across restarts produces no extra events or errors
            res2 = mgr2.check_and_expire_proposals(
                current_time=datetime(2026, 10, 3, 11, 0, 0, tzinfo=timezone.utc)
            )
            assert res2 == []

            # Timeline event persisted across restart
            events = mgr2.timeline.get_timeline_for_proposal(prop_id)
            exp_events = [e for e in events if e.event_type == EventType.PROPOSAL_EXPIRED]
            assert len(exp_events) == 1

    def test_user_note_protection_preserves_immutable_evidence(self, memory_journal: ForexTradeJournal):
        import sqlite3
        mgr = TradeLifecycleManager(journal=memory_journal)
        prop_id = mgr.submit_proposal(
            make_proposal("USDJPY", entry=155.20, sl=154.80, tp1=156.00, lots=0.40)
        )
        mgr.evaluate_risk(
            prop_id,
            ForexRiskDecision(
                pair="USDJPY",
                decision=ForexRiskDecisionAction.APPROVE,
                original_action=ForexAction.LONG,
                approved_action=ForexAction.LONG,
                executive_rationale="Approved.",
            ),
        )
        trade_id = mgr.open_position_from_proposal(prop_id, open_price=155.22, lots=0.40, ticket=55001)

        # Query raw SQLite evidence before user edit
        with memory_journal._lock:
            conn = memory_journal._get_connection()
            row_ev_before = conn.execute(
                "SELECT payload_json, content_hash FROM proposal_evidence WHERE proposal_id = ?;",
                (prop_id,),
            ).fetchone()
            row_tr_before = conn.execute(
                "SELECT open_price, lots, stop_loss, schema_version FROM trades WHERE trade_id = ?;",
                (trade_id,),
            ).fetchone()

        # User updates reflection and notes on trade
        user_reflection = "Clean trend breakout setup with minor slippage."
        user_notes = "Execution rating 9/10"
        memory_journal.update_trade_reflection(
            trade_id=trade_id,
            reflection=user_reflection,
            tags=["Breakout", "Tokyo-London"],
            notes=user_notes,
        )

        # Verify evidence remains bit-for-bit identical in SQLite
        with memory_journal._lock:
            conn = memory_journal._get_connection()
            row_ev_after = conn.execute(
                "SELECT payload_json, content_hash FROM proposal_evidence WHERE proposal_id = ?;",
                (prop_id,),
            ).fetchone()
            row_tr_after = conn.execute(
                "SELECT open_price, lots, stop_loss, schema_version FROM trades WHERE trade_id = ?;",
                (trade_id,),
            ).fetchone()

        assert row_ev_before == row_ev_after, "Immutable proposal_evidence was mutated by user note update!"
        assert row_tr_before == row_tr_after, "Broker trade execution fields were mutated by user reflection!"

        # Direct SQL mutation attempts on immutable proposal evidence are rejected by SQLite triggers
        with memory_journal._lock:
            conn = memory_journal._get_connection()
            with pytest.raises(sqlite3.IntegrityError, match="Proposal evidence is immutable"):
                conn.execute(
                    "UPDATE proposals SET metadata_json = '{\"hack\": true}' WHERE proposal_id = ?;",
                    (prop_id,),
                )
            with pytest.raises(sqlite3.IntegrityError, match="Immutable research record"):
                conn.execute(
                    "UPDATE proposal_evidence SET payload_json = '{\"hack\": true}' WHERE proposal_id = ?;",
                    (prop_id,),
                )

        trade_after = memory_journal.get_trade(trade_id)
        assert trade_after.reflection == user_reflection
        assert trade_after.notes == user_notes
        assert trade_after.tags == ["Breakout", "Tokyo-London"]
        assert trade_after.open_price == 155.22
        assert trade_after.lots == 0.40

    def test_export_trades_correctness(self, memory_journal: ForexTradeJournal):
        memory_journal.record_trade_open(
            pair="EURUSD",
            action=ForexAction.LONG,
            open_price=1.0850,
            stop_loss=1.0810,
            take_profit=1.0920,
            lots=0.5,
            trade_id="trd_exp_1",
            open_time_utc="2026-10-01T10:00:00+00:00",
        )
        memory_journal.record_trade_close(
            trade_id="trd_exp_1",
            close_price=1.0920,
            close_time_utc="2026-10-01T15:30:00+00:00",
            exit_reason=TradeExitReason.TAKE_PROFIT,
        )
        memory_journal.record_trade_open(
            pair="GBPJPY",
            action=ForexAction.SHORT,
            open_price=190.50,
            stop_loss=191.20,
            take_profit=189.00,
            lots=0.2,
            trade_id="trd_exp_2",
            open_time_utc="2026-10-02T08:00:00+00:00",
        )

        # JSON export
        import json
        json_str = memory_journal.export_trades(fmt="json")
        data = json.loads(json_str)
        assert isinstance(data, list)
        assert len(data) == 2
        trade_ids = {item["trade_id"] for item in data}
        assert trade_ids == {"trd_exp_1", "trd_exp_2"}

        # CSV export
        import csv
        import io
        csv_str = memory_journal.export_trades(fmt="csv")
        reader = list(csv.reader(io.StringIO(csv_str)))
        assert len(reader) == 3  # Header + 2 data rows
        header = reader[0]
        assert "trade_id" in header
        assert "net_profit" in header
        assert "schema_version" in header

        # Invalid format
        with pytest.raises(ValueError, match="Unsupported export format"):
            memory_journal.export_trades(fmt="xml")

