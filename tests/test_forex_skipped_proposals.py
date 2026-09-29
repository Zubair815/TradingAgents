"""Unit & Integration Tests for Skipped Proposal Evaluation & Theoretical Simulation (Phase 17)."""

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from tradingagents.agents.schemas_forex import ForexAction, OrderType
from tradingagents.database.journal import ForexTradeJournal
from tradingagents.database.models import (
    OrderExecutionRecord,
    ProposalRecord,
    ProposalStatus,
    TradeJournalRecord,
)
from tradingagents.dataflows.forex_data import ForexBar
from tradingagents.journal.lifecycle import TradeLifecycleManager
from tradingagents.journal.models import LifecycleState
from tradingagents.learning.history_provider import InMemoryTradeHistoryProvider
from tradingagents.metrics.manager import ForexMetricsManager
from tradingagents.metrics.skipped_proposals import (
    ProposalSimulationStatus,
    SkippedProposalEvaluator,
    SkippedProposalSimulation,
    calculate_ai_theoretical_performance,
    calculate_user_execution_performance,
    compare_ai_vs_user_performance,
)
from web.forex_routes import router, set_forex_dependencies

# ===========================================================================
# 1. ProposalStatus & LifecycleState SKIPPED_BY_USER Tests
# ===========================================================================


def test_skipped_by_user_status_and_lifecycle():
    """Verify SKIPPED_BY_USER enum, transitions, and user action recording."""
    assert ProposalStatus.SKIPPED_BY_USER == "SKIPPED_BY_USER"
    assert LifecycleState.SKIPPED_BY_USER == "SKIPPED_BY_USER"
    assert LifecycleState.SKIPPED_BY_USER.is_terminal

    # Verify from_str parsing
    assert ProposalStatus.from_str("SKIPPED_BY_USER") == ProposalStatus.SKIPPED_BY_USER
    assert ProposalStatus.from_str("skipped_by_user") == ProposalStatus.SKIPPED_BY_USER
    assert ProposalStatus.from_str("SKIPPED") == ProposalStatus.SKIPPED

    # Test state machine transition via TradeLifecycleManager
    journal = ForexTradeJournal(db_path=":memory:")
    lifecycle = TradeLifecycleManager(journal=journal)

    prop = ProposalRecord(
        proposal_id="prop_skip_test",
        pair="EURUSD",
        action=ForexAction.LONG,
        status=ProposalStatus.WAITING_USER,
        entry_price=1.0800,
        stop_loss=1.0750,
        take_profit_1=1.0900,
    )
    journal.save_proposal(prop, status=ProposalStatus.WAITING_USER)

    next_status = lifecycle.record_user_action(
        proposal_id="prop_skip_test",
        action="SKIPPED_BY_USER",
        reason="Discretionary skip: high impact news pending",
    )
    assert next_status == ProposalStatus.SKIPPED_BY_USER
    updated = journal.get_proposal("prop_skip_test")
    assert updated.status == ProposalStatus.SKIPPED_BY_USER


# ===========================================================================
# 2. Counterfactual Simulation Scenarios
# ===========================================================================


def test_simulation_market_order_hits_tp1():
    """Verify counterfactual simulation of a LONG proposal reaching TP1."""
    t0 = datetime(2026, 3, 10, 10, 0, tzinfo=timezone.utc)
    candles = [
        ForexBar(timestamp=t0, open=1.0800, high=1.0830, low=1.0790, close=1.0825, volume=100.0),
        ForexBar(timestamp=t0 + timedelta(hours=1), open=1.0825, high=1.0920, low=1.0815, close=1.0910, volume=120.0),
    ]

    proposal = ProposalRecord(
        proposal_id="prop_buy_tp1",
        pair="EURUSD",
        action=ForexAction.LONG,
        order_type=OrderType.MARKET,
        entry_price=1.0800,
        stop_loss=1.0750,
        take_profit_1=1.0900,
    )

    evaluator = SkippedProposalEvaluator()
    sim = evaluator.evaluate(proposal=proposal, candles=candles)

    assert sim.entry_triggered is True
    assert sim.status == ProposalSimulationStatus.HIT_TP1
    assert sim.hit_tp1 is True
    assert sim.hit_sl is False
    assert sim.exit_price == 1.0900
    assert sim.theoretical_r == 2.0  # 100 pips target / 50 pips risk
    assert sim.mfe_pips == 120.0  # Peak high 1.0920 - 1.0800
    assert sim.mae_pips == 10.0  # Deepest low 1.0800 - 1.0790
    assert sim.mfe_r == 2.4
    assert sim.mae_r == 0.2


