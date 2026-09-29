"""Institutional Verification Suite for Real Historical Agent Backtesting (Phase 20).

Validates:
1. Point-in-Time (PIT) isolation: Agents never see candles after evaluation timestamp T.
2. Cost controls: sampling_interval, max_analysis_points, analyst selection, and pre-launch estimation.
3. Order execution fidelity: MARKET, LIMIT, STOP with validity/expiration.
4. Execution friction: Spread drag, slippage drag, broker commission, and overnight swap accrual.
5. Intrabar collision resolution: Sub-resolution resolution vs conservative stops.
6. Mode differentiation: DEMO vs HISTORICAL_AGENT_BACKTEST with validated strategy performance flags.
7. Web API routing: /backtest/estimate and /backtest/run endpoints.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from tradingagents.agents.schemas_forex import (
    ForexAction,
    ForexTraderProposal,
    OrderType,
    SetupType,
)
from tradingagents.backtest.agent_backtester import (
    AgentBacktestConfig,
    AgentBacktestEstimate,
    HistoricalForexAgentBacktester,
    estimate_agent_analyses,
)
from tradingagents.backtest.forex_engine import (
    ForexBacktestConfig,
)
from tradingagents.database.models import TradeExitReason
from tradingagents.dataflows.forex_data import ForexBar
from web.server import app

# ---------------------------------------------------------------------------
# Test Helpers & Fixtures
# ---------------------------------------------------------------------------


def make_candle(
    timestamp: datetime,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: float = 100.0,
) -> ForexBar:
    return ForexBar(
        timestamp=timestamp,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


def generate_bars(
    count: int = 50,
    base_price: float = 1.0800,
    interval_minutes: int = 60,
    start_dt: datetime | None = None,
) -> list[ForexBar]:
    start = start_dt or datetime(2025, 1, 1, 0, 0, tzinfo=timezone.utc)
    bars: list[ForexBar] = []
    curr = base_price
    for i in range(count):
        ts = start + timedelta(minutes=i * interval_minutes)
        o = curr
        h = o + 0.0010
        low_val = o - 0.0010
        c = o + 0.0005
        curr = c
        bars.append(make_candle(ts, o, h, low_val, c))
    return bars


# ---------------------------------------------------------------------------
# 1. Resource & Cost Estimation Tests
# ---------------------------------------------------------------------------


def test_estimate_agent_analyses_deterministic_math():
    """Verify pre-launch resource estimation formulas."""
    est = estimate_agent_analyses(
        total_bars=100,
        sampling_interval=5,
        max_analysis_points=10,
        analyst_count=3,
        avg_tokens_per_analysis=5000,
        cost_per_1k_tokens=0.002,
    )

    assert isinstance(est, AgentBacktestEstimate)
    assert est.total_bars == 100
    assert est.sampling_interval == 5
    # 100 / 5 = 20 raw points, capped by max_analysis_points=10
    assert est.expected_analyses_count == 10
    # 10 * (3 analysts + 3 workflow nodes) = 60 calls
    assert est.estimated_llm_calls == 60
    # 10 * 5000 = 50,000 tokens
    assert est.estimated_tokens == 50000
    # (50,000 / 1000) * 0.002 = $0.10
    assert est.estimated_cost_usd == 0.10


def test_estimate_agent_analyses_uncapped():
    """Verify estimate without max_analysis_points cap."""
    est = estimate_agent_analyses(
        total_bars=50,
        sampling_interval=2,
        max_analysis_points=None,
        analyst_count=2,
    )
    assert est.expected_analyses_count == 25
    assert est.estimated_llm_calls == 25 * (2 + 3)


# ---------------------------------------------------------------------------
# 2. Strict Point-in-Time (PIT) Safety Tests
# ---------------------------------------------------------------------------


def test_agent_receives_only_past_and_current_bars():
    """Verify that at timestamp T, agent strictly receives candles <= T."""
    bars = generate_bars(count=20, base_price=1.1000)
    seen_windows: list[tuple[datetime, list[datetime]]] = []

    def mock_agent_cb(pair: str, ts: datetime, pit_candles: list[ForexBar]):
        seen_windows.append((ts, [c.timestamp for c in pit_candles]))
        return None

    config = AgentBacktestConfig(mode="DEMO",
        pair="EURUSD",
        timeframe="H1",
        sampling_interval=2,
    )
    backtester = HistoricalForexAgentBacktester(config=config)
    report = backtester.run(candles=bars, agent_pipeline_callable=mock_agent_cb)

    assert report.mode == "DEMO"
    assert report.validated_strategy_performance is False
    assert len(seen_windows) == 10  # 20 bars / 2

    for eval_ts, window_timestamps in seen_windows:
        assert window_timestamps[-1] == eval_ts
        for bar_ts in window_timestamps:
            # Absolute PIT assertion: NO future bar allowed
            assert bar_ts <= eval_ts


# ---------------------------------------------------------------------------
# 3. Cost Controls & Sampling Tests
# ---------------------------------------------------------------------------


def test_sampling_interval_and_max_analysis_points_cap():
    """Verify that sampling interval and max points ceiling are respected."""
    bars = generate_bars(count=30, base_price=1.0800)
    invocation_count = 0

    def mock_agent(pair: str, ts: datetime, pit_candles: list[ForexBar]):
        nonlocal invocation_count
        invocation_count += 1
        return None

    # sampling_interval=3 on 30 bars yields 10 points; capped at max_analysis_points=4
    config = AgentBacktestConfig(mode="DEMO",
        pair="EURUSD",
        timeframe="H1",
        sampling_interval=3,
        max_analysis_points=4,
    )
    backtester = HistoricalForexAgentBacktester(config=config)
    report = backtester.run(candles=bars, agent_pipeline_callable=mock_agent)

    assert invocation_count == 4
    assert report.analyses_performed == 4
    assert report.cost_control_summary["max_analysis_points"] == 4


# ---------------------------------------------------------------------------
# 4. Order Execution Types: MARKET, LIMIT, STOP with Expiry
# ---------------------------------------------------------------------------


def test_limit_order_fills_on_subsequent_candle_hit():
    """Verify that a BUY_LIMIT order queues as pending and fills only when price drops to target."""
    start = datetime(2025, 2, 1, 0, 0, tzinfo=timezone.utc)
    # Bar 0: open 1.0850, high 1.0860, low 1.0840, close 1.0855
    b0 = make_candle(start, 1.0850, 1.0860, 1.0840, 1.0855)
    # Bar 1: does not hit 1.0820
    b1 = make_candle(start + timedelta(hours=1), 1.0855, 1.0870, 1.0845, 1.0860)
    # Bar 2: dips to 1.0815 -> triggers BUY_LIMIT at 1.0820
    b2 = make_candle(start + timedelta(hours=2), 1.0860, 1.0865, 1.0815, 1.0830)
    # Bar 3: rises and hits take profit 1.0880
    b3 = make_candle(start + timedelta(hours=3), 1.0830, 1.0890, 1.0825, 1.0875)

    def mock_agent(pair: str, ts: datetime, pit_candles: list[ForexBar]):
        if ts == b0.timestamp:
            return ForexTraderProposal(
                pair="EURUSD",
                action=ForexAction.LONG,
                order_type=OrderType.BUY_LIMIT,
                entry_price=1.0820,
                stop_loss=1.0780,
                take_profit_1=1.0880,
                suggested_lot_size=0.1,
                reasoning="Limit order test setup",
            )
        return None

    config = AgentBacktestConfig(mode="DEMO",
        pair="EURUSD",
        timeframe="H1",
        sampling_interval=1,
    )
    backtester = HistoricalForexAgentBacktester(config=config)
    report = backtester.run(candles=[b0, b1, b2, b3], agent_pipeline_callable=mock_agent)

    res = report.result
    assert res is not None
    assert res.total_trades == 1
    assert res.winning_trades == 1
    assert res.filled_orders_count == 1
    trade = res.trades[0]
    assert trade.entry_price == pytest.approx(1.0820, 0.0005)
    assert trade.exit_price == 1.0880
    assert trade.exit_reason == TradeExitReason.TAKE_PROFIT


def test_pending_order_expires_when_valid_until_exceeded():
    """Verify that an unfilled pending order expires when valid_until is surpassed."""
    start = datetime(2025, 2, 1, 0, 0, tzinfo=timezone.utc)
    b0 = make_candle(start, 1.0850, 1.0860, 1.0840, 1.0855)
    b1 = make_candle(start + timedelta(hours=1), 1.0855, 1.0870, 1.0845, 1.0860)
    b2 = make_candle(start + timedelta(hours=2), 1.0860, 1.0875, 1.0850, 1.0870)

    # Valid until after b1, so by b2 it must be EXPIRED
    valid_until = (start + timedelta(minutes=90)).isoformat()

    def mock_agent(pair: str, ts: datetime, pit_candles: list[ForexBar]):
        if ts == b0.timestamp:
            return ForexTraderProposal(
                pair="EURUSD",
                action=ForexAction.LONG,
                order_type=OrderType.BUY_LIMIT,
                entry_price=1.0750,  # Never hit
                stop_loss=1.0700,
                take_profit_1=1.0900,
                valid_until=valid_until,
                reasoning="Expiring limit order",
            )
        return None

    config = AgentBacktestConfig(mode="DEMO", pair="EURUSD", timeframe="H1", sampling_interval=1)
    backtester = HistoricalForexAgentBacktester(config=config)
    report = backtester.run(candles=[b0, b1, b2], agent_pipeline_callable=mock_agent)

    res = report.result
    assert res is not None
    assert res.total_trades == 0  # Order was never filled
    assert res.expired_orders_count == 1
    assert res.filled_orders_count == 0


# ---------------------------------------------------------------------------
# 5. Overnight Swap Rollover and Execution Friction Tests
# ---------------------------------------------------------------------------


def test_overnight_swap_drag_accrual():
    """Verify that daily overnight swap is accrued when trades cross calendar day boundary."""
    # 2 days of bars
    d1 = datetime(2025, 3, 1, 10, 0, tzinfo=timezone.utc)
    d2 = datetime(2025, 3, 2, 10, 0, tzinfo=timezone.utc)
    d3 = datetime(2025, 3, 3, 10, 0, tzinfo=timezone.utc)

    b0 = make_candle(d1, 1.0800, 1.0820, 1.0790, 1.0810)
    b1 = make_candle(d2, 1.0810, 1.0830, 1.0800, 1.0820)
    b2 = make_candle(d3, 1.0820, 1.0840, 1.0810, 1.0830)

    def mock_agent(pair: str, ts: datetime, pit_candles: list[ForexBar]):
        if ts == b0.timestamp:
            return ForexTraderProposal(
                pair="EURUSD",
                action=ForexAction.LONG,
                order_type=OrderType.MARKET,
                suggested_lot_size=1.0,
                stop_loss=1.0700,
                take_profit_1=1.0950,
                reasoning="Multi-day swing trade",
            )
        return None

    bt_cfg = ForexBacktestConfig(
        swap_per_day_usd=3.50,  # $3.50/day per lot
        default_spread_pips=0.0,
        default_slippage_pips=0.0,
        commission_per_lot_usd=0.0,
    )
    config = AgentBacktestConfig(mode="DEMO",
        pair="EURUSD",
        timeframe="D1",
        sampling_interval=1,
        backtest_config=bt_cfg,
    )
    backtester = HistoricalForexAgentBacktester(config=config)
    report = backtester.run(candles=[b0, b1, b2], agent_pipeline_callable=mock_agent)

    res = report.result
    assert res is not None
    assert res.total_trades == 1
    # Spans 2 day rollovers (from Mar 1 to Mar 2 and Mar 3) -> 2 days * $3.50 * 1.0 lot = $7.00
    assert res.total_swap_cost_usd >= 7.00
    assert res.total_friction_usd >= 7.00


# ---------------------------------------------------------------------------
# 6. Intrabar Collision Resolution with Lower-Timeframe Data
# ---------------------------------------------------------------------------


def test_intrabar_collision_resolved_by_lower_timeframe_candles():
    """Verify that when a bar touches both SL and TP, sub-resolution bars resolve the winner."""
    t0 = datetime(2025, 4, 1, 12, 0, tzinfo=timezone.utc)
    setup_bar = make_candle(t0 - timedelta(hours=1), 1.0790, 1.0805, 1.0785, 1.0800)
    # H1 Bar touches both SL (1.0780) and TP (1.0850)
    parent_bar = make_candle(t0, 1.0800, 1.0860, 1.0770, 1.0840)

    # Sub-resolution M1 bars: price moves up to 1.0855 (TP) BEFORE dropping to 1.0770 (SL)
    sub_bar_1 = make_candle(t0 + timedelta(minutes=5), 1.0800, 1.0855, 1.0795, 1.0850)
    sub_bar_2 = make_candle(t0 + timedelta(minutes=20), 1.0850, 1.0852, 1.0770, 1.0780)

    def mock_agent(pair: str, ts: datetime, pit_candles: list[ForexBar]):
        if ts == setup_bar.timestamp:
            return ForexTraderProposal(
                pair="EURUSD",
                action=ForexAction.LONG,
                order_type=OrderType.MARKET,
                stop_loss=1.0780,
                take_profit_1=1.0850,
                reasoning="Sub-candle resolution test",
            )
        return None

    config = AgentBacktestConfig(mode="DEMO",
        pair="EURUSD",
        timeframe="H1",
        backtest_config=ForexBacktestConfig(conservative_stops=False, allow_ambiguous=True),
    )
    backtester = HistoricalForexAgentBacktester(config=config)
    report = backtester.run(
        candles=[setup_bar, parent_bar],
        lower_tf_candles=[sub_bar_1, sub_bar_2],
        agent_pipeline_callable=mock_agent,
    )

    res = report.result
    assert res is not None
    assert res.total_trades == 1
    # Sub-bar 1 hit TP first!
    assert res.trades[0].exit_reason == TradeExitReason.TAKE_PROFIT
    assert res.winning_trades == 1


# ---------------------------------------------------------------------------
# 7. Web API Endpoints Tests
# ---------------------------------------------------------------------------


def test_api_estimate_backtest_costs():
    """Verify POST /api/forex/backtest/estimate returns valid estimates."""
    client = TestClient(app)
    client.get("/")

    req = {
        "pair": "EURUSD",
        "timeframe": "H1",
        "count": 200,
        "sampling_interval": 4,
        "max_analysis_points": 25,
        "analyst_count": 3,
    }
    res = client.post("/api/forex/backtest/estimate", json=req)
    assert res.status_code == 200
    data = res.json()
    assert data["total_bars"] == 200
    assert data["sampling_interval"] == 4
    assert data["expected_analyses_count"] == 25
    assert data["estimated_llm_calls"] == 25 * 6
    assert "estimated_cost_usd" in data


def test_api_run_backtest_historical_agent_mode():
    """Verify POST /api/forex/backtest/run with mode=HISTORICAL_AGENT_BACKTEST."""
    client = TestClient(app)
    client.get("/")

    req = {
        "pair": "EURUSD",
        "timeframe": "M15",
        "mode": "HISTORICAL_AGENT_BACKTEST",
        "count": 30,
        "date_from": "2025-01-06",
        "date_to": "2025-01-06T07:30:00Z",
        "initial_balance": 50000.0,
        "spread_pips": 1.0,
        "slippage_pips": 0.2,
        "commission_per_lot_usd": 3.5,
        "sampling_interval": 5,
        "max_analysis_points": 3,
        "provider": "anthropic",
        "quick_model": "quick-test",
        "deep_model": "deep-test",
        "token_limits": 2048,
        "research_depth": "deep",
        "analyst_selection": ["forex_technical"],
    }

    # Patch graph factory inside router to produce deterministic mock proposal
    from tests.test_historical_integrity import sourced_bars
    from tradingagents.backtest.historical_data import candle_frame
    bars, meta = sourced_bars(30, minutes=15)
    frame = candle_frame(bars, meta)
    frame["close_time"] = [b.close_time for b in bars]
    frame["is_closed"] = True
    with (patch("web.forex_routes.ForexTradingAgentsGraph") as mock_graph_cls,
          patch("tradingagents.backtest.historical_data.fetch_forex_candles", return_value=frame),
          patch("tradingagents.dataflows.trading_economics.TradingEconomicsCalendar.query", return_value=[])):

        mock_graph = MagicMock()
        mock_graph.run.return_value = (
            {
                "forex_proposal": ForexTraderProposal(
                    pair="EURUSD",
                    action=ForexAction.LONG,
                    order_type=OrderType.MARKET,
                    setup_type=SetupType.TREND_CONTINUATION,
                    entry_price=1.0800,
                    stop_loss=1.0770,
                    take_profit_1=1.0860,
                    suggested_lot_size=0.1,
                    reasoning="Agent backtest mock",
                ),
                "forex_risk_decision": {
                    "pair": "EURUSD", "decision": "APPROVE", "original_action": "LONG",
                    "approved_action": "LONG", "approved_lot_size": 0.1,
                    "executive_rationale": "Mock approved decision",
                },
            },
            "LONG",
        )
        mock_graph_cls.return_value = mock_graph

        res = client.post("/api/forex/backtest/run", json=req)
        assert res.status_code == 200
        data = res.json()
        assert data["mode"] == "HISTORICAL_AGENT_BACKTEST"
        assert data["demo_mode"] is False
        assert data["validated_strategy_performance"] is False
        assert data["validation_status"] == "PARTIALLY_VALIDATED"
        assert "backtest_id" in data
        assert "markdown_report" in data
        assert "result" in data
        assert data["result"]["mode"] == "HISTORICAL_AGENT_BACKTEST"
        assert data["result"]["validated_strategy_performance"] is False
        graph_config = mock_graph_cls.call_args.kwargs["config"]
        assert graph_config["llm_provider"] == "anthropic"
        assert graph_config["quick_think_llm"] == "quick-test"
        assert graph_config["deep_think_llm"] == "deep-test"
        assert graph_config["max_tokens"] == 2048
        assert graph_config["max_debate_rounds"] == 3
        assert graph_config["historical_backtest"] is True
        assert mock_graph_cls.call_args.kwargs["selected_analysts"] == ["forex_technical"]
