"""End-to-End Closed-Loop Integration Test Suite (Requirement 70).

Verifies the complete closed-loop cycle of the Forex Decision-Support & Journaling Platform:
1. ForexTraderProposal generated
2. Approved by deterministic ForexRiskEngine
3. Persisted in immutable ForexTradeJournal
4. Simulated MT5 execution & deal fill recorded
5. Trade lifecycle closed with M1 price history
6. Automatic MFE/MAE excursion metrics calculated
7. ForexLearningManager post-trade reflection executed
8. Lessons persisted in ForexLessonStore
9. Lessons retrieved and injected into prompt guidance for future proposals
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tradingagents.agents.schemas_forex import (
    ForexAction,
    ForexRiskDecisionAction,
    ForexTraderProposal,
    OrderType,
    SetupType,
)
from tradingagents.database.journal import ForexTradeJournal
from tradingagents.database.models import ProposalStatus, TradeExitReason, TradeStatus
from tradingagents.dataflows.forex_data import ForexBar
from tradingagents.forex import ForexRiskEngine, ForexRiskLimits
from tradingagents.learning.manager import ForexLearningManager
from tradingagents.learning.models import ReflectionRating
from tradingagents.learning.store import ForexLessonStore
from tradingagents.metrics.models import TradeOutcomeCategory


@pytest.fixture
def temp_journal_and_learning(tmp_path: Path):
    """Provide isolated SQLite trade journal and learning manager."""
    db_file = tmp_path / "forex_closed_loop.db"
    lessons_file = tmp_path / "forex_lessons.db"

    journal = ForexTradeJournal(db_path=db_file)
    store = ForexLessonStore(db_path=lessons_file)
    learning_manager = ForexLearningManager(journal=journal, store=store)

    return journal, learning_manager, store


def test_full_closed_loop_lifecycle_profitable_trade(temp_journal_and_learning):
    """Verify entire closed loop from proposal -> risk -> execution -> close -> reflection -> retrieval."""
    journal, learning_manager, store = temp_journal_and_learning

    # -------------------------------------------------------------------------
    # 1. ForexTraderProposal Generation
    # -------------------------------------------------------------------------
    proposal = ForexTraderProposal(
        pair="EURUSD",
        action=ForexAction.LONG,
        order_type=OrderType.MARKET,
        setup_type=SetupType.PULLBACK,
        timeframe="H1",
        entry_price=1.08500,
        stop_loss=1.08200,      # 30 pips risk
        take_profit_1=1.09100,  # 60 pips reward (R:R = 2.0)
        suggested_risk_percent=1.0,
        reasoning="H1 bullish breakout retest with institutional order block confluence at 1.08500.",
    )

    # -------------------------------------------------------------------------
    # 2. Risk Engine Evaluation & Approval
    # -------------------------------------------------------------------------
    risk_limits = ForexRiskLimits(
        min_risk_reward_ratio=1.5,
        max_risk_percent=1.0,
        enforce_news_blackout=False,
    )
    risk_engine = ForexRiskEngine(default_limits=risk_limits)

    risk_decision = risk_engine.validate_proposal(
        proposal=proposal,
        curr_date="2025-06-11",
        curr_time_utc="14:00:00",
        account_balance=100_000.0,
    )

    assert risk_decision.decision == ForexRiskDecisionAction.APPROVE
    assert risk_decision.approved_lot_size is not None and risk_decision.approved_lot_size > 0.0
    assert risk_decision.max_risk_percent <= 1.0

    # -------------------------------------------------------------------------
    # 3. Journal Persistence (Immutable Proposal)
    # -------------------------------------------------------------------------
    proposal_id = journal.save_proposal(
        proposal=proposal,
        risk_decision=risk_decision,
        status=ProposalStatus.APPROVED,
        metadata={"source": "closed_loop_test"},
    )
    assert proposal_id is not None
    assert proposal_id.startswith("prop_")

    # Verify stored proposal record
    saved_prop = journal.get_proposal(proposal_id)
    assert saved_prop is not None
    assert saved_prop.pair == "EURUSD"
    assert saved_prop.status == ProposalStatus.APPROVED
    assert saved_prop.risk_decision is not None
    decision_val = (
        saved_prop.risk_decision.get("decision")
        if isinstance(saved_prop.risk_decision, dict)
        else getattr(saved_prop.risk_decision, "decision", None)
    )
    assert decision_val in (ForexRiskDecisionAction.APPROVE, ForexRiskDecisionAction.APPROVE.value)

    # -------------------------------------------------------------------------
    # 4. Simulated MT5 Manual Fill & Execution Record
    # -------------------------------------------------------------------------
    trade_id = "trd_closed_loop_eurusd_001"
    deal_id = "mt5_deal_998877"

    # Record trade open in journal first (foreign key target for executions)
    open_trade = journal.record_trade_open(
        trade_id=trade_id,
        proposal_id=proposal_id,
        pair="EURUSD",
        action=ForexAction.LONG,
        open_price=1.08502,
        stop_loss=1.08200,
        take_profit=1.09100,
        lots=risk_decision.approved_lot_size,
        tags=["h1_pullback", "order_block"],
        notes="Executed manually on MT5 observing agent proposal",
    )
    assert open_trade.status == TradeStatus.OPEN
    assert open_trade.proposal_id == proposal_id

    # Record MT5 execution deal
    exec_rec = journal.record_execution(
        deal_id=deal_id,
        trade_id=trade_id,
        pair="EURUSD",
        order_type="BUY",
        volume=risk_decision.approved_lot_size,
        price=1.08502,  # 0.2 pips slippage
        proposal_id=proposal_id,
        slippage_pips=0.2,
        spread_at_open_pips=0.7,
    )
    assert exec_rec.deal_id == deal_id

    # -------------------------------------------------------------------------
    # 5. Simulated Trade Close with M1 Price History
    # -------------------------------------------------------------------------
    t_start = datetime.now(timezone.utc) - timedelta(hours=3)
    # Synthetic M1 candles showing clean run-up to target
    m1_candles = [
        ForexBar(timestamp=t_start, open=1.08500, high=1.08550, low=1.08480, close=1.08520, volume=50.0),
        ForexBar(timestamp=t_start + timedelta(minutes=30), open=1.08520, high=1.08800, low=1.08500, close=1.08780, volume=70.0),
        ForexBar(timestamp=t_start + timedelta(minutes=60), open=1.08780, high=1.09150, low=1.08750, close=1.09110, volume=90.0),
        ForexBar(timestamp=t_start + timedelta(minutes=90), open=1.09110, high=1.09120, low=1.09050, close=1.09090, volume=60.0),
    ]

    closed_trade = journal.record_trade_close(
        trade_id=trade_id,
        close_price=1.09100,
        exit_reason=TradeExitReason.TAKE_PROFIT,
        notes="Target reached at liquidity pool crest",
    )
    assert closed_trade.status == TradeStatus.CLOSED
    assert closed_trade.pips_gained > 55.0
    assert closed_trade.r_multiple > 1.8

    # -------------------------------------------------------------------------
    # 6. Automatic MFE/MAE Calculation & Post-Trade Reflection
    # -------------------------------------------------------------------------
    reflection = learning_manager.reflect_on_trade(trade=trade_id, candles=m1_candles)

    assert reflection is not None
    assert reflection.trade_id == trade_id
    assert reflection.rating in (ReflectionRating.EXCELLENT, ReflectionRating.GOOD)
    assert len(reflection.lessons) >= 1

    # Verify journal was updated with reflection
    journal_trade_after = journal.get_trade(trade_id)
    assert journal_trade_after is not None
    assert journal_trade_after.reflection is not None
    assert len(journal_trade_after.tags) > 0

    # Verify audit timeline event recorded in journal
    events = journal.get_events(trade_id=trade_id)
    assert any(e["event_type"] == "NOTE_ADDED" and "ForexReflectionAgent" in e["actor"] for e in events)

    # -------------------------------------------------------------------------
    # 7. Lessons Persisted in ForexLessonStore
    # -------------------------------------------------------------------------
    stored_lessons = store.list_lessons(pair="EURUSD")
    assert len(stored_lessons) >= 1
    assert any(lsn.pair == "EURUSD" for lsn in stored_lessons)

    # -------------------------------------------------------------------------
    # 8. Contextual Lesson Retrieval & Prompt Guidance Injection
    # -------------------------------------------------------------------------
    guidance = learning_manager.retrieve_guidance_for_proposal(
        pair="EURUSD",
        setup_type="PULLBACK",
    )
    assert guidance != ""
    assert "### Historical Heuristics & Pitfalls" in guidance
    assert "EURUSD" in guidance


def test_closed_loop_with_premature_exit_learns_runner_discipline(temp_journal_and_learning):
    """Verify that an early exit before MFE generates corrective runner discipline lessons."""
    journal, learning_manager, store = temp_journal_and_learning

    # Proposal
    proposal = ForexTraderProposal(
        pair="GBPUSD",
        action=ForexAction.LONG,
        order_type=OrderType.MARKET,
        setup_type=SetupType.BREAKOUT,
        timeframe="M15",
        entry_price=1.26000,
        stop_loss=1.25600,     # 40 pips risk
        take_profit_1=1.27200, # 120 pips target (3.0R)
        suggested_risk_percent=1.0,
        reasoning="GBPUSD London breakout.",
    )
    prop_id = journal.save_proposal(proposal=proposal, status=ProposalStatus.APPROVED)

    # Manual execution
    trade_id = "trd_gbpusd_premature"
    t0 = datetime.now(timezone.utc) - timedelta(hours=2)
    journal.record_trade_open(
        trade_id=trade_id,
        proposal_id=prop_id,
        pair="GBPUSD",
        action=ForexAction.LONG,
        open_price=1.26000,
        stop_loss=1.25600,
        take_profit=1.27200,
        lots=1.0,
        open_time_utc=t0.isoformat(),
    )

    # Closed prematurely for small profit (+15 pips / +0.375R)
    journal.record_trade_close(
        trade_id=trade_id,
        close_price=1.26150,
        exit_reason=TradeExitReason.MANUAL,
        notes="Closed early out of anxiety",
    )

    # M1 history shows price went straight to 1.27150 (+115 pips / +2.875R MFE)
    candles = [
        ForexBar(timestamp=t0, open=1.26000, high=1.27150, low=1.25950, close=1.26150, volume=100.0),
    ]

    # Reflect
    reflection = learning_manager.reflect_on_trade(trade=trade_id, candles=candles)
    assert reflection.rating == ReflectionRating.POOR
    assert "premature_exit" in reflection.tags
    assert any(lsn.outcome_category == TradeOutcomeCategory.PREMATURE_EXIT.value for lsn in reflection.lessons)

    # Verify retrieval injects the premature exit heuristic for future GBPUSD trades
    guidance = learning_manager.retrieve_guidance_for_proposal(
        pair="GBPUSD",
        setup_type="BREAKOUT",
    )
    assert "Runner Execution Discipline" in guidance or "PREMATURE_EXIT" in guidance or "GBPUSD" in guidance


def test_closed_loop_risk_rejection_blocks_trade(temp_journal_and_learning):
    """Verify that a proposal violating risk bounds is REJECTED and prevented from execution."""
    journal, _, _ = temp_journal_and_learning

    bad_proposal = ForexTraderProposal(
        pair="USDJPY",
        action=ForexAction.LONG,
        order_type=OrderType.MARKET,
        setup_type=SetupType.BREAKOUT,
        timeframe="H1",
        entry_price=150.00,
        stop_loss=148.00,      # 200 pips risk
        take_profit_1=151.00,  # 100 pips reward (R:R = 0.5:1, well below 1.5 threshold)
        suggested_risk_percent=3.0,
        reasoning="Sub-optimal setup violating risk parameters.",
    )

    risk_limits = ForexRiskLimits(min_risk_reward_ratio=1.5, max_risk_percent=1.0)
    risk_engine = ForexRiskEngine(default_limits=risk_limits)
    decision = risk_engine.validate_proposal(bad_proposal)

    assert decision.decision == ForexRiskDecisionAction.REJECT
    assert len(decision.risk_violations) > 0

    prop_id = journal.save_proposal(
        proposal=bad_proposal,
        risk_decision=decision,
        status=ProposalStatus.REJECTED,
    )

    saved = journal.get_proposal(prop_id)
    assert saved is not None
    assert saved.status == ProposalStatus.REJECTED

    # Ensure no trade was created for this rejected proposal
    trades = journal.list_trades(pair="USDJPY")
    assert len(trades) == 0


def test_closed_loop_runaway_loss_generates_critical_lesson(temp_journal_and_learning):
    """Verify that a stop-breach runaway loss produces CRITICAL_ERROR reflection and hard-stop lessons."""
    journal, learning_manager, store = temp_journal_and_learning

    prop = ForexTraderProposal(
        pair="EURUSD",
        action=ForexAction.LONG,
        order_type=OrderType.MARKET,
        setup_type=SetupType.PULLBACK,
        timeframe="H1",
        entry_price=1.0800,
        stop_loss=1.0750,      # 50 pips risk
        take_profit_1=1.0900,
        suggested_risk_percent=1.0,
        reasoning="Long pullback.",
    )
    prop_id = journal.save_proposal(prop, status=ProposalStatus.APPROVED)

    trade_id = "trd_runaway_eurusd"
    journal.record_trade_open(
        trade_id=trade_id,
        proposal_id=prop_id,
        pair="EURUSD",
        action=ForexAction.LONG,
        open_price=1.0800,
        stop_loss=1.0750,
        take_profit=1.0900,
        lots=1.0,
    )

    # Closed at 1.0730 (65 pips loss, breaching intended 50 pip stop loss)
    journal.record_trade_close(
        trade_id=trade_id,
        close_price=1.0735,
        exit_reason=TradeExitReason.STOP_LOSS,
        notes="Stop breached due to late manual intervention",
    )

    t0 = datetime.now(timezone.utc) - timedelta(hours=3)
    candles = [
        ForexBar(timestamp=t0, open=1.0800, high=1.0810, low=1.0735, close=1.0735, volume=120.0),
    ]

    reflection = learning_manager.reflect_on_trade(trade=trade_id, candles=candles)
    assert reflection.rating == ReflectionRating.CRITICAL_ERROR
    assert "stop_breach" in reflection.tags
    assert any("Hard Stop Loss" in (lsn.rule_violated or "") for lsn in reflection.lessons)

    # Lesson is stored in lesson store
    lessons = store.list_lessons(pair="EURUSD")
    assert any("Hard Stop Loss" in (lsn.rule_violated or "") for lsn in lessons)