def test_simulation_limit_order_expired_not_triggered():
    """Verify counterfactual simulation when entry price is never reached."""
    t0 = datetime(2026, 3, 10, 10, 0, tzinfo=timezone.utc)
    candles = [
        ForexBar(timestamp=t0, open=1.0820, high=1.0850, low=1.0810, close=1.0840, volume=100.0),
        ForexBar(timestamp=t0 + timedelta(hours=1), open=1.0840, high=1.0860, low=1.0820, close=1.0850, volume=100.0),
    ]

    # Limit order below market price at 1.0750
    proposal = ProposalRecord(
        proposal_id="prop_untriggered",
        pair="EURUSD",
        action=ForexAction.LONG,
        order_type=OrderType.BUY_LIMIT,
        entry_price=1.0750,
        stop_loss=1.0700,
        take_profit_1=1.0850,
    )

    evaluator = SkippedProposalEvaluator()
    sim = evaluator.evaluate(proposal=proposal, candles=candles)

    assert sim.entry_triggered is False
    assert sim.status == ProposalSimulationStatus.EXPIRED_NOT_TRIGGERED
    assert sim.theoretical_r == 0.0
    assert sim.mfe_pips == 0.0
    assert sim.mae_pips == 0.0


def test_simulation_hits_sl():
    """Verify counterfactual simulation when position triggers and hits SL."""
    t0 = datetime(2026, 3, 10, 10, 0, tzinfo=timezone.utc)
    candles = [
        ForexBar(timestamp=t0, open=1.0800, high=1.0810, low=1.0780, close=1.0785, volume=100.0),
        ForexBar(timestamp=t0 + timedelta(hours=1), open=1.0785, high=1.0790, low=1.0740, close=1.0745, volume=150.0),
    ]

    proposal = ProposalRecord(
        proposal_id="prop_sl",
        pair="EURUSD",
        action=ForexAction.LONG,
        order_type=OrderType.MARKET,
        entry_price=1.0800,
        stop_loss=1.0750,
        take_profit_1=1.0900,
    )

    evaluator = SkippedProposalEvaluator()
    sim = evaluator.evaluate(proposal=proposal, candles=candles)

    assert sim.entry_triggered is True
    assert sim.status == ProposalSimulationStatus.HIT_SL
    assert sim.hit_sl is True
    assert sim.hit_tp1 is False
    assert sim.exit_price == 1.0750
    assert sim.theoretical_r == -1.0
    assert sim.mae_pips == 60.0  # 1.0800 - 1.0740


# ===========================================================================
# 3. Same-Candle Collision & Multi-Resolution Ambiguity Resolution Tests
# ===========================================================================


def test_same_candle_collision_resolved_to_tp_via_subresolution():
    """When parent candle hits both SL and TP, M1 sub-bars prove TP was hit first."""
    t0 = datetime(2026, 3, 10, 10, 0, tzinfo=timezone.utc)
    # Parent candle swings from 1.0740 (below SL 1.0750) to 1.0920 (above TP 1.0900)
    parent_candle = ForexBar(
        timestamp=t0, open=1.0800, high=1.0920, low=1.0740, close=1.0850, volume=500.0
    )

    # Sub-resolution M1 bars: rallies to TP first, then collapses
    sub_bars = [
        ForexBar(timestamp=t0, open=1.0800, high=1.0920, low=1.0800, close=1.0910, volume=250.0),
        ForexBar(timestamp=t0 + timedelta(minutes=5), open=1.0910, high=1.0915, low=1.0740, close=1.0750, volume=250.0),
    ]

    proposal = ProposalRecord(
        proposal_id="prop_collision_tp",
        pair="EURUSD",
        action=ForexAction.LONG,
        order_type=OrderType.MARKET,
        entry_price=1.0800,
        stop_loss=1.0750,
        take_profit_1=1.0900,
    )

    evaluator = SkippedProposalEvaluator()
    sim = evaluator.evaluate(
        proposal=proposal,
        candles=[parent_candle],
        sub_resolution_candles=sub_bars,
    )

    assert sim.sub_resolution_checked is True
    assert sim.ambiguity_resolved is True
    assert sim.is_ambiguous is False
    assert sim.status == ProposalSimulationStatus.HIT_TP1
    assert sim.exit_reason == "TAKE_PROFIT_1"
    assert sim.theoretical_r == 2.0


def test_same_candle_collision_resolved_to_sl_via_subresolution():
    """When parent candle hits both SL and TP, M1 sub-bars prove SL was hit first."""
    t0 = datetime(2026, 3, 10, 10, 0, tzinfo=timezone.utc)
    # Parent candle swings from 1.0740 to 1.0920
    parent_candle = ForexBar(
        timestamp=t0, open=1.0800, high=1.0920, low=1.0740, close=1.0880, volume=500.0
    )

    # Sub-resolution M1 bars: drops to SL first, then rallies
    sub_bars = [
        ForexBar(timestamp=t0, open=1.0800, high=1.0805, low=1.0740, close=1.0745, volume=250.0),
        ForexBar(timestamp=t0 + timedelta(minutes=5), open=1.0745, high=1.0920, low=1.0745, close=1.0880, volume=250.0),
    ]

    proposal = ProposalRecord(
        proposal_id="prop_collision_sl",
        pair="EURUSD",
        action=ForexAction.LONG,
        order_type=OrderType.MARKET,
        entry_price=1.0800,
        stop_loss=1.0750,
        take_profit_1=1.0900,
    )

    evaluator = SkippedProposalEvaluator()
    sim = evaluator.evaluate(
        proposal=proposal,
        candles=[parent_candle],
        sub_resolution_candles=sub_bars,
    )

    assert sim.sub_resolution_checked is True
    assert sim.ambiguity_resolved is True
    assert sim.is_ambiguous is False
    assert sim.status == ProposalSimulationStatus.HIT_SL
    assert sim.exit_reason == "STOP_LOSS"
    assert sim.theoretical_r == -1.0


def test_same_candle_collision_unresolved_is_ambiguous_never_conveniently_profitable():
    """When both SL and TP hit in same candle and sub-resolution cannot resolve, mark AMBIGUOUS."""
    t0 = datetime(2026, 3, 10, 10, 0, tzinfo=timezone.utc)
    # Parent candle hits both levels
    parent_candle = ForexBar(
        timestamp=t0, open=1.0800, high=1.0920, low=1.0740, close=1.0850, volume=500.0
    )

    proposal = ProposalRecord(
        proposal_id="prop_ambiguous",
        pair="EURUSD",
        action=ForexAction.LONG,
        order_type=OrderType.MARKET,
        entry_price=1.0800,
        stop_loss=1.0750,
        take_profit_1=1.0900,
    )

    # No sub-resolution data provided
    evaluator = SkippedProposalEvaluator()
    sim = evaluator.evaluate(proposal=proposal, candles=[parent_candle])

    assert sim.sub_resolution_checked is True
    assert sim.ambiguity_resolved is False
    assert sim.is_ambiguous is True
    assert sim.status == ProposalSimulationStatus.AMBIGUOUS
    assert sim.exit_reason == "AMBIGUOUS"
    # Never conveniently assume profit!
    assert sim.theoretical_r is None
    assert sim.hit_sl is False
    assert sim.hit_tp1 is False


# ===========================================================================
# 4. Separate AI Theoretical vs Actual User Execution Performance
# ===========================================================================


def test_separate_performance_metrics_and_user_skip_alpha():
    """Verify distinct metrics calculation for AI theoretical vs user actual and skip alpha."""
    proposals = [
        ProposalRecord(
            proposal_id="p1",
            pair="EURUSD",
            action=ForexAction.LONG,
            status=ProposalStatus.SKIPPED_BY_USER,
            entry_price=1.0800,
            stop_loss=1.0750,
            take_profit_1=1.0900,
        ),
        ProposalRecord(
            proposal_id="p2",
            pair="GBPUSD",
            action=ForexAction.LONG,
            status=ProposalStatus.SKIPPED_BY_USER,
            entry_price=1.2500,
            stop_loss=1.2450,
            take_profit_1=1.2600,
        ),
        ProposalRecord(
            proposal_id="p3",
            pair="USDJPY",
            action=ForexAction.SHORT,
            status=ProposalStatus.SKIPPED_BY_USER,
            entry_price=150.00,
            stop_loss=150.50,
            take_profit_1=149.00,
        ),
    ]

    # Counterfactual outcomes:
    # p1: Hit SL (-1.0R) -> Skipped, so User avoided 1 loss! (+1R alpha saved)
    # p2: Hit SL (-1.0R) -> Skipped, so User avoided another loss! (+1R alpha saved)
    # p3: Hit TP (+2.0R) -> Skipped, so User missed a winner (-2R lost opportunity)
    simulations = [
        SkippedProposalSimulation(
            proposal_id="p1",
            pair="EURUSD",
            action=ForexAction.LONG,
            status=ProposalSimulationStatus.HIT_SL,
            entry_triggered=True,
            entry_price=1.0800,
            hit_sl=True,
            theoretical_r=-1.0,
        ),
        SkippedProposalSimulation(
            proposal_id="p2",
            pair="GBPUSD",
            action=ForexAction.LONG,
            status=ProposalSimulationStatus.HIT_SL,
            entry_triggered=True,
            entry_price=1.2500,
            hit_sl=True,
            theoretical_r=-1.0,
        ),
        SkippedProposalSimulation(
            proposal_id="p3",
            pair="USDJPY",
            action=ForexAction.SHORT,
            status=ProposalSimulationStatus.HIT_TP1,
            entry_triggered=True,
            entry_price=150.00,
            hit_tp1=True,
            theoretical_r=2.0,
        ),
    ]

    # AI theoretical calculation
    ai_metrics = calculate_ai_theoretical_performance(proposals=proposals, simulations=simulations)
    assert ai_metrics.total_proposals == 3
    assert ai_metrics.win_count == 1
    assert ai_metrics.loss_count == 2
    assert ai_metrics.win_rate_pct == 33.3
    assert ai_metrics.total_theoretical_r == 0.0  # -1 + -1 + 2 = 0.0

    # Human user executed trades (different trades that user took)
    user_trades = [
        TradeJournalRecord(
            trade_id="t1",
            pair="EURUSD",
            action=ForexAction.LONG,
            lots=0.1,
            open_price=1.0800,
            close_price=1.0890,
            stop_loss=1.0750,
            realized_r=1.8,
            gross_profit=180.0,
        ),
        TradeJournalRecord(
            trade_id="t2",
            pair="GBPUSD",
            action=ForexAction.LONG,
            lots=0.1,
            open_price=1.2500,
            close_price=1.2460,
            stop_loss=1.2450,
            realized_r=-0.8,
            gross_profit=-80.0,
        ),
    ]

    executions = [
        OrderExecutionRecord(
            deal_id="d1",
            trade_id="t1",
            pair="EURUSD",
            action=ForexAction.LONG,
            order_type=OrderType.MARKET,
            volume=0.1,
            price=1.0801,
            requested_price=1.0800,
            fill_price=1.0801,
            slippage_pips=1.0,
            slippage_cost=10.0,
            spread_cost=12.0,
        )
    ]

    user_metrics = calculate_user_execution_performance(trades=user_trades, executions=executions)
    assert user_metrics.total_executed_trades == 2
    assert user_metrics.win_count == 1
    assert user_metrics.loss_count == 1
    assert user_metrics.win_rate_pct == 50.0
    assert user_metrics.total_realized_r == 1.0
    assert user_metrics.avg_realized_r == 0.5
    assert user_metrics.avg_slippage_pips == 1.0

    # Comparative benchmarking
    comp = compare_ai_vs_user_performance(
        proposals=proposals,
        simulations=simulations,
        trades=user_trades,
        executions=executions,
    )

    assert comp.skipped_avoided_losses == 2
    assert comp.skipped_missed_winners == 1
    # User Skip Alpha: avoided 2 losses (+2R) - missed 1 winner (-2R) = 0.0R net
    assert comp.user_skip_alpha_r == 0.0
    assert comp.skip_efficiency_pct == 66.7


# ===========================================================================
# 5. ForexMetricsManager & Web API Integration Tests
# ===========================================================================


def test_metrics_manager_integration():
    """Verify ForexMetricsManager evaluate_proposal and comparative report generation."""
    journal = ForexTradeJournal(db_path=":memory:")
    metrics_mgr = ForexMetricsManager(journal=journal)

    prop = ProposalRecord(
        proposal_id="prop_mgr_test",
        pair="EURUSD",
        action=ForexAction.LONG,
        status=ProposalStatus.SKIPPED_BY_USER,
        entry_price=1.0800,
        stop_loss=1.0750,
        take_profit_1=1.0900,
    )
    journal.save_proposal(prop, status=ProposalStatus.SKIPPED_BY_USER)

    t0 = datetime(2026, 3, 10, 10, 0, tzinfo=timezone.utc)
    candles = [
        ForexBar(timestamp=t0, open=1.0800, high=1.0910, low=1.0790, close=1.0905, volume=100.0)
    ]

    sim = metrics_mgr.evaluate_proposal(proposal="prop_mgr_test", candles=candles)
    assert sim.status == ProposalSimulationStatus.HIT_TP1

    # In-memory history provider evaluation
    provider = InMemoryTradeHistoryProvider(
        candle_map={"EURUSD": candles, "EURUSD_H1": candles}
    )
    sims = metrics_mgr.evaluate_skipped_proposals(history_provider=provider)
    assert len(sims) == 1
    assert sims[0].status == ProposalSimulationStatus.HIT_TP1

    # Comparative performance summary
    comp = metrics_mgr.get_comparative_performance(simulations=sims)
    assert comp.skipped_proposals_count == 1
    assert comp.skipped_missed_winners == 1

    # Markdown dashboard with comparative table
    summary = metrics_mgr.analyze_portfolio(trades=[])
    md = metrics_mgr.render_markdown_dashboard(summary=summary, comparative=comp)
    assert "## 4. AI Theoretical Edge vs Actual Human Discretion" in md
    assert "User Skip Alpha" in md


def test_web_routes_skipped_metrics_endpoints():
    """Verify FastAPI routes for skipped proposals and comparative metrics."""
    journal = ForexTradeJournal(db_path=":memory:")
    metrics_mgr = ForexMetricsManager(journal=journal)
    set_forex_dependencies(journal=journal, metrics_manager=metrics_mgr)

    prop = ProposalRecord(
        proposal_id="prop_route_test",
        pair="EURUSD",
        action=ForexAction.LONG,
        status=ProposalStatus.SKIPPED_BY_USER,
        entry_price=1.0800,
        stop_loss=1.0750,
        take_profit_1=1.0900,
    )
    journal.save_proposal(prop, status=ProposalStatus.SKIPPED_BY_USER)

    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)
    from web.server import _SESSION_TOKEN, DASHBOARD_API_KEY
    client = TestClient(app, headers={"X-API-Key": DASHBOARD_API_KEY or _SESSION_TOKEN})

    # 1. GET /api/forex/metrics/skipped
    res_list = client.get("/api/forex/metrics/skipped")
    assert res_list.status_code == 200
    data = res_list.json()
    assert data["count"] == 1
    assert data["skipped_proposals"][0]["proposal_id"] == "prop_route_test"

    # 2. GET /api/forex/metrics/comparative
    res_comp = client.get("/api/forex/metrics/comparative")
    assert res_comp.status_code == 200
    comp_data = res_comp.json()["comparative_performance"]
    assert "ai_theoretical" in comp_data
    assert "user_execution" in comp_data
    assert "user_skip_alpha_r" in comp_data
